#!/usr/bin/env python3
"""盯盘 watchdog 单测: 去重/追加、叠加合并、状态跨日翻篇、降级计数、交易时段闸门、日报共享去重。

运行: python3 scripts/test_watchdog.py
"""
import email.utils
import json
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import watchdog  # noqa: E402
import notify_daily  # noqa: E402
from notify_daily import load_watchdog_pushed  # noqa: E402


def _t(code, dr, cnt, price=10.0, name="测试股"):
    return {"stock_code": code, "stock_name": name, "direction": dr,
            "trades_count": cnt, "position_ratio": "9成以上", "price": price,
            "trade_date": "2026-09-17"}


class TestDecidePushes(unittest.TestCase):
    def test_new_op_pushed(self):
        pushed = {}
        new, known = watchdog.decide_pushes([_t("600371", "买入", 1)], pushed)
        self.assertEqual(len(new), 1)
        self.assertFalse(new[0][1])                      # 非追加
        self.assertEqual(known, [])
        self.assertEqual(pushed["600371"]["买入"], 1)     # 已记次数

    def test_same_count_suppressed(self):
        """次数不变 → 不推, 但进 known(消息里作台账展示, 不带 🆕)。"""
        pushed = {"600371": {"买入": 1}}
        new, known = watchdog.decide_pushes([_t("600371", "买入", 1)], pushed)
        self.assertEqual(new, [])
        self.assertEqual(len(known), 1)

    def test_count_growth_is_add(self):
        """盘中补量/回买: 次数增加 → 补推"追加"(盲区修正)。"""
        pushed = {"600371": {"买入": 1}}
        new, known = watchdog.decide_pushes([_t("600371", "买入", 2)], pushed)
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0][1])
        self.assertEqual(known, [])
        self.assertEqual(pushed["600371"]["买入"], 2)

    def test_directions_independent(self):
        pushed = {"600371": {"买入": 2}}
        new, known = watchdog.decide_pushes([_t("600371", "卖出", 1)], pushed)
        self.assertEqual(len(new), 1)                    # 卖出首次出现, 与买入互不影响

    def test_count_never_regresses(self):
        pushed = {"600371": {"买入": 3}}
        watchdog.decide_pushes([_t("600371", "买入", 1)], pushed)
        self.assertEqual(pushed["600371"]["买入"], 3)


class TestMergePlayer(unittest.TestCase):
    def setUp(self):
        self.base = {"id": "900461598", "name": "大道可寻",
                     "p": [{"sn": "旧股", "sc": "000001", "cp": 1, "np": 1, "pr": 0, "rr": 0}],
                     "t": [{"td": "2026-09-16", "dr": "买入", "sn": "昨日股", "sc": "000002",
                            "tc": 1, "rr": "9成以上", "pr": 10.0, "_id": 7, "_k": "abc"},
                           {"td": "2026-09-17", "dr": "买入", "sn": "旧快照", "sc": "000003",
                            "tc": 1, "rr": "5成", "pr": 9.0, "_id": 8, "_k": "old"}],
                     "i": [{"sn": "推测"}]}
        self.positions = [{"stock_name": "万向德农", "stock_code": "600371",
                           "cost_price": 13.13, "current_price": 15.64,
                           "profit_ratio": 19.1, "position_ratio": 96.0}]
        self.today = [_t("600371", "买入", 1, 13.13)]

    def test_history_preserved_today_replaced(self):
        m = watchdog.merge_player(self.base, self.positions, self.today, "2026-09-17")
        tds = [x["td"] for x in m["t"]]
        self.assertEqual(tds, ["2026-09-17", "2026-09-16"])   # 新在前, 按日期倒序
        self.assertEqual(len(m["t"]), 2)                      # 旧的"今日"行被实时行替换
        self.assertEqual(m["t"][0]["sn"], "测试股")            # 今日行来自实时接口
        self.assertEqual(m["t"][1]["_k"], "abc")              # 历史 _k/_id 原样保留
        self.assertEqual(m["p"][0]["sn"], "万向德农")          # p 整体替换为实时持仓
        self.assertEqual(m["i"], [{"sn": "推测"}])             # i 无 DB 不重算, 保留底稿
        self.assertEqual((m["id"], m["name"]), ("900461598", "大道可寻"))


class TestStateRollover(unittest.TestCase):
    def test_date_mismatch_gives_fresh_state(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "st.json"
            f.write_text(json.dumps({"date": "2026-09-16", "pushed": {"x": {}},
                                     "fails": 3, "alerted": 1}), encoding="utf-8")
            st = watchdog.load_state("2026-09-17", f)
            self.assertEqual(st["date"], "2026-09-17")
            self.assertEqual(st["pushed"], {})
            self.assertEqual((st["fails"], st["alerted"]), (0, 0))

    def test_missing_file_gives_fresh_state(self):
        with tempfile.TemporaryDirectory() as td:
            st = watchdog.load_state("2026-09-17", Path(td) / "none.json")
            self.assertEqual(st, watchdog.fresh_state("2026-09-17"))


class TestSeedBaseline(unittest.TestCase):
    def test_seeds_only_today_rows_with_max_count(self):
        pushed = {}
        base = {"t": [{"td": "2026-09-17", "dr": "买入", "sc": "600371", "tc": 2},
                      {"td": "2026-09-17", "dr": "卖出", "sc": "600371", "tc": 1},
                      {"td": "2026-09-16", "dr": "买入", "sc": "000002", "tc": 5}]}
        n = watchdog.seed_baseline(pushed, base, "2026-09-17")
        self.assertEqual(n, 2)
        self.assertEqual(pushed, {"600371": {"买入": 2, "卖出": 1}})


class TestBaseSpoiled(unittest.TestCase):
    """底稿防剧透判定(2026-11-11): 今日部署的底稿不得作"已推"基线,
    否则 crawl 先于 watchdog 部署的当日操作会被 seed 永久静默(月儿兰事故)。"""

    BJ = ZoneInfo("Asia/Shanghai")

    def _lm_gmt(self, y, mo, d, h, mi):
        """构造 RFC1123 GMT 串(Pages Last-Modified 同格式)。"""
        return email.utils.format_datetime(
            datetime(y, mo, d, h, mi, tzinfo=timezone.utc))

    def _run(self, lm, fixed_bj):
        with unittest.mock.patch.object(watchdog, "bj_now", return_value=fixed_bj):
            return watchdog.base_is_spoiled(lm)

    def test_今日盘中部署判剧透(self):
        # crawl 09:52 部署(GMT 01:52) → 北京今日 → 不得 seed
        fixed = datetime(2026, 11, 11, 10, 21, tzinfo=self.BJ)
        self.assertTrue(self._run(self._lm_gmt(2026, 11, 11, 1, 52), fixed))

    def test_昨日部署不判剧透(self):
        # 11-10 15:15 EOD 部署(GMT 07:15) → 北京昨日 → 正常 seed
        fixed = datetime(2026, 11, 11, 10, 21, tzinfo=self.BJ)
        self.assertFalse(self._run(self._lm_gmt(2026, 11, 10, 7, 15), fixed))

    def test_空header保持现行为(self):
        self.assertFalse(self._run("", datetime(2026, 11, 11, tzinfo=self.BJ)))

    def test_解析失败保持现行为(self):
        self.assertFalse(self._run("not-a-date", datetime(2026, 11, 11, tzinfo=self.BJ)))

    def test_gmt跨日按北京日期判定(self):
        # GMT 11-10 17:30 = 北京 11-11 01:30 → 属北京今日 → 判剧透
        fixed = datetime(2026, 11, 11, 9, 36, tzinfo=self.BJ)
        self.assertTrue(self._run(self._lm_gmt(2026, 11, 10, 17, 30), fixed))


class TestTradingWindow(unittest.TestCase):
    def _dt(self, wd, hm):
        # 2026-09-14 是周一; 用任意周一构造
        return datetime(2026, 9, 14 + wd, int(hm[:2]), int(hm[2:]))

    def test_boundaries(self):
        self.assertTrue(watchdog.in_trading_window(self._dt(0, "0931")))
        self.assertTrue(watchdog.in_trading_window(self._dt(0, "1505")))
        self.assertFalse(watchdog.in_trading_window(self._dt(0, "0930")))
        self.assertFalse(watchdog.in_trading_window(self._dt(0, "1506")))
        self.assertFalse(watchdog.in_trading_window(self._dt(5, "1000")))   # 周六
        self.assertFalse(watchdog.in_trading_window(self._dt(6, "1000")))   # 周日

    def test_lunch_break_excluded(self):
        """午休 11:30-13:00 不开机(2026-09-18 用户拍板): 期间无成交, 空烧 runner。"""
        self.assertFalse(watchdog.in_trading_window(self._dt(0, "1130")))
        self.assertFalse(watchdog.in_trading_window(self._dt(0, "1200")))
        self.assertFalse(watchdog.in_trading_window(self._dt(0, "1259")))
        self.assertTrue(watchdog.in_trading_window(self._dt(0, "1301")))    # 13:00 复盘开盘
        self.assertTrue(watchdog.in_trading_window(self._dt(0, "1100")))    # 午休前正常


class TestDingTalkSharedDedup(unittest.TestCase):
    def test_load_watchdog_pushed(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "wd.json"
            self.assertEqual(load_watchdog_pushed("2026-09-17"), set())   # 文件缺失=兜底
            f.write_text(json.dumps({"date": "2026-09-17",
                                     "pushed": {"900461598": {"600371": {"买入": 1}}}}),
                         encoding="utf-8")
            old = notify_daily.WATCHDOG_STATE
            try:
                notify_daily.WATCHDOG_STATE = f
                self.assertEqual(load_watchdog_pushed("2026-09-17"),
                                 {("900461598", "600371", "买入")})
                self.assertEqual(load_watchdog_pushed("2026-09-18"), set())  # 跨日=兜底
            finally:
                notify_daily.WATCHDOG_STATE = old


class TestVisibilityPath(unittest.TestCase):
    def test_state_path_points_at_tracked_file(self):
        """回归(2026-09-18): STATE 曾多拼一层 jiarenmens → CI 解析到不存在的路径,
        隐藏选手从不被识别(每班白拉), 可见性状态也从未被持久化。"""
        repo = Path(__file__).resolve().parents[2]
        self.assertEqual(notify_daily.WATCHDOG_STATE,
                         repo / "jiarenmens" / "data" / ".watchdog_state.json")
        import src.utils.visibility as vis
        self.assertEqual(vis.STATE,
                         repo / "jiarenmens" / "data" / "player_visibility.json")
        self.assertTrue(vis.STATE.exists())


class TestBuildMessage(unittest.TestCase):
    def test_full_list_format(self):
        """全员列表格式(快报同款): 有新操作出卡并标 🆕, 块间空行, 无操作归 💤, 隐藏归 🔇。"""
        t_a = _t("600371", "买入", 1)
        t_b = _t("000002", "买入", 1)
        data = {"900456476": {"name": "狼之行一", "ok": True, "trades": [t_a], "positions": []},
                "900458047": {"name": "点火的佩斯", "ok": True, "trades": [t_b], "positions": []},
                "900240956": {"name": "股得猫咛", "ok": True, "trades": [], "positions": []},
                "900438148": {"name": "我嘚财富", "hidden": True}}
        new_mark = {"900456476": {id(t_a): False}}   # 只有甲有本轮新增
        msg = watchdog.build_message("2026-09-18", "10:00", data, new_mark, {})
        lines = msg.splitlines()
        i_a = next(i for i, l in enumerate(lines) if "狼之行一" in l)
        i_b = next(i for i, l in enumerate(lines) if "点火的佩斯" in l)
        self.assertEqual(lines[i_b - 1], "", "选手块之间必须有空行")
        self.assertIn("🆕", lines[i_a], "有本轮新增的选手名应标 🆕")
        self.assertNotIn("🆕", lines[i_b], "无新增的选手不标 🆕")
        self.assertTrue(any("🆕" in l and "买入" in l for l in lines[i_a:i_b]))
        self.assertTrue(any("💤 今日暂无操作" in l and "股得猫咛" in l for l in lines))
        self.assertTrue(any("🔇 隐藏号今日暂无操作" in l and "我嘚财富" in l for l in lines))

    def test_hidden_player_with_trades_renders(self):
        """隐藏号调仓走流水通道(2026-11-07): 有当日操作时正常出卡带 🔇 角标,
        不进"暂无操作"脚注; 无 positions 也不叠页。"""
        t_h = {"stock_code": "600838", "stock_name": "上海九百", "direction": "买入",
               "trades_count": 1, "position_ratio": "9成以上", "price": 12.06}
        data = {"900438148": {"name": "我嘚财富", "hidden": True, "ok": True, "trades": [t_h]},
                "900240956": {"name": "股得猫咛", "ok": True, "trades": [], "positions": []}}
        new_mark = {"900438148": {id(t_h): False}}
        msg = watchdog.build_message("2026-11-07", "10:00", data, new_mark, {})
        lines = msg.splitlines()
        i_h = next(i for i, l in enumerate(lines) if "我嘚财富" in l)
        self.assertIn("🔇", lines[i_h], "隐藏号出卡带 🔇 角标")
        self.assertIn("🆕", lines[i_h])
        self.assertTrue(any("买入" in l and "上海九百" in l for l in lines[i_h:i_h+3]))
        self.assertFalse(any("🔇 隐藏号今日暂无操作" in l for l in lines),
                         "隐藏号有操作时不应出现在脚注")

    def test_fetch_hidden_trades_aggregates(self):
        """relocate 流水聚合: 同 (code,方向) 多笔→trades_count 累加, 价格取该方向最新一笔,
        非当日行剔除; 产出与 detail tradeSummary 同形。"""
        rows = [
            {"bizDate": 20261107, "relocateTime": "2026-11-07 10:01:18", "stkCode": "000530",
             "stkName": "冰山冷热", "bsMark": "B", "relocatePrice": "8.710", "positionRatio": "2成"},
            {"bizDate": 20261107, "relocateTime": "2026-11-07 09:47:54", "stkCode": "000530",
             "stkName": "冰山冷热", "bsMark": "B", "relocatePrice": "8.600", "positionRatio": "1成以下"},
            {"bizDate": 20261107, "relocateTime": "2026-11-07 09:49:20", "stkCode": "000530",
             "stkName": "冰山冷热", "bsMark": "S", "relocatePrice": "8.910", "positionRatio": "1成以下"},
            {"bizDate": 20261106, "relocateTime": "2026-11-06 14:00:00", "stkCode": "000530",
             "stkName": "冰山冷热", "bsMark": "B", "relocatePrice": "9.010", "positionRatio": "1成以下"},
        ]
        with unittest.mock.patch.object(watchdog, "_call_api", return_value={"pages": rows}):
            out = watchdog.fetch_hidden_trades("u", "z", "20261107")
        by = {(t["stock_code"], t["direction"]): t for t in out}
        self.assertEqual(by[("000530", "买入")]["trades_count"], 2)
        self.assertEqual(by[("000530", "买入")]["price"], 8.710)   # 首见=最新一笔
        self.assertEqual(by[("000530", "买入")]["position_ratio"], "2成")  # 最新档位先占位
        self.assertEqual(by[("000530", "卖出")]["trades_count"], 1)
        self.assertEqual(len(out), 2, "昨日行不入当日聚合")


class TestHolidayCalendar(unittest.TestCase):
    """交易日历: 单一数据源=前端 tradingCalendar.js(上交所通知), 运行时解析。"""

    def test_parse_holidays_extracts_year_sets(self):
        js = ("const HOLIDAYS_2026 = new Set([\n"
              "  '2026-01-01', // 元旦\n"
              "  '2026-10-01', '2026-10-02', // 国庆\n"
              "])\nconst COVERAGE_YEAR = 2026")
        hol = watchdog.parse_holidays(js)
        self.assertEqual(hol, {2026: {"2026-01-01", "2026-10-01", "2026-10-02"}})

    def test_parse_garbage_gives_empty(self):
        self.assertEqual(watchdog.parse_holidays("不是日历"), {})

    def test_real_calendar_holidays_blocked(self):
        """真实日历: 09-25 中秋(周五)、10-01 国庆休市; 09-18、10-08 开市。"""
        f = lambda s: watchdog.is_trading_day(datetime.strptime(s, "%Y-%m-%d").date())
        self.assertFalse(f("2026-09-25"))   # 中秋(周五)
        self.assertFalse(f("2026-10-01"))   # 国庆(周四)
        self.assertFalse(f("2026-10-06"))   # 国庆(周二)
        self.assertTrue(f("2026-09-18"))    # 周五开市
        self.assertTrue(f("2026-10-08"))    # 节后首个交易日(周四)

    def test_out_of_coverage_falls_back_to_weekday(self):
        """未收录年份退回周末近似(与前端同语义), 不猜测。"""
        self.assertTrue(watchdog.is_trading_day(datetime(2027, 1, 4).date()))   # 周一
        self.assertFalse(watchdog.is_trading_day(datetime(2027, 1, 2).date()))  # 周六


class TestIncrementKeyMigration(unittest.TestCase):
    """增量键换算法的上线第一班: 历史 seen 只有旧键, 不得把历史操作判新增。"""

    def test_叠加文件今日行带同源增量键(self):
        from src.utils.trade_key import trade_keys
        base = {"id": "900461598", "name": "大道可寻", "p": [], "t": [], "i": []}
        m = watchdog.merge_player(base, [], [_t("600371", "买入", 1, 13.13)], "2026-09-17")
        row = m["t"][0]
        self.assertIn("_k", row)
        self.assertIn("_k_legacy", row)
        exp_new, exp_old = trade_keys(_t("600371", "买入", 1, 13.13), "900461598")
        self.assertEqual((row["_k"], row["_k_legacy"]), (exp_new, exp_old))

    def test_历史seen只有旧键时日报不标新增(self):
        from src.utils.trade_key import trade_keys
        new_key, old_key = trade_keys(_t("600371", "买入", 1, 13.13), "900461598")
        t = {"sc": "600371", "dr": "买入", "sn": "测试股", "pr": 13.13,
             "_k": new_key, "_k_legacy": old_key}
        seen, counter = {old_key}, [0]
        line = notify_daily._trade_line(t, {}, seen, True, counter)
        self.assertNotIn("🆕", line, "迁移失败会全量重推历史操作")
        self.assertEqual(counter[0], 0)
        self.assertIn(new_key, seen, "写入 seen 应迁移到新键")

    def test_真正的新操作仍标新增(self):
        from src.utils.trade_key import trade_keys
        old_new, old_key = trade_keys(_t("600371", "买入", 1, 13.13), "900461598")
        fresh_new, fresh_old = trade_keys(_t("000001", "买入", 1, 9.9), "900461598")
        t = {"sc": "000001", "dr": "买入", "sn": "新票", "pr": 9.9,
             "_k": fresh_new, "_k_legacy": fresh_old}
        seen, counter = {old_key}, [0]
        line = notify_daily._trade_line(t, {}, seen, True, counter)
        self.assertIn("🆕", line)
        self.assertEqual(counter[0], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
