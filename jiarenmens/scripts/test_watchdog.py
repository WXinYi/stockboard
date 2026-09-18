#!/usr/bin/env python3
"""盯盘 watchdog 单测: 去重/追加、叠加合并、状态跨日翻篇、降级计数、交易时段闸门、日报共享去重。

运行: python3 scripts/test_watchdog.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

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
                "900450475": {"name": "武研琳", "ok": True, "trades": [t_b], "positions": []},
                "900240956": {"name": "股得猫咛", "ok": True, "trades": [], "positions": []},
                "900438148": {"name": "我嘚财富", "hidden": True}}
        new_mark = {"900456476": {id(t_a): False}}   # 只有甲有本轮新增
        msg = watchdog.build_message("2026-09-18", "10:00", data, new_mark, {})
        lines = msg.splitlines()
        i_a = next(i for i, l in enumerate(lines) if "狼之行一" in l)
        i_b = next(i for i, l in enumerate(lines) if "武研琳" in l)
        self.assertEqual(lines[i_b - 1], "", "选手块之间必须有空行")
        self.assertIn("🆕", lines[i_a], "有本轮新增的选手名应标 🆕")
        self.assertNotIn("🆕", lines[i_b], "无新增的选手不标 🆕")
        self.assertTrue(any("🆕" in l and "买入" in l for l in lines[i_a:i_b]))
        self.assertTrue(any("💤 今日暂无操作" in l and "股得猫咛" in l for l in lines))
        self.assertTrue(any("🔇 组合已隐藏" in l and "我嘚财富" in l for l in lines))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
