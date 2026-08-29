"""
开盘啦(KPL)竞价数据采集器

分层采集竞价抢筹策略所需数据(2026-08-09 实测 19/19 接口可用):
- 环境层: 情绪/连板高度/量能/竞价总额/涨跌家数/昨日涨停表现
- 板块层: 板块竞价异动(GetBKJJ_W36) + 竞价时段板块强度(RealRankingInfo RStart=0925)
- 个股层: 板块成分竞价(GetBKJJBL)/竞价列表(MorningBiddingList)/涨停池(DailyLimitPerformance)
          涨停基因(GetZhangTingGene, 免Token)/竞价分时(GetStockBid)/大单(GetMainMonitor)

落库: 独立 SQLite(auction.db), 按日期当日覆盖, 供回测积累。
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.config import (
    DATA_DIR, KPL_UA, KPL_TOKEN, KPL_USERID,
    KPL_HOST_RT, KPL_HOST_HIS, KPL_HOST_APP, KPL_HOST_LHB, KPL_TIMEOUT,
)

API = "/w1/api/index.php"


class KPLSpider:
    """开盘啦接口封装(统一 UA / 重试 / 超时)"""

    def __init__(self, token: str = KPL_TOKEN, user_id: str = KPL_USERID):
        self.token = token
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": KPL_UA})

    def _get(self, params: Dict[str, Any], host: str = KPL_HOST_RT, retries: int = 2) -> Dict:
        url = host + API
        last_err = None
        for i in range(retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=KPL_TIMEOUT)
                r.raise_for_status()
                data = r.json()
                if data.get("errcode") not in ("0", None):
                    raise ValueError(f"errcode={data.get('errcode')} {data.get('errmsg')}")
                return data
            except Exception as e:
                last_err = e
                time.sleep(0.3 * (i + 1))
        raise RuntimeError(f"KPL GET 失败 {params.get('a')}: {last_err}")

    def _post(self, params: Dict[str, Any], host: str = KPL_HOST_LHB, retries: int = 2) -> Dict:
        url = host + API
        last_err = None
        for i in range(retries + 1):
            try:
                r = self.session.post(url, data=params, timeout=KPL_TIMEOUT)
                r.raise_for_status()
                data = r.json()
                if data.get("errcode") not in ("0", None):
                    raise ValueError(f"errcode={data.get('errcode')} {data.get('errmsg')}")
                return data
            except Exception as e:
                last_err = e
                time.sleep(0.3 * (i + 1))
        raise RuntimeError(f"KPL POST 失败 {params.get('a')}: {last_err}")

    # ---- 环境层 ----

    def env_mood(self) -> Dict:
        """市场情绪/连板高度: ztjs涨停家数 strong情绪值 lbgd连板高度 df_num大幅回撤"""
        return self._get({"a": "ChangeStatistics", "st": 10, "c": "HomeDingPan"}, KPL_HOST_APP)

    def env_capacity(self, date_str: str) -> Dict:
        """市场量能: last最新量(万) s_zrcs昨日量(万)"""
        return self._get({"a": "MarketCapacity", "c": "HisHomeDingPan", "Date": date_str, "Type": 0}, KPL_HOST_HIS)

    def env_bid_total(self, date_str: str) -> Dict:
        """竞价总体(历史回放): tJJJE今日竞价总额 lJJJE昨日 tSZ/tXD今日红绿家数 lSZ/lXD昨日"""
        return self._get({"a": "MorningBidding", "c": "HisHomeDingPan", "Date": date_str}, KPL_HOST_HIS)

    def env_bid_total_live(self) -> Dict:
        """竞价总体(当日实时, 无日期参数): tJJJE今日竞价总额 lJJJE昨日 tSZ/tXD今日红绿家数 lSZ/lXD昨日。
        实测 08-13 收盘后仍返回当日数据: tSZ=1142/tXD=4317 → 当天红盘占比可算。"""
        return self._get({"Order": 1, "a": "MorningBidding", "st": 10, "c": "HomeDingPan",
                          "PhoneOSNew": 1, "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3",
                          "VerSion": "5.20.0.2", "Token": self.token, "Index": 0,
                          "apiv": "w41", "UserID": self.user_id}, KPL_HOST_RT)

    def env_bid_count(self, date_str: str) -> Dict:
        """竞价数量统计(历史回放): [涨停委买数, 撮合>2000w数, 热门股数, 主力净额>1000w数, 砸盘数]"""
        return self._get({"a": "MorningBiddingNum", "c": "HisHomeDingPan", "Date": date_str}, KPL_HOST_HIS)

    def env_bid_count_live(self) -> Dict:
        """竞价数量统计(当日实时, 无日期): [涨停委买数, 撮合>2000w数, 热门股数, 主力净额>1000w数, 砸盘数]。
        实测 08-13 返回 [210, 220, 50, 66, 1]。"""
        return self._get({"Order": 1, "a": "MorningBiddingNum", "st": 10, "c": "HomeDingPan",
                          "PhoneOSNew": 1, "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3",
                          "VerSion": "5.20.0.2", "Token": self.token, "Index": 0,
                          "apiv": "w41", "UserID": self.user_id}, KPL_HOST_RT)

    def env_zt_expression(self, date_str: str) -> Dict:
        """昨日涨停今日表现(字段含义待文档, 原样记录)"""
        return self._get({"a": "ZhangTingExpression", "c": "HisHomeDingPan", "Day": date_str}, KPL_HOST_HIS)

    # ---- 板块层 ----

    def board_bid(self, date_str: str) -> Dict:
        """板块竞价异动(历史回放, His 只服务已完成交易日): List1今日新增/List2昨日延续/List3其他
        每条 [板块代码, 名称, 竞价爆量倍数, 异动金额, 预留, 主力净额]"""
        return self._get({"a": "GetBKJJ_W36", "c": "StockBidYiDong", "Day": date_str.replace("-", ""),
                          "Order": 1, "Type": 0,
                          "Token": self.token, "UserID": self.user_id}, KPL_HOST_HIS)

    def board_bid_live(self) -> Dict:
        """板块竞价异动(当日实时, 无 Day 参数): 参考 LowellLee/kpl get_bkjj 调用方式。
        仅竞价时段有数据; 收盘后清空。"""
        return self._get({"a": "GetBKJJ_W36", "c": "StockBidYiDong", "PhoneOSNew": 1,
                          "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3", "VerSion": "5.20.0.8",
                          "Token": self.token, "apiv": "w41", "UserID": self.user_id}, KPL_HOST_RT)

    def board_ranking(self, date_str: str, r_start: str = "0925", r_end: str = "0930",
                      zs_type: int = 7, page_size: int = 60) -> Dict:
        """板块强度(历史回放, His 只服务已完成交易日): 19列 [代码,名称,强度,涨幅,涨速,成交额,主力净额,...]"""
        return self._get({"Order": 1, "a": "RealRankingInfo", "st": page_size, "c": "ZhiShuRanking",
                          "PhoneOSNew": 1, "RStart": r_start, "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3",
                          "VerSion": "5.20.0.2", "Index": 0, "Date": date_str, "REnd": r_end,
                          "apiv": "w41", "Type": 5, "ZSType": zs_type}, KPL_HOST_HIS)

    def board_ranking_live(self, page_size: int = 60, zs_type: int = 7) -> Dict:
        """板块强度(当日实时, 无 Date): 参考 LowellLee/kpl get_top_sectors_realtime(Type=1/apiv=w26)。
        按强度排序, 19列与历史变体一致。仅竞价时段有数据。"""
        return self._get({"Order": 1, "a": "RealRankingInfo", "st": page_size, "apiv": "w26",
                          "Type": 1, "c": "ZhiShuRanking", "PhoneOSNew": 1,
                          "DeviceID": "20ad85ca-becb-3bed-b3d4-30032a0f5923",
                          "Index": 0, "ZSType": zs_type}, KPL_HOST_RT)

    # ---- 个股层 ----

    def board_stocks(self, plate_id: str, date_str: str, st: int = 50, filter_: int = 3) -> Dict:
        """板块内股票竞价(竞价量比/涨幅/净额/换手/流通市值/标记)
        每条 [代码,名称,现价,实时涨幅,竞价量比,竞价额,竞价涨幅,竞价净额,竞价换手,流通市值,板块标签,预留,预留,标记]"""
        return self._get({"a": "GetBKJJBL", "c": "StockBidYiDong", "Day": date_str.replace("-", ""),
                          "StockID": plate_id, "Index": 0, "Order": 1, "Type": 1,
                          "IsLB": 0, "IsZT": 0, "Isst": 1, "filter": filter_, "st": st,
                          "Token": self.token, "UserID": self.user_id}, KPL_HOST_HIS)

    def board_stocks_live(self, plate_id: str, st: int = 50, filter_: int = 1) -> Dict:
        """板块内股票竞价(当日实时, 无 Day): 竞价量比/涨幅/净额/换手/流通市值/板块。
        实测 GetBKJJBL 实时变体收盘后仍返回当日数据(20 行), 竞价量比当天可拿到。
        每条 [代码,名称,现价,实时涨幅,量比,竞价金额,竞价涨幅,竞价大单净额,竞价换手,流通市值,板块]"""
        return self._get({"a": "GetBKJJBL", "c": "StockBidYiDong", "PhoneOSNew": 1,
                          "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3", "VerSion": "5.20.0.8",
                          "IsLB": 0, "IsZT": 0, "Isst": 1, "filter": filter_, "st": st,
                          "Token": self.token, "Index": 0, "apiv": "w41",
                          "Type": 1, "StockID": plate_id, "UserID": self.user_id}, KPL_HOST_RT)

    def bid_list(self, pid_type: int = 0, type_: int = 4, st: int = 100) -> Dict:
        """竞价列表(当日实时, 免Token, 无日期参数): PidType 0涨停委买/1撮合>2000w/2热门/3主力净额>1000w/4砸盘
        每条 [代码,名称,竞价价,竞价涨幅,涨停委买额,连板数,竞价净额,竞价换手,主力净额,主力买,主力卖,板块标签,流通市值,...,tag]
        ⚠️ 仅竞价时段(约 09:15-09:25)有数据; 收盘后清空。历史回放无数据(实时接口)。
        参考 LowellLee/kpl get_morning_bidding(c=HomeDingPan, RT host, 不传 Date)。"""
        return self._get({"Order": 1, "a": "MorningBiddingList", "st": st, "c": "HomeDingPan",
                          "PhoneOSNew": 1, "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3",
                          "VerSion": "5.20.0.2", "Token": self.token, "Index": 0,
                          "PidType": pid_type, "apiv": "w41", "Type": type_, "UserID": self.user_id},
                         KPL_HOST_RT)

    def zt_pool(self, date_str: str, pid_type: int = 1, st: int = 500) -> Dict:
        """涨停板列表(免Token): PidType 1一板~5五板及以上
        每条 [代码,名称,...,涨停时间戳,涨停原因,封单额,最大封单,主力净额,主力买,主力卖,成交额,板块,流通市值,...,连板标记]"""
        return self._get({"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "Day": date_str,
                          "PidType": pid_type, "Type": 4, "Index": 0, "Order": 0, "st": st}, KPL_HOST_HIS)

    def zt_gene(self, stock_id: str) -> List:
        """涨停基因(免Token): [涨停次数, 5%溢价次, 次日红盘%, 首板封板率%, 首板破板率%, 连板率%]"""
        data = self._get({"a": "GetZhangTingGene", "apiv": "w42", "c": "StockL2Data", "StockID": stock_id,
                          "PhoneOSNew": 1, "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3",
                          "VerSion": "5.21.0.0"}, KPL_HOST_RT)
        return data.get("List", [])

    def stock_bid(self, stock_id: str) -> Dict:
        """个股竞价分时 9:15-9:25: bid [[时间,价格,买卖方向,累计量],...]"""
        return self._get({"a": "GetStockBid", "c": "StockL2Data", "PhoneOSNew": 1,
                          "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3", "VerSion": "5.20.0.2",
                          "Token": self.token, "apiv": "w41", "StockID": stock_id, "UserID": self.user_id}, KPL_HOST_RT)

    def main_monitor(self, stock_id: str, money: int = 2) -> Dict:
        """大单成交(30万-1000万分档, Money: 0=30万 2=100万 3=300万): 逐笔大单"""
        return self._get({"Order": 0, "st": 20, "a": "GetMainMonitor_w30", "c": "StockYiDongKanPan",
                          "PhoneOSNew": 1, "DeviceID": "00000000-296c-20ad-0000-00003eb74e84",
                          "VerSion": "5.7.0.12", "Token": "4e7fa8458a2add3f14a50ca79e863772",
                          "Index": 0, "Money": money, "apiv": "w31",
                          "StockID": stock_id, "UserID": "1973778", "IsBS": 0}, KPL_HOST_APP)

    def stock_pankou(self, stock_id: str) -> Dict:
        """盘口五档+实时快照(仅当日实时, 无历史): real{last_px,px_change_rate,avg_px,entrust_rate委比,
        vol_ratio,turnover_ratio,up_px涨停价,down_px跌停价,preclose_px...}, weituo{s1..s10/b1..b10五档}"""
        return self._get({"a": "GetStockPanKou", "c": "StockL2Data", "PhoneOSNew": 1,
                          "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3", "VerSion": "5.20.0.2",
                          "Token": self.token, "apiv": "w41", "StockID": stock_id,
                          "UserID": self.user_id}, KPL_HOST_RT)

    def stock_trend(self, stock_id: str, type_: int = 1) -> Dict:
        """个股分时(增量, 仅当日实时): 含分时序列/实时涨幅/竞价成交额, 用于盘中走势与水下拉升识别"""
        return self._get({"a": "GetStockTrendIncremental", "c": "StockL2Data", "PhoneOSNew": 1,
                          "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3", "VerSion": "5.20.0.2",
                          "Token": self.token, "apiv": "w41", "Type": type_,
                          "StockID": stock_id, "UserID": self.user_id}, KPL_HOST_RT)

    def zt_pool_rt(self, pid_type: int = 1, st: int = 500) -> Dict:
        """涨停板列表·当日实时(HomeDingPan, 盘中/盘后均可取当日): 字段同 zt_pool"""
        return self._get({"a": "DailyLimitPerformance", "c": "HomeDingPan", "PhoneOSNew": 1,
                          "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3", "VerSion": "5.18.0.2",
                          "PidType": pid_type, "Type": 4, "Index": 0, "Order": 0, "st": st,
                          "apiv": "w39"}, KPL_HOST_RT)

    def rise_fall_rt(self) -> Dict:
        """涨跌/炸板分析·当日实时: info[0] = [涨停,跌停,自然涨停,曾跌停,破板率,炸板数,日期]"""
        return self._get({"a": "RiseFallAnalysis", "apiv": "w43", "c": "HomeDingPan",
                          "PhoneOSNew": 1}, KPL_HOST_APP)


# =============================================================================
# 落库(独立 auction.db, 按日期当日覆盖)
# =============================================================================

class AuctionStore:
    """竞价数据存储: 独立 SQLite, 与 crawl_data.db 隔离"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (DATA_DIR / "auction.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        # 周期闸门: v5_results 记录选股时点的周期阶段(回测按阶段分组)
        try:
            with self._conn() as c:
                c.execute("ALTER TABLE v5_results ADD COLUMN cycle_stage TEXT")
        except Exception:
            pass  # 列已存在

    def _init_db(self):
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS mood_daily (
                date TEXT PRIMARY KEY, ztjs INTEGER, strong INTEGER, lbgd INTEGER,
                df_num INTEGER, zt_expression TEXT, raw TEXT
            );
            CREATE TABLE IF NOT EXISTS board_bid (
                date TEXT, board_code TEXT, board_name TEXT, list_type TEXT,
                burst REAL, amount REAL, main_net REAL,
                PRIMARY KEY (date, board_code, list_type)
            );
            CREATE TABLE IF NOT EXISTS bid_pool (
                date TEXT, code TEXT, name TEXT, price REAL, change_pct REAL,
                limit_up_buy REAL, bid_pct REAL, bid_net REAL, turnover_ratio REAL,
                main_net REAL, unfilled_buy REAL, plates TEXT, circ_mv REAL, tag TEXT, source TEXT,
                PRIMARY KEY (date, code, source)
            );
            CREATE TABLE IF NOT EXISTS limit_pool (
                date TEXT, code TEXT, name TEXT, pid_type INTEGER,
                zt_time INTEGER, reason TEXT, seal_amount REAL, max_seal REAL,
                main_net REAL, amount REAL, plates TEXT, circ_mv REAL, tag TEXT,
                PRIMARY KEY (date, code)
            );
            CREATE TABLE IF NOT EXISTS gene_daily (
                date TEXT, code TEXT, limit_count INTEGER, premium_5pct INTEGER,
                next_red_pct REAL, seal_pct REAL, break_pct REAL, consecutive_pct REAL,
                PRIMARY KEY (date, code)
            );
            CREATE TABLE IF NOT EXISTS candidates (
                date TEXT, code TEXT, name TEXT, tier TEXT,
                score REAL, max_score INTEGER,
                s1 INTEGER, s2 INTEGER, s3 INTEGER, s4 INTEGER, s5 INTEGER, s6 INTEGER,
                s7 REAL, fused_score REAL,
                s8 INTEGER, s9 INTEGER, unfilled_buy REAL,
                bid_price REAL, bid_pct REAL, bid_net REAL, turnover REAL,
                vol_ratio REAL, circ_mv REAL, bid_vol_last REAL,
                bid_buy_ratio REAL, bid_vol_total REAL,
                ma60_above INTEGER, ret20 REAL, macd_ok INTEGER, kdj_ok INTEGER,
                tag TEXT, boards TEXT, seal_pct REAL, resonance INTEGER,
                rank_in_day INTEGER,
                PRIMARY KEY (date, code)
            );
            CREATE TABLE IF NOT EXISTS candidate_results (
                date TEXT, code TEXT,
                open_px REAL, high_px REAL, low_px REAL, close_px REAL,
                pct_open REAL, pct_bid REAL, pct_day REAL,
                pct_open_day REAL, pct_e31 REAL,
                role TEXT,  -- NULL=候选(core/watch) / control=随机池基准 / rejected_fade=高开低走被拒组
                PRIMARY KEY (date, code)
            );
            CREATE TABLE IF NOT EXISTS funnel_rejected (
                date TEXT, code TEXT, name TEXT, reason TEXT,
                PRIMARY KEY (date, code)
            );
            CREATE TABLE IF NOT EXISTS bid_series (
                date TEXT, code TEXT, name TEXT,
                series TEXT,
                PRIMARY KEY (date, code)
            );
            CREATE TABLE IF NOT EXISTS v5_results (
                date TEXT, code TEXT,
                name TEXT,
                -- 选股时点快照(9:25 定格)
                bid_pct REAL, turnover REAL, circ_mv REAL,
                prev_pct REAL, height INTEGER,
                was_limit INTEGER, fade INTEGER, half_pos INTEGER,
                pos_tag TEXT,             -- main=主攻3万 / sub=次攻2.4万 / NULL=普通
                group_tag TEXT,           -- v5=候选 / v5_rej_turn=换手不足被拒 / v5_rej_mv=市值不足被拒
                boards TEXT,
                -- 收盘后打标(T+0 当日)
                open_px REAL, close_px REAL, pct_open REAL,   -- 开盘买→当日收(V5 主口径)
                pct_day REAL,             -- 当日收 vs 昨收
                -- 次日打标(T+1, 卖出纪律执行日; 昨日强组 vs 低位组的关键分野)
                next_date TEXT,
                next_open_pct REAL,       -- 次日开盘 vs 当日收(隔夜跳空)
                next_close_pct REAL,      -- 次日收盘 vs 当日开盘(次日持有收益, V5 次日兑现口径)
                next_stop_hit INTEGER,    -- 次日盘中最低是否触及 -3% 止损线(相对买入价)
                labeled_at TEXT,
                PRIMARY KEY (date, code)
            );
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_bid_pool_date ON bid_pool(date)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_limit_pool_date ON limit_pool(date)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_candidates_date ON candidates(date)")
            # 迁移: 旧库 candidates 表缺融合/文章因子列时 ALTER 补列(幂等)
            _have = {r[1] for r in c.execute("PRAGMA table_info(candidates)")}
            for _col, _ddl in {
                "s7": "REAL", "fused_score": "REAL",
                "bid_buy_ratio": "REAL", "bid_vol_total": "REAL",
                "ma60_above": "INTEGER", "ret20": "REAL",
                "macd_ok": "INTEGER", "kdj_ok": "INTEGER",
                "s8": "INTEGER", "s9": "INTEGER", "unfilled_buy": "REAL",
            }.items():
                if _col not in _have:
                    c.execute(f"ALTER TABLE candidates ADD COLUMN {_col} {_ddl}")
            # 迁移: 旧库 bid_pool 表补 unfilled_buy 列(幂等)
            _have_pool = {r[1] for r in c.execute("PRAGMA table_info(bid_pool)")}
            if "unfilled_buy" not in _have_pool:
                c.execute("ALTER TABLE bid_pool ADD COLUMN unfilled_buy REAL")
            # 迁移: 旧库 candidate_results 补 pct_day/pct_open_day/pct_e31/role 列(幂等)
            _have_res = {r[1] for r in c.execute("PRAGMA table_info(candidate_results)")}
            for _col, _ddl in {"pct_day": "REAL", "pct_open_day": "REAL",
                               "pct_e31": "REAL", "role": "TEXT"}.items():
                if _col not in _have_res:
                    c.execute(f"ALTER TABLE candidate_results ADD COLUMN {_col} {_ddl}")

    @staticmethod
    def _validate_date(date_str):
        """数据日期合法性校验(2026-08-23 审计后新增):
        1. 格式必须 YYYY-MM-DD(防 None/'0'/畸形串落库);
        2. 不允许晚于今天(防时钟错乱/接口回传未来日期);
        3. 距今超过3天的历史日期拒绝写入 —— 更早历史属于回填工具
           (verify_dragon.py --start/--end)的职责; 日常 scan 只服务最近3天竞价快照。
        校验失败 raise ValueError。"""
        import re as _re
        from datetime import date as _date, datetime as _dt
        s = str(date_str or "")
        if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            raise ValueError(f"数据日期格式非法: {date_str!r}(应为 YYYY-MM-DD)")
        try:
            d = _dt.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"数据日期不是有效日历日: {s!r}")
        today = _date.today()
        if d > today:
            raise ValueError(f"数据日期在未来: {s} > 今天{today.isoformat()}, 疑似时钟/接口错乱, 拒绝写入")
        if (today - d).days > 3:
            raise ValueError(
                f"数据日期过旧: {s}(距今{(today - d).days}天)。"
                f"scan 只写最近3天的竞价快照; 历史回填请用 verify_dragon.py --start/--end")

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def save_mood(self, date_str: str, mood: Dict, capacity: Dict, bid_total: Dict,
                  bid_count: List, zt_expr: Dict):
        self._validate_date(date_str)
        with self._conn() as c:
            info = (mood.get("info") or [{}])[0]
            c.execute("""INSERT OR REPLACE INTO mood_daily
                (date, ztjs, strong, lbgd, df_num, zt_expression, raw) VALUES (?,?,?,?,?,?,?)""",
                (date_str, int(info.get("ztjs") or 0), int(info.get("strong") or 0),
                 int(info.get("lbgd") or 0), int(info.get("df_num") or 0),
                 json.dumps({"capacity": capacity.get("info"), "bid_total": bid_total.get("info"),
                             "bid_count": bid_count, "zt_expr": zt_expr.get("info")}, ensure_ascii=False),
                 json.dumps({"mood": mood, "capacity": capacity, "bid_total": bid_total,
                             "bid_count": bid_count, "zt_expr": zt_expr}, ensure_ascii=False)))

    def save_board_bid(self, date_str: str, data: Dict):
        self._validate_date(date_str)
        with self._conn() as c:
            for lt, key in (("L1", "List1"), ("L2", "List2"), ("L3", "List3")):
                for row in data.get(key, []):
                    if len(row) < 6:
                        continue
                    c.execute("""INSERT OR REPLACE INTO board_bid
                        (date, board_code, board_name, list_type, burst, amount, main_net)
                        VALUES (?,?,?,?,?,?,?)""",
                        (date_str, row[0], row[1], lt, float(row[2]), float(row[3]), float(row[5])))

    def save_bid_pool(self, date_str: str, rows: List[List], source: str):
        self._validate_date(date_str)
        with self._conn() as c:
            for r in rows:
                if len(r) < 13:
                    continue
                c.execute("""INSERT OR REPLACE INTO bid_pool
                    (date, code, name, price, change_pct, limit_up_buy, bid_pct, bid_net,
                     turnover_ratio, main_net, unfilled_buy, plates, circ_mv, tag, source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (date_str, r[0], r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0),
                     float(r[5] or 0), float(r[6] or 0), float(r[7] or 0), float(r[8] or 0),
                     float(r[9] or 0) if len(r) > 9 else 0,
                     str(r[11] or ""), float(r[12] or 0), str(r[16] or ""), source))

    def save_bid_series(self, date_str: str, stock_bids: Dict[str, List], pool: Dict = None):
        """保存 GetStockBid 原始竞价分时(每只票整段 [时间,价,方向,累计量] 序列, JSON)。
        回测时从 series 可重算 S2/S3/委比代理/竞价量, 不必依赖扫描时快照。
        stock_bids: {code: [[time,px,dir,cum_vol],...]} 或 {code: {"code":..,"name":..,"bid":[...]}}
        pool: 候选池(取 name, 可选)"""
        import json
        self._validate_date(date_str)
        with self._conn() as c:
            for code, v in stock_bids.items():
                if isinstance(v, dict):
                    bid = v.get("bid") or []
                    name = v.get("name") or ""
                else:
                    bid = v or []
                    name = (pool or {}).get(code, {}).get("name", "") if pool else ""
                if not bid:
                    continue
                c.execute("""INSERT OR REPLACE INTO bid_series
                    (date, code, name, series) VALUES (?,?,?,?)""",
                    (date_str, code, name, json.dumps(bid, ensure_ascii=False)))

    def save_limit_pool(self, date_str: str, groups: List[tuple]):
        """groups: [(pid_type, rows), ...] — DailyLimitPerformance 每个 PidType 的 info
        可能含多个分组数组, 需先展平再按 PidType 落库"""
        self._validate_date(date_str)
        with self._conn() as c:
            for pid_type, rows in groups:
                for r in rows:
                    if len(r) < 14:
                        continue
                    c.execute("""INSERT OR REPLACE INTO limit_pool
                        (date, code, name, pid_type, zt_time, reason, seal_amount, max_seal,
                         main_net, amount, plates, circ_mv, tag)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (date_str, r[0], r[1], pid_type, int(r[4] or 0), str(r[5] or ""),
                         float(r[6] or 0), float(r[7] or 0), float(r[8] or 0), float(r[11] or 0),
                         str(r[12] or ""), float(r[13] or 0), str(r[18] if len(r) > 18 else "")))

    def save_genes(self, date_str: str, genes: Dict[str, List]):
        self._validate_date(date_str)
        with self._conn() as c:
            for code, g in genes.items():
                if len(g) < 6:
                    continue
                c.execute("""INSERT OR REPLACE INTO gene_daily
                    (date, code, limit_count, premium_5pct, next_red_pct, seal_pct, break_pct, consecutive_pct)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (date_str, code, int(g[0]), int(g[1]), float(g[2]), float(g[3]),
                     float(g[4]), float(g[5])))

    def save_candidates(self, date_str: str, candidates: List[Dict], watch: List[Dict]):
        """落库当日选股(core/watch 两层) + S1-S6 因子分 + 原始因子(回测输入)。
        候选 dict 来自 run_funnel: {code,name,score,tier,max,factors,sub,gene,boards,tag,resonance}"""
        self._validate_date(date_str)
        with self._conn() as c:
            # 日期隔离: 每次扫描产出完整名单, 先清当日旧行, 避免 INSERT OR REPLACE 残留上次名单多出/剔除的 code
            c.execute("DELETE FROM candidates WHERE date=?", (date_str,))
            for tier, arr in (("core", candidates), ("watch", watch)):
                for rank, item in enumerate(arr, 1):
                    f_ = item.get("factors") or {}
                    s_ = item.get("sub") or {}
                    g_ = (item.get("gene") or {}).get("data") or {}
                    c.execute("""INSERT OR REPLACE INTO candidates
                        (date, code, name, tier, score, max_score,
                         s1, s2, s3, s4, s5, s6, s7, fused_score,
                         s8, s9, unfilled_buy,
                         bid_price, bid_pct, bid_net, turnover, vol_ratio, circ_mv, bid_vol_last,
                         bid_buy_ratio, bid_vol_total,
                         ma60_above, ret20, macd_ok, kdj_ok,
                         tag, boards, seal_pct, resonance, rank_in_day)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (date_str, item["code"], item.get("name") or "", tier,
                         float(item.get("score") or 0), int(item.get("max") or 21),
                         int(s_.get("S1资金") or 0), int(s_.get("S2形态") or 0),
                         int(s_.get("S3共振") or 0), int(s_.get("S4身位") or 0),
                         int(s_.get("S5量比") or 0), int(s_.get("S6基因") or 0),
                         item.get("s7"), item.get("fused_score"),
                         int(s_.get("S8撮合") or 0), int(s_.get("S9委买") or 0),
                         f_.get("unfilled_buy"),
                         f_.get("bid_price"), f_.get("bid_pct"), f_.get("bid_net"),
                         f_.get("turnover"), f_.get("vol_ratio"), f_.get("circ_mv"),
                         f_.get("bid_vol_last"),
                         f_.get("bid_buy_ratio"), f_.get("bid_vol_total"),
                         f_.get("ma60_above"), f_.get("ret20"),
                         f_.get("macd_ok"), f_.get("kdj_ok"),
                         item.get("tag") or "",
                         ",".join(item.get("boards") or []), g_.get("seal_pct"),
                         int(item.get("resonance") or 0), rank))

    def save_candidate_result(self, date_str: str, code: str, open_px, high_px, low_px,
                              close_px, pct_open, pct_bid, pct_day=None,
                              pct_open_day=None, pct_e31=None, role=None):
        """写入单只当日实际表现(candidate_results), --label 结果标签用
        pct_bid = 收盘相对竞价价(策略口径); pct_day = 收盘相对昨收(当天涨跌幅);
        pct_open_day = 收盘相对开盘价(开盘买入); pct_e31 = 收盘相对 09:31 价(E层确认入场)
        role: NULL=候选 / control=随机池基准 / rejected_fade=高开低走被拒组(对照组)"""
        self._validate_date(date_str)
        with self._conn() as c:
            c.execute("""INSERT OR REPLACE INTO candidate_results
                (date, code, open_px, high_px, low_px, close_px, pct_open, pct_bid, pct_day,
                 pct_open_day, pct_e31, role)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (date_str, code, open_px, high_px, low_px, close_px, pct_open, pct_bid, pct_day,
                 pct_open_day, pct_e31, role))

    def save_rejected(self, date_str: str, rows: List[Dict]):
        """落库漏斗被拒明细(funnel_rejected), 对照组(高开低走/对倒/资金不足)打标基础"""
        self._validate_date(date_str)
        with self._conn() as c:
            c.execute("DELETE FROM funnel_rejected WHERE date=?", (date_str,))
            c.executemany("""INSERT OR REPLACE INTO funnel_rejected (date, code, name, reason)
                             VALUES (?,?,?,?)""",
                          [(date_str, r["code"], r["name"], r.get("reason") or "") for r in rows])

    def save_v5_results(self, date_str: str, rows: List[Dict]):
        """落库 V5 当日名单快照(v5_results), 含选股时点因子与分组标签。
        幂等: 先 DELETE 当日再整批写入(重跑覆盖)。"""
        self._validate_date(date_str)
        with self._conn() as c:
            c.execute("DELETE FROM v5_results WHERE date=?", (date_str,))
            c.executemany("""INSERT OR REPLACE INTO v5_results
                (date, code, name, bid_pct, turnover, circ_mv,
                 prev_pct, height, was_limit, fade, half_pos,
                 pos_tag, group_tag, boards, cycle_stage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(date_str, r["code"], r["name"], r.get("bid_pct"), r.get("turnover"),
                  r.get("circ_mv"), r.get("prev_pct"), r.get("height"),
                  int(bool(r.get("was_limit"))), int(bool(r.get("fade"))),
                  int(bool(r.get("half_pos"))), r.get("pos_tag"), r.get("group_tag"),
                  ",".join(r.get("boards") or []), r.get("cycle_stage")) for r in rows])

    def load_v5_results(self, date_str: str) -> List[Dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM v5_results WHERE date=? ORDER BY turnover DESC", (date_str,))]

    def label_v5_result(self, date_str: str, code: str, open_px=None, close_px=None,
                        pct_open=None, pct_day=None):
        """T+0 打标: 更新单只 V5 结果的当日行情(只更新列, 不动选股快照)"""
        self._validate_date(date_str)
        with self._conn() as c:
            c.execute("""UPDATE v5_results SET
                open_px=?, close_px=?, pct_open=?, pct_day=?, labeled_at=datetime('now','localtime')
                WHERE date=? AND code=?""",
                (open_px, close_px, pct_open, pct_day, date_str, code))

    def label_v5_next(self, date_str: str, code: str, next_date, next_open_pct=None,
                      next_close_pct=None, next_stop_hit=None):
        """T+1 打标: 次日开盘/收盘表现 + 是否触及-3%止损(卖出纪律验证核心字段)"""
        self._validate_date(date_str)
        with self._conn() as c:
            c.execute("""UPDATE v5_results SET
                next_date=?, next_open_pct=?, next_close_pct=?, next_stop_hit=?,
                labeled_at=datetime('now','localtime')
                WHERE date=? AND code=?""",
                (next_date, next_open_pct, next_close_pct, next_stop_hit, date_str, code))

    def load_bid_pool(self, date_str: str) -> List[Dict]:
        """读取当日候选池(bid_pool), 对照组抽样用"""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM bid_pool WHERE date=?", (date_str,)).fetchall()
            return [dict(r) for r in rows]

    def load_rejected(self, date_str: str) -> List[Dict]:
        """读取当日漏斗被拒明细(funnel_rejected), 高开低走被拒组打标用"""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM funnel_rejected WHERE date=?", (date_str,)).fetchall()
            return [dict(r) for r in rows]

    def load_candidates(self, date_str: str) -> List[Dict]:
        """按日期读取当日候选(core/watch), --label 结果标签用"""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM candidates WHERE date=? ORDER BY tier, rank_in_day",
                             (date_str,)).fetchall()
            return [dict(r) for r in rows]

    def load_bid_pool(self, date_str: str) -> List[Dict]:
        self._validate_date(date_str)
        with self._conn() as c:
            rows = c.execute("SELECT * FROM bid_pool WHERE date=?", (date_str,)).fetchall()
            return [dict(r) for r in rows]

    def load_limit_pool(self, date_str: str) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM limit_pool WHERE date=?", (date_str,)).fetchall()
            return [dict(r) for r in rows]


class HotRankStore:
    """东财人气榜快照存储(独立 hot_rank.db):
    auction.yml(09:25 am)与 crawl.yml(13:05 pm)双 workflow 写入,独立文件避免提交冲突。
    一天最多两份快照;同 (date, snap) 重复写入自动跳过。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (DATA_DIR / "hot_rank.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS hot_rank (
                date TEXT, snap TEXT, rank INTEGER,
                code TEXT, name TEXT, rise INTEGER,
                crawled_at TEXT,
                PRIMARY KEY (date, snap, rank)
            )""")

    @staticmethod
    def _validate_date(date_str: str):
        """与 AuctionStore 同规则:格式合法、不晚于今天、距今≤3天"""
        import re as _re
        from datetime import date as _date, datetime as _dt
        s = str(date_str or "")
        if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            raise ValueError(f"数据日期格式非法: {date_str!r}")
        try:
            d = _dt.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"数据日期不是有效日历日: {s!r}")
        today = _date.today()
        if d > today:
            raise ValueError(f"数据日期在未来: {s} > {today.isoformat()}")
        if (today - d).days > 3:
            raise ValueError(f"数据日期过旧: {s}(scan 只写最近3天)")

    def has_snapshot(self, date_str: str, snap: str) -> bool:
        self._validate_date(date_str)
        with sqlite3.connect(self.db_path) as c:
            return c.execute("SELECT 1 FROM hot_rank WHERE date=? AND snap=? LIMIT 1",
                             (date_str, snap)).fetchone() is not None

    def save_hot_rank(self, date_str: str, snap: str, rows: List[Dict]) -> bool:
        """写入一份快照(rows: rank/code/name/rise);已存在同(date,snap)则跳过返回 False"""
        self._validate_date(date_str)
        assert snap in ("am", "pm"), f"snap 非法: {snap}"
        if self.has_snapshot(date_str, snap):
            return False
        with sqlite3.connect(self.db_path) as c:
            c.executemany("""INSERT OR REPLACE INTO hot_rank
                (date, snap, rank, code, name, rise, crawled_at) VALUES (?,?,?,?,?,?,datetime('now','localtime'))""",
                [(date_str, snap, r["rank"], r["code"], r["name"], r.get("rise") or 0) for r in rows])
        return True

    def load_hot_rank(self, date_str: str, snap: Optional[str] = None) -> List[Dict]:
        with sqlite3.connect(self.db_path) as c:
            c.row_factory = sqlite3.Row
            if snap:
                rows = c.execute("SELECT * FROM hot_rank WHERE date=? AND snap=? ORDER BY rank",
                                 (date_str, snap)).fetchall()
            else:
                rows = c.execute("SELECT * FROM hot_rank WHERE date=? ORDER BY snap, rank",
                                 (date_str,)).fetchall()
            return [dict(r) for r in rows]
