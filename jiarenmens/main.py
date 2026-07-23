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

# 始终抓取的关注选手（不跳过检查点）
WATCHED_ZH_IDS = {"900240956"}  # 股得猫咛


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
) -> Tuple[str, str, Optional[Dict], List, List]:
    """
    通过 API 获取单个选手的所有数据

    Args:
        zh_id: 选手ID
        name: 选手名称

    Returns:
        (zh_id, name, detail, positions, trades)
    """
    detail, positions, trades = await crawl_player_all_data_async(zh_id)
    return (zh_id, name, detail, positions, trades)


async def crawl_all_data_async(
    players: List[Dict[str, Any]],
    storage,
    max_workers: int = 20,
    skip_existing: bool = True,
    checkpoint_interval: int = 50
):
    """
    异步获取所有选手数据（纯 API，无需浏览器）

    Args:
        players: 选手列表
        storage: 存储实例
        max_workers: 最大并发数
        skip_existing: 是否跳过已存在的
        checkpoint_interval: 检查点保存间隔
    """
    # 加载检查点
    checkpoint = load_checkpoint()
    completed_ids = set(checkpoint.get('completed_ids', []))
    start_time = checkpoint.get('start_time') or time.time()

    # 本次数据日期
    crawl_date = date.today().isoformat()

    logger.info(f"检查点: 已完成 {len(completed_ids)} 个选手")
    logger.info(f"本次数据日期: {crawl_date}")

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

        async def crawl_with_semaphore(player: Dict[str, Any]) -> Tuple[str, str, Any, List, List]:
            zh_id = player.get('zh_id')
            name = player.get('name', '')

            async with semaphore:
                # 关注选手始终重新抓取，不跳过
                if interrupted or (zh_id in completed_ids and zh_id not in WATCHED_ZH_IDS):
                    return (zh_id, name, None, [], [])

                return await crawl_player_data_async(zh_id, name)

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
        skip_count = 0

        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            result = await coro
            zh_id, name, detail, positions, trades = result

            if detail:
                # 合并 player_list 中的 labels 和 ranks
                extra = player_extra.get(zh_id, {})
                detail["labels"] = extra.get("labels", [])
                detail["ranks"] = extra.get("ranks", [])

                success_count += 1
                completed_ids.add(zh_id)
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
                if zh_id in completed_ids:
                    skip_count += 1
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
        logger.warning("未获取到选手列表")
        return

    # 确保关注选手始终在列表中（去重后插入最前）
    WATCHED_PLAYERS = [
        {"zh_id": "900240956", "name": "股得猫咛"},
    ]
    existing_ids = {p.get("zh_id") for p in players}
    for wp in reversed(WATCHED_PLAYERS):
        if wp["zh_id"] not in existing_ids:
            players.insert(0, wp)
            logger.info(f"已添加关注选手: {wp['name']}({wp['zh_id']})")
        else:
            # 移到最前面
            for i, p in enumerate(players):
                if p.get("zh_id") == wp["zh_id"]:
                    players.pop(i)
                    players.insert(0, p)
                    break

    logger.info(f"共获取 {len(players)} 个选手 (去重后)")

    if args.test:
        players = players[:10]
        logger.info(f"测试模式: 只处理前 {len(players)} 个选手")

    # Step 2: 异步获取数据
    logger.info(f"\n[Step 2] 异步获取选手数据 (并发数={args.workers}, 跳过已存在={skip_existing})...")

    try:
        asyncio.run(crawl_all_data_async(
            players,
            storage,
            max_workers=args.workers,
            skip_existing=skip_existing
        ))
    except KeyboardInterrupt:
        logger.info("采集被用户中断")
    except Exception as e:
        logger.exception(f"数据采集过程出现未处理异常: {e}")


if __name__ == "__main__":
    main()