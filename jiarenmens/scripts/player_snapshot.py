#!/usr/bin/env python3
"""P1 · MVP 数据层：4 JSON 契约生产者（feature ``player-data-refactor`` / Sprint 1）。

子命令（AC-1.3 / 架构 §7.3 请求量口径）::

  --rank     09:26 榜单唯一一次：rtV1 ``rt_get_rank`` 5 榜 × 300（75 分页）→ players.json
             → **恰好 75 次**榜单 API 调用（K3）
  --midday   11:30：**2 调用/人** = 调仓 1 页（当日过滤）+ 持仓，**NO yield API**
             → stocks.json(snapshot=1130) / today.json / players[].today（2600 次 @N=1300）
  --close    15:15：**3 调用/人** = 调仓 1 页 + 持仓 + **收益 yield（额外一次，仅此处）**
             → 收盘覆盖 + prevClose 更新（3900 次 @N=1300）

  4-JSON 链路全天选手请求 = 75 + 2N + 3N = **6575**（N=1300，K1 精确保真）。

硬约束（S1 / AQ3=B / AQ5=A / D3 / F6）：
  * 产物**仅写 /tmp 或 --out 临时目录**；``resolve_out`` 硬拒绝任何
    线上 ``stockboard-app/public`` 生产路径（含大小写变体 / symlink / ``..`` / ``//``，
    线上 stocks.json 保持旧 ``stockStats[]``）。
  * ``userId`` 硬连线自排行榜每行 ``userid`` 字段（AQ5=A）：**无回退分支、
    无逐人补解析调用**；缺字段即显式失败（Failure ≠ Fact）。
  * 调仓固定 1 页（``EM_TRADE_PAGES`` 默认 1），``beginDate=endDate=当日``，不取历史 40 条。
  * ``SB_DATA_V2=0`` → no-op kill switch；原子写统一走 ``src/utils/atomic.py``（F6）。
  * ``--demo`` / ``--fixtures`` 为离线 dry-run 通道（零网络副作用）；``--live`` 复用
    既有 EM 客户端（``api_detail._call_api_new`` + ``PlayerListSpider``），S1 默认不启用。
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.utils.atomic import _atomic_json  # noqa: E402  (F6 原子写)
from src.spiders.api_detail import NEW_USER_IDS as WATCHED_USER_IDS  # noqa: E402
# M-02：复用既有 EM 协议实现（仅 import / reuse，绝不修改这两个 tracked 文件）。
from src.spiders import api_detail as _api_detail  # noqa: E402  (_call_api_new / _load_tokens)
from src.spiders.player_list import PlayerListSpider  # noqa: E402  (rtV1 榜单)

BJ_TZ = ZoneInfo("Asia/Shanghai")

# ── rtV1 榜单契约（与 src/spiders/player_list.py 保持一致；AQ5=A 硬连线 userid）──
RANK_API_URL = "https://emdcspzhapi.dfcfs.cn/rtV1"
V2_API_URL = "https://spzhapi.dfcfs.cn/rtV2"
RANK_TYPES = {"10004": "总榜", "10003": "年榜", "10001": "月榜",
              "10000": "周榜", "10005": "日榜"}
RANK_PAGE_SIZE = 20
DEFAULT_RANK_LIMIT = 300          # 5 榜 × 300 / 20 = 恰好 75 次（K3）
DEFAULT_TRADE_PAGES = 1           # D3：固定 1 页，作用域仅本链路
RANK_TIME = "09:26"
SNAPSHOT_MIDDAY = "1130"
SNAPSHOT_CLOSE = "close"
# 契约 §3.1：players[].today.updatedAt ∈ {11:30, 15:15}（快照时点常量，非完整 ISO）。
# 注意：stocks.json / today.json 的 updatedAt 仍是完整 ISO（§3.2/§3.3），二者语义不同。
TODAY_LABEL = {SNAPSHOT_MIDDAY: "11:30", SNAPSHOT_CLOSE: "15:15"}

# M-02：rtV2 handler 名（与 src/spiders/api_detail.crawl_player_all_data 完全一致）。
V2_YIELD_HANDLER = "combination_yield_detail_handler"
V2_POSITION_HANDLER = "CombinationHoldPositionPermitHandler"
V2_TRADES_HANDLER = "CombinationRelocatePositionHandler"

# 9 名特别关注选手（顺序 = 置顶顺序；语义同 main.py:46-57）。
# ⚠️ 本副本必须与 src/config/watched.py（AQ2=A 单一真源）逐项一致：cmd_rank 的
# force_insert_watched 用的是这份，改名单时两处都要改，否则下一班会把移出者重新插回。
WATCHED_PLAYERS = [
    ("900456476", "狼之行一"), ("900351276", "新生代柚子04"),
    ("900240956", "股得猫咛"),
    ("900376763", "东之福气娟子"), ("900439290", "再也不硬抗了"),
    ("900461598", "大道可寻"), ("900293400", "月儿兰"),
    ("900458047", "点火的佩斯"), ("900455448", "捏盘冲升"),
]

ONLINE_MARKER = "public/data/latest"
DEFAULT_OUT = Path(tempfile.gettempdir()) / "player-data-refactor-s1" / "out"

# M-03 加固：真实生产目录（大小写不敏感 FS 下 PUBLIC/DATA/LATEST 变体亦指向同一 inode）。
# ROOT = .../jiarenmens，ROOT.parent = 仓库根；线上产物位于 <repo>/stockboard-app/public/。
PROD_PUBLIC = ROOT.parent / "stockboard-app" / "public"
PROD_LATEST = PROD_PUBLIC / "data" / "latest"

# 4 个静态 JSON 分片（严格 4 个，PRD-Q4=6B：不产第 5 个 JSON）。
PLAYERS_JSON = "players.json"
STOCKS_JSON = "stocks.json"
TODAY_JSON = "today.json"


# ═══════════════════════════════════════════
# 公共小工具（S2 由 AQ2=A 统一迁入 src/ 共享模块；S1 各自内联保持自包含）
# ═══════════════════════════════════════════

def read_json(path, default=None):
    """安全读 JSON；缺失/截断返回 default（carry-forward 的失败兜底）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def bj_now():
    return datetime.now(BJ_TZ)


def today_bj():
    return bj_now().strftime("%Y-%m-%d")


def bj_iso():
    return bj_now().replace(microsecond=0).isoformat()


def kill_switch_active():
    """``SB_DATA_V2=0`` → no-op kill switch（Epic 4 灰度开关）。"""
    return os.environ.get("SB_DATA_V2", "").strip() == "0"


def _canonical(path):
    """M-03：realpath + normcase + casefold 归一化，抹平大小写 / symlink / ``..`` / ``//`` / 尾斜杠。

    ``os.path.realpath`` 解析 symlink 与 ``..``；``os.path.normcase`` 在 macOS 上
    **不**做大小写折叠（实测 normcase('/X/PUBLIC') 仍为 '/X/PUBLIC'），故必须再叠加
    ``casefold()`` 才能覆盖 ``PUBLIC/DATA/LATEST`` 这类大小写变体绕过。
    """
    return os.path.normcase(os.path.realpath(str(path))).casefold()


def _is_production_out(value):
    """M-03：判断候选 --out 是否指向（或等于/包含于/祖先于）任何线上生产路径。

    三层判定（任一命中即拒绝，S1 期间拒绝 ALL 生产路径）：
      1. 大小写不敏感子串 ``public/data/latest``（覆盖绝对路径 / 相对路径 / ``..``
         穿透 / 双斜杠 / 尾斜杠 / 尚不存在的路径）；
      2. 归一化后与 ``PROD_LATEST`` / ``PROD_PUBLIC`` 做双向路径前缀比对
         （candidate 落在生产目录内，或 candidate 是生产目录的祖先 —— 拒绝整棵树）；
      3. inode 级 ``os.path.samefile`` 兜底：目录真实存在时，符号链接 / 大小写
         变体与生产目录同 inode 也拒绝（macOS 大小写不敏感 FS 实证）。
    """
    cand = _canonical(Path(value).expanduser().resolve())
    marker = os.path.normcase(ONLINE_MARKER).casefold()
    if marker in cand:
        return True
    for base in (_canonical(PROD_LATEST), _canonical(PROD_PUBLIC)):
        if cand == base or cand.startswith(base + os.sep) \
                or base.startswith(cand.rstrip(os.sep) + os.sep):
            return True
    # inode 比对（仅在两端都存在时有意义；不创建任何目录）
    resolved = os.path.realpath(str(Path(value).expanduser().resolve()))
    for prod_path in (PROD_LATEST, PROD_PUBLIC):
        try:
            if os.path.exists(resolved) and os.path.exists(prod_path) \
                    and os.path.samefile(resolved, str(prod_path)):
                return True
        except OSError:
            pass
    return False


def resolve_out(value):
    """S1 硬约束：产物仅允许 /tmp 或 --out 临时目录，绝不写线上 public/（AQ3=B）。

    M-03：由大小写敏感 deny-list 升级为 ``realpath + normcase + casefold`` 双向
    归一化 + inode samefile 兜底，覆盖大写变体 / ``..`` / ``//`` / symlink /
    相对路径+尾斜杠等所有绕过形式。S2 接入 crawl.yml 时才移除守卫（属 S2 scope）。
    """
    if _is_production_out(value) and os.environ.get("SB_WRITE_PROD") != "1":
        out = Path(value).expanduser().resolve()
        raise SystemExit(
            f"拒绝写入线上路径 {out}；默认仅允许 /tmp 或 --out 临时目录（AQ3=B）；"
            f"CI 接入需显式设置 SB_WRITE_PROD=1")
    out = Path(value).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


# ═══════════════════════════════════════════
# EM 客户端：FixtureClient（离线 dry-run / 单测）与 EmClient（S2 真实接入）
# ═══════════════════════════════════════════

class FixtureClient:
    """离线客户端：读 ``bundle.json`` 或内联 dict，零网络副作用，记录每次调用供单测断言。"""

    def __init__(self, path=None, data=None):
        self.calls = []
        bundle = data if data is not None else (read_json(path, {}) or {})
        self.rank = bundle.get("rank", {})
        self.yield_ = bundle.get("yield", {})
        self.position = bundle.get("position", {})
        self.trades = bundle.get("trades", {})

    @classmethod
    def from_data(cls, data):
        return cls(data=data)

    def rank_page(self, rank_type, offset, count):
        self.calls.append(("rank_page", rank_type, offset, count))
        return list(self.rank.get(rank_type, []))[offset:offset + count]

    def player_yield(self, zh_id, user_id):
        self.calls.append(("yield", zh_id))
        return dict(self.yield_.get(zh_id, {}))

    def player_position(self, zh_id, user_id):
        self.calls.append(("position", zh_id))
        return list(self.position.get(zh_id, []))

    def player_trades(self, zh_id, user_id, begin_date, end_date, page):
        """D3：beginDate=endDate=当日，固定 1 页，不取历史 40 条。"""
        self.calls.append(("trades", zh_id, begin_date, end_date, page))
        return [dict(t) for t in self.trades.get(zh_id, [])
                if begin_date <= t.get("trade_date", "") <= end_date]

    def rank_calls(self):
        return sum(1 for c in self.calls if c[0] == "rank_page")


class EmClient:
    """真实 EM 客户端（``--live``；S2 workflow 接入，S1 默认不启用 / 零网络副作用）。

    M-02：不再自建 HTTP 请求。改为**只读复用**既有 EM 协议实现
    （绝不修改 ``api_detail.py`` / ``player_list.py`` 这两个 tracked 文件）：

      * rtV2（收益 / 持仓 / 调仓）→ ``src.spiders.api_detail._call_api_new`` +
        ``_load_tokens``：登录态 ``ctToken``/``utToken`` 放进 args，``randomCode`` /
        ``deviceId`` / ``clientVersion`` / UA / Referer / ``code==0`` 校验 / 重试 /
        超时全部由既有 helper 统一注入（2026-11-10 迁移后真实协议，[A6]）。
      * rtV1（榜单）→ ``src.spiders.player_list.PlayerListSpider.fetch_rank_players``
        / ``fetch_player_list``：既有 session(UA/Referer) + ``result=="0"`` 校验 +
        20/页分页；``userid`` 由 AQ5=A 硬连线。

    ★ 诚实登记的残留 gap（S2 用录制/真实响应收口，本 Sprint **不伪造**）：
      1. 既有 ``fetch_rank_players(rank_type, max_count)`` 内部自带分页并返回
         **归一化**行（键 ``zh_id``/``user_id``/``name``），而 ``cmd_rank`` 的
         ``rank_page(offset, count)`` 期望**单页** rtV1 原始行（键 ``zjzh`` /
         ``userid`` / ``zhuheName``）。二者接口不同、无法「一页一调用」干净对齐；
         本类按 rank_type 缓存一次 spider 结果，反适配键名后按 offset/count 切片。
         真实 HTTP 次数由 spider 内部分页决定，与 §7.3 的 75 次分页口径不完全
         等价 —— 属 S2 workflow 接入对齐项，不在 S1 假装有 75 次真实请求。
      2. ``_call_api_new`` 返回 ``data`` 内层对象，各 handler 字段形态不同。本类
         **复用** api_detail 既有换算口径（``_pct`` / ``_safe_float`` /
         ``_parse_positions``）做最小结构化适配；真实字段与阈值仍须 S2 用录制
         响应核对，不臆造字段值。
      3. token 缺失时 ``_require_tokens`` 显式失败（诚实失败，不静默当成功）。
    """

    def __init__(self, spider=None, tokens=None, rank_limit=None):
        self.calls = []
        # 默认接真实 helper；单测注入 mock 以验证参数构造、零网络。
        self._spider = spider if spider is not None else PlayerListSpider()
        self._tokens = tokens if tokens is not None else _api_detail._load_tokens()
        self._rank_limit = rank_limit or DEFAULT_RANK_LIMIT
        self._rank_cache = {}

    # ── rtV2：统一走既有 _call_api_new（token / randomCode / deviceId / UA）──
    def _require_tokens(self):
        """登录态 token 缺失 → 诚实失败（对齐 api_detail.crawl_player_all_data）。"""
        if not self._tokens or not self._tokens.get("ctToken") \
                or not self._tokens.get("utToken"):
            raise RuntimeError(
                "EM 登录态 token 缺失（EM_CT_TOKEN/EM_UT_TOKEN 或 em_tokens.json）；"
                "拒绝伪造成功，--live 采集诚实失败")
        return self._tokens

    @staticmethod
    def _combination_id(zh_id):
        """真实协议 ``combinationId`` 为 int（api_detail: ``int(zh_id)``）。"""
        try:
            return int(zh_id)
        except (TypeError, ValueError):
            return zh_id

    def _base_args(self, zh_id, user_id):
        """与 api_detail.crawl_player_all_data 的 ``base`` 完全一致：
        ``ctToken``/``utToken`` 放 args 内 + 公开 ``userId`` + ``combinationId``。"""
        tokens = self._require_tokens()
        return {"ctToken": tokens["ctToken"], "utToken": tokens["utToken"],
                "userId": str(user_id),
                "combinationId": self._combination_id(zh_id)}

    def player_yield(self, zh_id, user_id):
        self.calls.append(("yield", zh_id))
        args = self._base_args(zh_id, user_id)
        data = _api_detail._call_api_new(V2_YIELD_HANDLER, args, self._tokens) or {}
        # 复用 api_detail 既有换算口径把 rtV2 净值字段适配为 prevClose 契约字段。
        # 月/年收益沿用 api_detail 的 monthYieldRate / yearYieldRate（无 View 后缀的原始值）；
        # 字段缺失写 None 而非 0.0 —— 不可用 0 冒充缺失（Failure ≠ Fact）。
        month = data.get("monthYieldRate")
        year = data.get("yearYieldRate")
        return {"totalReturn": _api_detail._pct(data.get("totalYieldRate")),
                "dailyReturn": _api_detail._pct(data.get("dayYieldRate")),
                "monthlyReturn": _api_detail._pct(month) if month not in (None, "") else None,
                "yearlyReturn": _api_detail._pct(year) if year not in (None, "") else None,
                "netValue": _api_detail._safe_float(data.get("netWorth"))}

    def player_position(self, zh_id, user_id):
        self.calls.append(("position", zh_id))
        args = self._base_args(zh_id, user_id)
        raw = _api_detail._call_api_new(V2_POSITION_HANDLER, args, self._tokens)
        parsed = _api_detail._parse_positions(str(zh_id), raw) or []
        # 复用 _parse_positions 输出，映射为 aggregate_stock_stats 期望的键名。
        return [{"code": r.get("stock_code", ""), "name": r.get("stock_name", ""),
                 "ratio": r.get("position_ratio", 0.0), "profit": r.get("profit_ratio", 0.0)}
                for r in parsed]

    def player_trades(self, zh_id, user_id, begin_date, end_date, page):
        """D3：单页 + ``beginDate=endDate=当日`` 当日过滤，不取历史 40 条。"""
        self.calls.append(("trades", zh_id, begin_date, end_date, page))
        args = self._base_args(zh_id, user_id)
        args.update({"beginDate": begin_date, "endDate": end_date,
                     "pageNum": page, "pageSize": RANK_PAGE_SIZE})
        data = _api_detail._call_api_new(V2_TRADES_HANDLER, args, self._tokens) or {}
        out = []
        for row in (data.get("pages") or []):
            biz = str(row.get("bizDate", "") or "")
            trade_date = f"{biz[:4]}-{biz[4:6]}-{biz[6:8]}" if len(biz) == 8 else biz
            if begin_date <= trade_date <= end_date:      # 当日过滤（Failure ≠ Fact）
                out.append({"trade_date": trade_date,
                            "stock_code": str(row.get("stkCode", "") or "")})
        return out

    # ── rtV1：复用既有 PlayerListSpider（result=="0" / UA / Referer / 20 分页）──
    def rank_page(self, rank_type, offset, count):
        self.calls.append(("rank_page", rank_type, offset, count))
        if rank_type not in self._rank_cache:
            normalized = self._spider.fetch_rank_players(
                rank_type, max_count=self._rank_limit) or []
            # AQ5=A 硬连线 userid：归一化行反适配为 rtV1 原始行键（非回退分支）。
            self._rank_cache[rank_type] = [
                {"zjzh": str(r.get("zh_id", "") or ""),
                 "userid": str(r.get("user_id", "") or ""),
                 "zhuheName": str(r.get("name", "") or "")}
                for r in normalized]
        return list(self._rank_cache[rank_type])[offset:offset + count]

    def fetch_all_players(self):
        """全榜合并去重入口 → 直接复用 PlayerListSpider.fetch_player_list。"""
        return self._spider.fetch_player_list(max_per_rank=self._rank_limit) or []

    def rank_calls(self):
        return sum(1 for c in self.calls if c[0] == "rank_page")


# ═══════════════════════════════════════════
# 纯函数：4 JSON 契约的构建（单测直接断言）
# ═══════════════════════════════════════════

def dedupe_rank_rows(rows_by_type):
    """AQ5=A 硬连线数据路径：``userId`` **直接取每行 ``userid`` 字段**。

    已由真实 rtV1 响应验证该字段每行存在；**不实现任何「缺字段 → 回退 B /
    逐人补解析 / 回到用户确认」分支**（ADR-005）。缺字段或空值 → 显式抛错
    （Failure ≠ Fact），保证 players[] 永不出现缺失 userId。
    """
    out = {}
    for rows in rows_by_type.values():
        for row in rows:
            zh_id = str(row.get("zjzh", "") or "")
            if not zh_id:
                continue
            user_id = str(row.get("userid", "") or "")
            if not user_id:
                raise ValueError("AQ5=A：rt_get_rank 行缺少 userid 字段（禁止回退分支）")
            if zh_id not in out:
                out[zh_id] = {"id": zh_id, "name": row.get("zhuheName", ""),
                              "userId": user_id, "watched": False,
                              "prevClose": {}, "today": {}}
    return out


def force_insert_watched(players_map):
    """9 名 WATCHED_PLAYERS **无条件强制插入**（无论在不在首班榜内），watched=True。

    榜内者沿用其 ``userid``（AQ5=A 主路径）；榜外者取已知公开 userId 静态常量
    （同 ``api_detail.NEW_USER_IDS``，**0 额外请求**）。这不是 AQ5=A 禁止的条件
    回退分支：无字段探测、无逐人 API 补解析、不触发 option B。
    """
    for zh_id, name in WATCHED_PLAYERS:
        if zh_id in players_map:
            players_map[zh_id]["watched"] = True
            continue
        user_id = str(WATCHED_USER_IDS.get(zh_id, "") or "")
        if not user_id:
            raise ValueError(f"S1：榜外 watched {zh_id} 缺已知 userId（禁止静默填空）")
        players_map[zh_id] = {"id": zh_id, "name": name, "userId": user_id,
                              "watched": True, "prevClose": {}, "today": {}}
    return players_map


def build_players_doc(date, players_map, prev_players=None):
    """players.json。``prevClose`` / ``today`` 从上一版 carry-forward（盘中不变）。"""
    prev_map = {p["id"]: p for p in (prev_players or {}).get("players", [])}
    players = []
    for item in players_map.values():
        merged = dict(item)
        old = prev_map.get(item["id"], {})
        merged["prevClose"] = old.get("prevClose", {})
        merged["today"] = old.get("today", {})
        players.append(merged)
    return {"date": date, "rankTime": RANK_TIME, "players": players}


def aggregate_stock_stats(position_by_player):
    """全量口径 ``stats[].{code,name,h,tp,ap}``（**无 quality qh/qtp/qap**）。"""
    agg = {}
    for rows in position_by_player.values():
        for row in rows:
            code = str(row.get("code", "") or "")
            if not code:
                continue
            entry = agg.setdefault(code, {"code": code, "name": row.get("name", ""),
                                          "h": 0, "tp": 0.0, "ap": 0.0})
            entry["h"] += 1
            entry["tp"] += float(row.get("ratio", 0.0) or 0.0)
            entry["ap"] += float(row.get("profit", 0.0) or 0.0)
    stats = sorted(agg.values(), key=lambda e: (-e["tp"], e["code"]))
    for entry in stats:
        entry["tp"] = round(entry["tp"], 2)
        entry["ap"] = round(entry["ap"], 2)
    return stats


def build_stocks_doc(date, snapshot, updated_at, stats):
    """``snapshot`` 枚举硬校验：仅 1130 / close（架构 §3.2 / AC-1.3.3）。"""
    if snapshot not in (SNAPSHOT_MIDDAY, SNAPSHOT_CLOSE):
        raise ValueError(f"snapshot 枚举仅允许 1130/close，收到 {snapshot!r}")
    return {"date": date, "snapshot": snapshot, "updatedAt": updated_at, "stats": stats}


def build_today_doc(date, updated_at, traded_ids, prev_today=None):
    """today.json：``tradedPlayerIds`` 11:30→15:15 并集 carry-forward；
    ``emContract`` **原样保留**（当日 sticky，不重置为 true，架构 §3.3）。"""
    prev = prev_today or {}
    em = prev.get("emContract") or {"ok": False, "checkedAt": ""}
    return {"date": date, "updatedAt": updated_at,
            "tradedPlayerIds": sorted(set(list(prev.get("tradedPlayerIds", [])) + list(traded_ids))),
            "emContract": em}


def patch_players_today(doc, updated_at, trade_counts, yield_by_player=None,
                        refresh_prev_close=False, today_label=None):
    """给 players[].today 打补丁。

    * ``updatedAt`` ∈ {11:30, 15:15}（契约 §3.1 快照时点常量 ``today_label``）；
      与 stocks.json / today.json 的完整 ISO ``updated_at`` 语义不同。
    * 仅 ``--close`` 用收益数据刷新 prevClose（AC-1.1：盘中不变，15:15 才更新）。
    * ``--midday`` 无收益数据：不触碰 prevClose、不臆造收益值。
    """
    label = today_label if today_label is not None else updated_at
    for player in doc["players"]:
        player["today"] = {"updatedAt": label,
                           "tradeCount": int(trade_counts.get(player["id"], 0))}
        if refresh_prev_close and yield_by_player and yield_by_player.get(player["id"]):
            y = yield_by_player[player["id"]]
            player["prevClose"] = {"totalReturn": y.get("totalReturn", 0.0),
                                   "dailyReturn": y.get("dailyReturn", 0.0),
                                   "monthlyReturn": y.get("monthlyReturn"),
                                   "yearlyReturn": y.get("yearlyReturn"),
                                   "netValue": y.get("netValue", 0.0)}
    return doc


# ═══════════════════════════════════════════
# 子命令
# ═══════════════════════════════════════════

def cmd_rank(out, client, date, rank_limit):
    """09:26：5 榜 × ceil(limit/20) 分页 = 恰好 75 次（limit=300 时确定性）。"""
    rows_by_type = {}
    for rank_type in RANK_TYPES:
        rows = []
        for offset in range(0, rank_limit, RANK_PAGE_SIZE):
            count = min(RANK_PAGE_SIZE, rank_limit - offset)
            rows.extend(client.rank_page(rank_type, offset, count))
        rows_by_type[rank_type] = rows
    players_map = force_insert_watched(dedupe_rank_rows(rows_by_type))
    prev = read_json(out / PLAYERS_JSON, None)
    doc = build_players_doc(date, players_map, prev)
    _atomic_json(out / PLAYERS_JSON, doc)
    return doc


def cmd_snapshot(out, client, date, snapshot, updated_at, trade_pages):
    """11:30 / 15:15 两班（AC-1.3 / 架构 §7.3 精确请求量口径）。

    调用集合（N = 选手数）：
      * ``--midday``：**2 调用/人** = 调仓 1 页（当日过滤）+ 持仓；**不调收益接口**。
      * ``--close`` ：**3 调用/人** = 调仓 1 页 + 持仓 + **收益（额外且仅此处）**。

    ``snapshot`` 保持 ∈ {1130, close}。产物在 midday 无收益数据时仍完整合法：
      * ``stocks.json``：仅由持仓接口聚合（midday 仍调 position），与收益无关；
      * ``today.json``：仅由调仓结果生成（midday 仍调 trades）；
      * ``players[].today``：仅由 tradeCount 打补丁；
      * ``players[].prevClose``：**盘中不变**，沿用 --rank carry-forward 值，
        仅 --close 用收益数据刷新 —— **不臆造任何收益值**（Failure ≠ Fact）。
    """
    if snapshot not in (SNAPSHOT_MIDDAY, SNAPSHOT_CLOSE):
        raise ValueError(f"snapshot 枚举仅允许 1130/close，收到 {snapshot!r}")
    is_close = (snapshot == SNAPSHOT_CLOSE)

    players_doc = read_json(out / PLAYERS_JSON, None)
    if not players_doc:
        raise SystemExit("需先跑 --rank 生成 players.json（09:26 榜单首班）")
    players = players_doc["players"]
    user_ids = {p["id"]: p["userId"] for p in players}

    yield_by, position_by, trades_by = {}, {}, {}
    for player in players:
        zh_id = player["id"]
        # 两班共有：持仓 + 调仓固定 1 页（D3 当日过滤、不取历史 40 条）
        position_by[zh_id] = client.player_position(zh_id, user_ids[zh_id])
        for page in range(1, trade_pages + 1):
            trades_by.setdefault(zh_id, []).extend(
                client.player_trades(zh_id, user_ids[zh_id], date, date, page))
        # AC-1.3：收益接口是 --close 独有的额外一次调用（midday 绝不调用）
        if is_close:
            yield_by[zh_id] = client.player_yield(zh_id, user_ids[zh_id])

    traded_ids = [zh for zh, rows in trades_by.items() if rows]
    stats = aggregate_stock_stats(position_by)
    _atomic_json(out / STOCKS_JSON, build_stocks_doc(date, snapshot, updated_at, stats))
    prev_today = read_json(out / TODAY_JSON, None)
    _atomic_json(out / TODAY_JSON,
                 build_today_doc(date, updated_at, traded_ids, prev_today))
    counts = {zh: len(rows) for zh, rows in trades_by.items()}
    patch_players_today(players_doc, updated_at, counts,
                        yield_by if is_close else None,
                        refresh_prev_close=is_close,
                        today_label=TODAY_LABEL[snapshot])
    _atomic_json(out / PLAYERS_JSON, players_doc)
    return {"snapshot": snapshot, "tradedPlayerIds": traded_ids, "stats": len(stats)}


def demo_bundle(date):
    """内置离线样例：5 榜少量行 + 收益/持仓/调仓，用于零网络 dry-run 展示 4 JSON 形状。"""
    sample = [("900293400", "月儿兰", "6838395742604432"),
              ("900351276", "新生代柚子04", "3208446625163740"),
              ("900461598", "大道可寻", "3019386220935090"),
              ("900000001", "甲选手", "1000000000000001"),
              ("900000002", "乙选手", "1000000000000002")]
    rank = {rt: [{"zjzh": z, "zhuheName": n, "userid": u} for z, n, u in sample]
            for rt in RANK_TYPES}
    all_ids = list(dict.fromkeys([z for z, _, _ in sample] + [z for z, _ in WATCHED_PLAYERS]))
    yield_ = {z: {"totalReturn": 100.0 + i, "dailyReturn": 1.5,
                  "monthlyReturn": -0.24, "yearlyReturn": 215.77, "netValue": 2.0}
              for i, z in enumerate(all_ids)}
    position = {
        "900293400": [{"code": "002086", "name": "东方海洋", "ratio": 99.0, "profit": 78.0}],
        "900351276": [{"code": "002086", "name": "东方海洋", "ratio": 50.0, "profit": 12.0},
                      {"code": "600715", "name": "文投控股", "ratio": 40.0, "profit": 3.0}],
        "900000001": [{"code": "600715", "name": "文投控股", "ratio": 20.0, "profit": 1.0}],
    }
    trades = {"900293400": [{"trade_date": date, "stock_code": "002086"}],
              "900000002": [{"trade_date": date, "stock_code": "600715"}]}
    return {"rank": rank, "yield": yield_, "position": position, "trades": trades}


def make_client(args, date):
    if args.live:
        return EmClient()                       # S2 workflow 接入；S1 零网络副作用
    if args.fixtures:
        return FixtureClient(path=Path(args.fixtures) / "bundle.json")
    if args.demo:
        return FixtureClient.from_data(demo_bundle(date))
    raise SystemExit("S1 离线 dry-run：需 --demo 或 --fixtures <dir>（真实 EM 调用属 S2 接入）")


def build_parser():
    ap = argparse.ArgumentParser(description="P1 4-JSON 契约生产者（Sprint 1，产物仅 /tmp）")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--rank", action="store_true", help="09:26 榜单首班 → players.json")
    grp.add_argument("--midday", action="store_true", help="11:30 → stocks/today/players.today")
    grp.add_argument("--close", action="store_true", help="15:15 → 收盘覆盖")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 /tmp/...；拒绝线上路径）")
    ap.add_argument("--fixtures", default="", help="离线 fixture 目录（含 bundle.json）")
    ap.add_argument("--demo", action="store_true", help="用内置样例离线 dry-run")
    ap.add_argument("--live", action="store_true", help="真实 EM 客户端（S2 接入，S1 不启用）")
    ap.add_argument("--date", default="", help="覆盖交易日 YYYY-MM-DD（默认北京当日）")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if kill_switch_active():
        print("[SB_DATA_V2=0] kill switch：no-op，未写任何文件")
        return 0
    out = resolve_out(args.out)
    date = args.date or today_bj()

    if args.rank:
        client = make_client(args, date)
        limit = int(os.environ.get("EM_RANK_LIMIT", str(DEFAULT_RANK_LIMIT)))
        doc = cmd_rank(out, client, date, limit)
        print(f"players.json → {len(doc['players'])} 人 / rankTime={doc['rankTime']} / "
              f"榜单分页 {client.rank_calls()} 次 → {out}")
        return 0

    snapshot = SNAPSHOT_MIDDAY if args.midday else SNAPSHOT_CLOSE
    client = make_client(args, date)
    pages = int(os.environ.get("EM_TRADE_PAGES", str(DEFAULT_TRADE_PAGES)))
    info = cmd_snapshot(out, client, date, snapshot, bj_iso(), pages)
    print(f"stocks.json(snapshot={info['snapshot']}) / today.json / players[].today → "
          f"{info['stats']} 只票 / 调仓 {len(info['tradedPlayerIds'])} 人 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
