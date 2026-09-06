"""单测: 09:29 出击推送的两个纯函数(pick_strike_top / rank_lianban_bid)。
运行: cd jiarenmens && venv/bin/python -m unittest scripts.test_strike_push -v
(无第三方依赖; auction_scan 导入即校验模块可加载)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.auction_scan import pick_strike_top, rank_lianban_bid  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
