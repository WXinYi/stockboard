"""
股票数据看板 - 数据采集模块

功能：
- 异步数据获取
- SQLite 高效存储
- 增量更新 + 断点续传
- 批量写入优化
"""
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple, Optional

from src.spiders.player_list import PlayerListSpider
from src.spiders.api_detail import crawl_player_all_data_async
from src.storage.storage_factory import get_storage
from src.utils.logger import setup_logger

logger = setup_logger()

# 检查点文件
CHECKPOINT_FILE = Path(__file__).parent / "data" / "checkpoint.json"

# 批量保存配置
BATCH_SIZE = 50

# 关注选手（唯一数据源）：始终重新抓取（不跳过检查点），并保证在采集列表最前。
# 顺序 = 置顶顺序。全部选手一个组, 不再分子分组(2026-08-30)。
# 2026-09-05 淘汰 4 家(依据: 长期成绩+龙头战法回测+近10日操作三口径一致差):
#   900401128 半路的第n天(总-13.7%, 近10日0胜6负) / 900013608 wxm1988蒙(年-29.5%, 组合08-30起已隐藏)
#   900369020 年年纳福地风清扬→奉天奉天(总-50%, 回撤84%, 已停手) / 900315547 西门星辰啊(292天仅+5.3%, 近10日胜率27%)
# 2026-10-23 取消 1 家: 900439290 兴奋之青果→再也不硬抗了→我要再来一次(组合 10-15 起东财侧隐藏,
#   数据不可得; 恢复公开后如需继续跟踪可重新加入)
# 2026-10-28 移除 1 家: 900441708 林子木了(10-23 满仓买海洋王后组合在东财侧删除, v2 detail 明示
#   "该组合已删除", 删号不会回归; 最后调仓记录仅存库内至 10-23)
# 2026-11-07 移除 2 家: 900407488 凡人炒股 / 900237280 慢慢做趋势(用户拍板"全部移除, 不推送不关注";
#   近5日每股口径 凡人 -0.7%/笔逆势补上海九百, 慢慢做趋势 +8.6%/笔打法转型——移除与近期盈亏无关)
# 2026-11-11 移除 1 家: 900450475 敢作敢当的武研琳(组合在东财侧被删除 code=9061, 新组合未公开无法恢复;
#   userId 与 900439290"再也不硬抗了"不同账号, 勿混; 历史 players/900450475.json 保留可查)
WATCHED_PLAYERS = [
    ("900456476", "狼之行一"),         # 满仓单票每日龙头接力(9天新号, 观察验证中; 同名老号 900298267 勿混)
    ("900351276", "新生代柚子04"),      # 高频+龙头兼顾, 年+149%
    ("900240956", "股得猫咛"),
    ("900438148", "我嘚财富"),          # ⚠️ 组合 08-30 起东财侧隐藏, 恢复公开自动回归推送
    ("900376763", "东之福气娟子"),
    ("900439290", "再也不硬抗了"),      # ⚠️ 10-15 起隐藏; 曾用名 兴奋之青果/我要再来一次; 11-07 重加: 调仓走流水通道推送; 11-11 更名对齐东财现名(userId 与武研琳不同账号)
    ("900461598", "大道可寻"),          # 2026-09-17 加入: 月+31.6% 总+37.8% 净值1.38(运行10天新号, 月/周/日三榜, 现持600371/603989)
    ("900293400", "月儿兰"),            # 2026-10-15 加入(用户指定): 满仓单票, 月+26.0% 总+234.0% 净值3.34(361天), 现持300111向日葵99%
    ("900458047", "点火的佩斯"),        # 2026-10-28 加入: 转债T+0高频→龙头重仓适应性强, 月+123.3% 总+24.5% 净值1.35(64天), 现持000678襄阳轴承已5连板
    ("900455448", "捏盘冲升"),          # 2026-11-07 加入: 狙击手打法(58天仅6笔全胜), 总+98.1% 净值1.98, 现满仓002086东方海洋浮盈+78%; 他的卖出动作比买入值钱
]

# 关注名单只用于置顶插入采集列表; 盘中时效性由 watchdog.yml 独立负责,
# 采集班不再按关注身份分档(避免与 watchdog 争抢同一 EM token 的限流额度)。


# =============================================================================
# 检查点管理
# =============================================================================

def load_checkpoint() -> Dict[str, Any]:
    """加载检查点"""
    if not CHECKPOINT_FILE.exists():
        return {
            'last_index': 0,
            'completed_ids': [],
            'last_list_update': None,
            'start_time': None
        }

    try:
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"加载检查点失败: {e}")
        return {
            'last_index': 0,
            'completed_ids': [],
            'last_list_update': None,
            'start_time': None
        }


def save_checkpoint(state: Dict[str, Any]):
    """保存检查点（原子写入，防止进程中断时文件损坏）"""
    try:
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = CHECKPOINT_FILE.with_suffix(".tmp")
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, separators=(',', ':'))
        tmp_file.replace(CHECKPOINT_FILE)
    except Exception as e:
        logger.warning(f"保存检查点失败: {e}")


# =============================================================================
# 异步数据采集核心
# =============================================================================

async def crawl_player_data_async(
    zh_id: str,
    name: str,
    level: str = "trades",
    user_id: str = "",
) -> Tuple[str, str, Optional[Dict], List, List]:
    """
    按档位采集单个选手（分层采集, 2026-11-10）

    档位由 crawl_with_semaphore 按班次与选手身份决定:
      full   收盘专班 EOD 校准 / 盘中新选手初始化(6调用)
      trades 盘中常规班(含关注选手) — 只调仓, 持仓由收集侧复制最近校准快照(1调用)
      hourly 整点班 — 调仓+收益净值(2调用)
      flash  watchdog/watched_flash 专用 — 调仓2页+真实持仓(main.py 不再选此档)

    Returns:
        (zh_id, name, detail, positions, trades)
        trades/hourly/flash 档 detail 为 None 或只含部分字段
    """
    detail, positions, trades = await crawl_player_all_data_async(
        zh_id, user_id or None, False, level)
    return (zh_id, name, detail, positions, trades)


async def crawl_all_data_async(
    players: List[Dict[str, Any]],
    storage,
    max_workers: int = 20,
    skip_existing: bool = True,
    checkpoint_interval: int = 50,
    level: str = "trades",
):
    """
    异步获取所有选手数据（纯 API，无需浏览器，分层档位）

    Args:
        players: 选手列表
        storage: 存储实例
        max_workers: 最大并发数
        skip_existing: (已废弃, 完成集现用于降档而非跳过; 保留参数兼容调用方)
        checkpoint_interval: 检查点保存间隔
        level: 班次基础档位 trades/hourly/full(新选手自动升 full; 关注选手与老选手同档)
    """
    # 本次数据日期
    crawl_date = date.today().isoformat()

    # 加载检查点
    checkpoint = load_checkpoint()
    completed_ids = set(checkpoint.get('completed_ids', []))
    start_time = checkpoint.get('start_time') or time.time()

    # checkpoint 是"当日完成集": 跨日自动失效, 避免完成集跨日无限累积。
    # 它不再用于档位判定(盘中班全员 trades), 仅作进度统计与断点记录。
    if checkpoint.get('crawl_date') != crawl_date:
        if completed_ids:
            logger.info("checkpoint 属于旧日期: 完成集清空")
        completed_ids = set()
    checkpoint['crawl_date'] = crawl_date

    # 库内已知选手集合(players 表全集): 不在其中的 = 新选手 → full 档初始化(含全量持仓)
    try:
        known_ids = set(storage.get_all_player_ids())
    except Exception as e:
        logger.warning(f"已知选手集合加载失败, 全员按已知处理: {e}")
        known_ids = set()

    logger.info(f"检查点: 已完成 {len(completed_ids)} 个选手, 库内已知选手 {len(known_ids)} 个")
    logger.info(f"本次数据日期: {crawl_date}, 班次档位: {level}")

    # 批量数据缓冲区
    pending_players = []
    pending_positions = []
    pending_trades = []

    def _flush_batch(storage, players_buf, positions_buf, trades_buf, crawl_date):
        """批量保存数据"""
        if players_buf:
            storage.save_players_batch(players_buf)
            logger.debug(f"批量保存 {len(players_buf)} 个选手")
        if positions_buf:
            storage.save_positions_batch(positions_buf, crawl_date)
            logger.debug(f"批量保存 {len(positions_buf)} 条持仓记录")
        if trades_buf:
            storage.save_trades_batch(trades_buf, crawl_date)
            logger.debug(f"批量保存 {len(trades_buf)} 条调仓记录")

    try:
        # 信号处理
        loop = asyncio.get_event_loop()
        interrupted = False

        def signal_handler():
            nonlocal interrupted
            logger.info("收到中断信号，正在保存检查点和数据...")
            interrupted = True
            _flush_batch(storage, pending_players, pending_positions, pending_trades, crawl_date)
            checkpoint['completed_ids'] = list(completed_ids)
            checkpoint['start_time'] = start_time
            save_checkpoint(checkpoint)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                pass

        # 创建信号量控制并发（API 并发可以设高一些）
        semaphore = asyncio.Semaphore(max_workers)

        def pick_level(zh_id: str) -> str:
            """班次基础档位 → 选手档位:
            收盘专班(level=full)全员 full 校准;
            盘中班: 库内无档案的新选手 full 初始化, 其余(含关注选手)一律 trades/hourly。
            关注选手的实时性由 watchdog.yml(每 5 分钟, flash 档)独立负责, crawl 班
            只保留 1 次调仓拉取保证库内不断档, 不再重复拉持仓 —— 避免与 watchdog
            争抢同一 EM token 的限流额度, 也缩短让位闸门的等待。
            确定性函数: 收集侧重算与发起时一致(known_ids 只在 full 成功分支追加)。"""
            if level == "full":
                return "full"
            if zh_id not in known_ids:
                return "full"      # 新选手初始化: 全量持仓+info, 之后各班降档
            return level

        async def crawl_with_semaphore(player: Dict[str, Any]) -> Tuple[str, str, Any, List, List]:
            zh_id = player.get('zh_id')
            name = player.get('name', '')

            async with semaphore:
                if interrupted:
                    return (zh_id, name, None, [], [])
                # 完成集不再跳过: 调仓流水每班必拉(watchdog 核心), 只是降档到 trades
                return await crawl_player_data_async(zh_id, name, pick_level(zh_id),
                                                     player.get('user_id', ''))

        # 构建 player_list 补充数据（labels, ranks）
        player_extra = {}
        for p in players:
            pid = p.get("zh_id", "")
            if pid:
                player_extra[pid] = {
                    "labels": p.get("labels", []),
                    "ranks": p.get("ranks", []),
                }

        # 创建所有任务
        tasks = [crawl_with_semaphore(p) for p in players]

        # 异步执行
        success_count = 0
        fail_count = 0
        skip_count = 0   # 降档制下不再有"跳过"(保留计数兼容返回值)

        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            result = await coro
            zh_id, name, detail, positions, trades = result

            if detail and detail.pop("stub", False):
                # flash 档轻量标记: 仅证明组合接口可达(hidden 探测依赖), 不作为档案入库
                detail = None

            if detail:
                # full/hourly 档
                if not detail.get("name"):
                    # hourly 档(或 full 档 info 接口偶发失败): 只含收益字段 →
                    # 与库内旧档案合并, 防止 UPSERT 用默认值清空 name/followers 等
                    old = storage.load_player(zh_id)
                    if old:
                        detail = {**old, **detail}
                # 合并 player_list 中的 labels 和 ranks
                extra = player_extra.get(zh_id, {})
                detail["labels"] = extra.get("labels", [])
                detail["ranks"] = extra.get("ranks", [])

                success_count += 1
                completed_ids.add(zh_id)
                known_ids.add(zh_id)   # 当班新选手入库, 同班内后续判定不再重复初始化
                pending_players.append(detail)
                if positions:
                    pending_positions.append((zh_id, positions))
                if trades:
                    pending_trades.append((zh_id, trades))
                logger.info(f"  ✓ [{i}/{len(players)}] {name}({zh_id}): {len(positions)}持仓, {len(trades)}调仓")

                # 批量保存
                if len(pending_players) >= BATCH_SIZE:
                    _flush_batch(storage, pending_players, pending_positions, pending_trades, crawl_date)
                    pending_players.clear()
                    pending_positions.clear()
                    pending_trades.clear()
            else:
                # 瘦身档(trades/hourly): 无完整 detail → players 表不写(收益/名字保持库内旧值)
                lvl = pick_level(zh_id)
                if trades or positions or lvl in ("trades", "hourly", "flash"):
                    success_count += 1
                    completed_ids.add(zh_id)
                    if trades:
                        pending_trades.append((zh_id, trades))
                    calib = ""
                    if not positions and lvl in ("trades", "hourly"):
                        # 盘中持仓 = 复制最近一次 EOD 校准快照(0 次 API)。
                        # 全员持仓只在收盘后更新; 盘中显示昨收, 同时保证 export_json
                        # 当日持仓快照非空(covered_ids/清仓守卫/变化对比不误判"空仓 0%")
                        prev = storage.load_positions(zh_id)
                        if prev:
                            calib = str(prev[0].get("crawl_date") or "")
                            positions = [dict(p) for p in prev
                                         if str(p.get("crawl_date") or "") == calib
                                         and p.get("stock_code")]
                    if positions:
                        pending_positions.append((zh_id, positions))
                    tag = f"校准{calib}快照" if calib else ("真实" if lvl == "flash" else "无")
                    logger.info(f"  ✓ [{i}/{len(players)}] {name}({zh_id}): "
                                f"{len(positions)}持仓({tag}), {len(trades)}调仓")

                    # 批量保存(瘦身班也要走, 防止长班次积压)
                    if len(pending_trades) + len(pending_positions) >= BATCH_SIZE:
                        _flush_batch(storage, pending_players, pending_positions, pending_trades, crawl_date)
                        pending_players.clear()
                        pending_positions.clear()
                        pending_trades.clear()
                else:
                    fail_count += 1
                    logger.warning(f"  ✗ [{i}/{len(players)}] {name}({zh_id}): 获取失败")

            # 保存检查点
            if i % checkpoint_interval == 0:
                _flush_batch(storage, pending_players, pending_positions, pending_trades, crawl_date)
                pending_players.clear()
                pending_positions.clear()
                pending_trades.clear()
                checkpoint['completed_ids'] = list(completed_ids)
                checkpoint['start_time'] = start_time
                save_checkpoint(checkpoint)
                logger.info(f"  [检查点已保存] 进度: {i}/{len(players)}")

            if interrupted:
                logger.info("采集被中断，已保存数据")
                break

        # 最终批量保存
        _flush_batch(storage, pending_players, pending_positions, pending_trades, crawl_date)

        # 最终检查点
        checkpoint['completed_ids'] = list(completed_ids)
        checkpoint['start_time'] = start_time
        save_checkpoint(checkpoint)

        logger.info("\n" + "=" * 60)
        logger.info(f"数据采集完成! 成功: {success_count}, 跳过: {skip_count}, 失败: {fail_count}")
        logger.info("=" * 60)

        return success_count, skip_count, fail_count

    except Exception as e:
        logger.exception(f"数据采集过程异常: {e}")
        _flush_batch(storage, pending_players, pending_positions, pending_trades, crawl_date)
        raise


# =============================================================================
# 主入口
# =============================================================================

def main():
    """主函数"""
    import argparse

    # 先检查是否有--analyze
    if '--analyze' in sys.argv:
        from src.analysis.position_analyzer import analyze_positions
        sys.argv = [a for a in sys.argv if a != '--analyze']
        analyze_positions()
        return

    parser = argparse.ArgumentParser(description='股票数据看板 - 数据采集')
    parser.add_argument('--test', action='store_true', help='测试模式(只处理10个选手)')
    parser.add_argument('--limit', type=int, default=500, help='每榜单获取数量(default: 500)')
    parser.add_argument('--workers', type=int, default=20, help='并发数(default: 20)')
    parser.add_argument('--no-skip', action='store_true', help='不跳过已存在的文件')
    parser.add_argument('--checkpoint-reset', action='store_true', help='重置检查点')
    args = parser.parse_args()

    skip_existing = not args.no_skip

    # 重置检查点
    if args.checkpoint_reset and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        logger.info("检查点已重置")

    # 确定存储
    storage = get_storage()

    logger.info("=" * 60)
    logger.info(f"股票数据看板 - 数据采集")
    logger.info(f"每榜单: {args.limit}名, 并发数: {args.workers}, 批量大小: {BATCH_SIZE}")
    logger.info(f"存储模式: SQLite")
    # 记录抓取开始时间（北京时间）
    crawl_start = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    (Path(__file__).parent / "data" / "crawl_start.txt").write_text(crawl_start)

    logger.info("=" * 60)

    # Step 1: 获取选手列表
    logger.info("\n[Step 1] 获取选手列表...")
    player_list_spider = PlayerListSpider()
    players = player_list_spider.fetch_player_list(args.limit)

    if not players:
        # 榜单接口返回空 = 采集失败(选手榜在非交易日同样有数据), 不能当作"今天没事"静默退出:
        # 否则后续 export/提交/部署会拿着旧库产出"今天"的页面(2026-09-15 静默审计 W7)。
        logger.error("❌ 未获取到选手列表(接口异常?), 以非零退出, 不生成当日数据")
        return 1

    # 确保关注选手始终在列表中（去重后插入最前），名单见模块级 WATCHED_PLAYERS
    existing_ids = {p.get("zh_id") for p in players}
    for zh_id, name in reversed(WATCHED_PLAYERS):
        if zh_id not in existing_ids:
            players.insert(0, {"zh_id": zh_id, "name": name})
            logger.info(f"已添加关注选手: {name}({zh_id})")
        else:
            # 移到最前面
            for i, p in enumerate(players):
                if p.get("zh_id") == zh_id:
                    players.pop(i)
                    players.insert(0, p)
                    break

    logger.info(f"共获取 {len(players)} 个选手 (去重后)")

    if args.test:
        players = players[:10]
        logger.info(f"测试模式: 只处理前 {len(players)} 个选手")

    # Step 2: 异步获取数据(分层档位, 2026-11-10)
    # 班次档位: crawl-eod(EM_FULL_CRAWL=1 兼容)→full 收盘校准;
    #           EM_CRAWL_LEVEL 显式指定(trades/hourly/flash/full); 默认盘中常规班 trades(1调用/人, 持仓复制校准快照)
    level = os.environ.get("EM_CRAWL_LEVEL", "").strip()
    if level not in ("trades", "hourly", "flash", "full"):
        level = "full" if os.environ.get("EM_FULL_CRAWL") == "1" else "trades"
    if level == "full":
        logger.info("收盘专班/初始化班: 全量模式(EOD 校准)")
    logger.info(f"\n[Step 2] 异步获取选手数据 (并发数={args.workers}, 班次档位={level})...")

    try:
        success_count, skip_count, fail_count = asyncio.run(crawl_all_data_async(
            players,
            storage,
            max_workers=args.workers,
            skip_existing=skip_existing,
            level=level
        ))
    except KeyboardInterrupt:
        logger.info("采集被用户中断")
        return 1
    except Exception as e:
        # 未处理异常 = 采集未完成; 非零退出让 workflow 停在这里,
        # 避免旧库数据被导出/提交/部署成"今天的快照"(2026-09-15 静默审计 W7)
        logger.exception(f"❌ 数据采集过程出现未处理异常: {e}")
        return 1

    # 全员失败 = 源头不可用(网络/接口风控), 同样不产出当日数据;
    # 部分失败仍继续(逐人失败已在日志逐条列出, 缺口由 export 的采集覆盖守卫兜住)
    if players and success_count == 0 and fail_count >= len(players):
        logger.error(f"❌ 全部 {len(players)} 名选手采集失败, 判定为源头不可用, 非零退出")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)