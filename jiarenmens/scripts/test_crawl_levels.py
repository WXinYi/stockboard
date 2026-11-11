#!/usr/bin/env python3
"""采集档位 eod 单测(2026-11-11): 只省 info, 收益/持仓/dimensions/调仓2页与 full 一致。

重点防「反向修复」:
- eod 不拉 info 后, detail 必须不含档案字段 → 触发 main.py 的缺字段不覆盖合并;
- 调仓仍 2 页(本地库有 647 个 (选手,日) 超过单页 20 行, 降 1 页会丢当日调仓);
- dimensions 仍拉(否则 max_drawdown / win_rate 退化)。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.spiders import api_detail  # noqa: E402

ZH = "900293400"
YLD = {"totalYieldRate": 100.0, "dayYieldRate": 1.5, "monthYieldRate": 20.0,
       "yearYieldRate": 50.0, "netWorth": 2.5}
POS = [{"data": [{"__name": "测试股", "__code": "600371", "holdPos": 10,
                  "positionRateDetail": "10", "cbj": 9.0, "__zxjg": 10.0}]}]
DIMS = {"maxDrawdown": 12.5, "dayWinRate": 50}
INFO = {"zuheName": "测试选手", "concernCnt": 100, "yxts": 300,
        "comment": "档案简介", "uidNick": "经理", "startDate": "2020-01-01",
        "portfRat": 90}
TRADES = [{"biz_date": "20261111", "stock_code": "600371", "direction": "买入",
           "trades_count": 1, "price": 10.0, "position_ratio": "1成"}]


def _fake_call(method, args, tokens, with_ok=False):
    return {
        "CombinationInfoHandler": INFO,
        "combination_yield_detail_handler": YLD,
        "CombinationHoldPositionPermitHandler": POS,
        "combination_dimensions": DIMS,
    }.get(method)


class TestEodLevel(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.page_args = []

        def call(method, args, tokens, with_ok=False):
            self.calls.append(method)
            return _fake_call(method, args, tokens, with_ok)

        def trades(tokens, uid, zh_id, max_pages=3, with_ok=False):
            self.page_args.append(max_pages)
            return (list(TRADES), True) if with_ok else list(TRADES)

        self.patches = [
            patch.object(api_detail, "_load_tokens", return_value={"ctToken": "x", "utToken": "y"}),
            patch.object(api_detail, "_resolve_user_id", return_value="uid-1"),
            patch.object(api_detail, "_call_api_new", side_effect=call),
            patch.object(api_detail, "_fetch_trades_pages", side_effect=trades),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def _run(self, level):
        self.calls.clear()
        self.page_args.clear()
        return api_detail.crawl_player_all_data(ZH, "uid-1", level=level), list(self.calls), list(self.page_args)

    def test_档位表包含eod(self):
        self.assertIn("eod", api_detail.CRAWL_LEVELS)

    def test_eod不拉info但保留收益持仓dimensions(self):
        (_, _, _), calls, _ = self._run("eod")
        self.assertNotIn("CombinationInfoHandler", calls)
        for m in ("combination_yield_detail_handler", "CombinationHoldPositionPermitHandler",
                  "combination_dimensions"):
            self.assertIn(m, calls)

    def test_eod保留调仓2页(self):
        self._run("eod")
        self.assertEqual(self.page_args, [2], "降 1 页会丢高频选手当日调仓")

    def test_full仍拉info_回滚档可用(self):
        (_, _, _), calls, _ = self._run("full")
        self.assertIn("CombinationInfoHandler", calls)

    def test_eod与full的业务字段完全一致(self):
        (d_e, p_e, t_e), _, _ = self._run("eod")
        (d_f, p_f, t_f), _, _ = self._run("full")
        for k in ("total_return", "daily_return", "weekly_return", "monthly_return",
                  "yearly_return", "net_value", "max_drawdown", "win_rate"):
            self.assertEqual(d_e[k], d_f[k], f"{k} 在 eod 档退化")
        self.assertEqual(p_e, p_f, "真实持仓必须一致")
        self.assertEqual(t_e, t_f, "当日调仓必须一致")

    def test_eod不含档案字段以触发缺字段不覆盖合并(self):
        (d, _, _), _, _ = self._run("eod")
        for k in ("name", "followers", "days", "concept", "intro", "manager_name",
                  "start_date", "portf_rat"):
            self.assertNotIn(k, d, f"{k} 不应由 eod 档写入")

    def test_合并后档案字段保留且收益刷新(self):
        (d, _, _), _, _ = self._run("eod")
        old = {"zh_id": ZH, "name": "测试选手", "followers": 100, "days": 300,
               "concept": "档案简介", "portf_rat": 90.0,
               "total_return": 1.0, "max_drawdown": 99.0}
        merged = {**old, **d}
        self.assertEqual(merged["name"], "测试选手")
        self.assertEqual(merged["followers"], 100)
        self.assertEqual(merged["days"], 300)
        self.assertEqual(merged["portf_rat"], 90.0)
        self.assertNotEqual(merged["total_return"], 1.0, "收益应被当日值覆盖")
        self.assertNotEqual(merged["max_drawdown"], 99.0, "回撤应被当日值覆盖")

    def test_eod接口全空返回三空且判组合异常(self):
        with patch.object(api_detail, "_call_api_new", return_value=None):
            self.assertEqual(api_detail.crawl_player_all_data(ZH, "uid-1", level="eod"),
                             (None, [], []))

    def test_未知档位仍返回三空(self):
        self.assertEqual(api_detail.crawl_player_all_data(ZH, "uid-1", level="nope"),
                         (None, [], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
