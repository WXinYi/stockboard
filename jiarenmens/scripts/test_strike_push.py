"""单测: 09:29 出击推送的两个纯函数(pick_strike_top / rank_lianban_bid)。
运行: cd jiarenmens && venv/bin/python -m unittest scripts.test_strike_push -v
(无第三方依赖; auction_scan 导入即校验模块可加载)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.auction_scan import auction_pulse, pick_strike_top, rank_lianban_bid  # noqa: E402


class TestPickStrikeTop(unittest.TestCase):
    def test_go_care_watch_order(self):
        """可做(出击)优先 → 矩阵谨慎(备选)次之; 观察不上榜(除非全观察)"""
        picks = [
            {"code": "1", "name": "甲", "status": "观察(只看最强)"},
            {"code": "2", "name": "乙", "status": "可做(矩阵谨慎)"},
            {"code": "3", "name": "丙", "status": "可做(接力)"},
        ]
        top, watch = pick_strike_top(picks)
        self.assertFalse(watch)
        self.assertEqual([p["code"] for p in top], ["3", "2"])

    def test_all_watch_fallback(self):
        """全观察(退潮期典型) → watch_mode=True, 取观察前5, 保持存档顺序"""
        picks = [{"code": str(i), "name": f"股{i}", "status": "观察(火种)"} for i in range(7)]
        top, watch = pick_strike_top(picks)
        self.assertTrue(watch)
        self.assertEqual(len(top), 5)
        self.assertEqual([p["code"] for p in top], ["0", "1", "2", "3", "4"])

    def test_go_truncated_to_five(self):
        """可做超过5只 → 截断到5, 保持顺序"""
        picks = [{"code": str(i), "name": f"股{i}", "status": "可做"} for i in range(8)]
        top, watch = pick_strike_top(picks)
        self.assertFalse(watch)
        self.assertEqual(len(top), 5)
        self.assertEqual([p["code"] for p in top], ["0", "1", "2", "3", "4"])

    def test_empty(self):
        top, watch = pick_strike_top([])
        self.assertEqual(top, [])
        self.assertTrue(watch)


class TestRankLianbanBid(unittest.TestCase):
    def test_rank_by_turnover_then_bid(self):
        """换手高优先 → 竞价涨幅高优先"""
        rows = [
            {"code": "1", "name": "甲", "height": 2, "bid_pct": 5.0, "turnover": 1.0},
            {"code": "2", "name": "乙", "height": 3, "bid_pct": 1.0, "turnover": 3.0},
            {"code": "3", "name": "丙", "height": 2, "bid_pct": 6.0, "turnover": 1.0},
        ]
        top = rank_lianban_bid(rows)
        self.assertEqual([r["code"] for r in top], ["2", "3", "1"])

    def test_filters(self):
        """换手0(含补算失败)与 ST 剔除"""
        rows = [
            {"code": "1", "name": "甲", "height": 3, "bid_pct": 3.0, "turnover": 2.0},
            {"code": "2", "name": "乙", "height": 2, "bid_pct": 3.0, "turnover": 0},
            {"code": "3", "name": "*ST乙", "height": 2, "bid_pct": 3.0, "turnover": 9.0},
            {"code": "4", "name": "st丙", "height": 2, "bid_pct": 3.0, "turnover": 9.0},
        ]
        top = rank_lianban_bid(rows)
        self.assertEqual([r["code"] for r in top], ["1"])

    def test_truncate(self):
        """结果截断 top_n"""
        rows = [{"code": str(i), "name": f"股{i}", "height": 2, "bid_pct": 2.0,
                 "turnover": float(i)} for i in range(1, 9)]
        top = rank_lianban_bid(rows, top_n=5)
        self.assertEqual([r["code"] for r in top], ["8", "7", "6", "5", "4"])
        self.assertEqual(top[0]["turnover"], 8.0)


class TestAuctionPulse(unittest.TestCase):
    def _pool(self, pcts):
        return {str(i): {"name": f"股{i}", "bid_pct": p} for i, p in enumerate(pcts)}

    def test_repair(self):
        """均幅≥+2 且 红盘率≥70% → 情绪修复"""
        pcts = [5, 6, 4, 3, 2, 1, -1, 3, 4, 2]
        r = auction_pulse([{"code": str(i), "name": "x"} for i in range(10)], self._pool(pcts))
        self.assertEqual(r["verdict"], "情绪修复")
        self.assertGreaterEqual(r["red_rate"], 0.7)

    def test_continue_downtrend(self):
        """均幅<0 → 退潮延续; 大面率≥30% → 退潮延续"""
        r1 = auction_pulse([{"code": str(i), "name": "x"} for i in range(10)],
                           self._pool([-2, -4, -1, -3, 1, 0, -2, -1, 0, -1]))
        self.assertEqual(r1["verdict"], "退潮延续")
        pcts = [4, 5, -5, -6, -4, 2, 1, -7, 3, 0]  # 均幅≈-0.7? 算: 4+5-5-6-4+2+1-7+3+0=-7 → 均幅-0.7 已覆盖
        # 大面≥3/10 且红盘率高: [5,4,6,4,5,-8,-6,-5,4,3] → 均幅1.2 红盘率0.8 大面0.3
        r2 = auction_pulse([{"code": str(i), "name": "x"} for i in range(10)],
                           self._pool([5, 4, 6, 4, 5, -8, -6, -5, 4, 3]))
        self.assertEqual(r2["verdict"], "退潮延续")

    def test_divergence(self):
        """红盘率中等且无大面 → 分化(观望)"""
        r = auction_pulse([{"code": str(i), "name": "x"} for i in range(10)],
                          self._pool([3, 2, 1, 0, -1, 2, 1, 0, 1, -2]))
        self.assertEqual(r["verdict"], "分化(观望)")

    def test_sample_too_small(self):
        r = auction_pulse([{"code": str(i), "name": "x"} for i in range(4)],
                          self._pool([5, 6, 7, 8]))
        self.assertIsNone(r)

    def test_leader_quotes(self):
        pool = self._pool([5, 6, 4, 3, 2, 1, -1, 3, 4, 2])
        r = auction_pulse([{"code": str(i), "name": "x"} for i in range(10)], pool,
                          leaders=[{"code": "0", "name": "龙版"}, {"code": "1", "name": "亚盛"}])
        self.assertIn("龙版+5.0%", r["lead_txt"])
        self.assertIn("亚盛+6.0%", r["lead_txt"])


if __name__ == "__main__":
    unittest.main()
