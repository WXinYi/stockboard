#!/usr/bin/env python3
"""export_json 的 stockStats 双口径聚合回归测试。

背景(2026-11-07): qh/qtp/qap 曾在推导式里引用未定义的裸变量 code(泄漏 qrows
循环残留值) → 全行恒定(线上 qh=5/354/15.95 连续数月), 优质口径整体失效。
本测试锁死: 每票 qh 必须来自该票自己的优质聚合。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_json import _build_stock_stats   # noqa: E402


class TestBuildStockStats(unittest.TestCase):
    def test_qh_per_stock_not_leaked(self):
        """两票优质持有数不同 → qh 必须逐票取值, 不得全行同值(泄漏回归)。"""
        stock_stats_map = {
            "002086": {"code": "002086", "name": "东方海洋", "holders": 107,
                       "total_position": 6653.0, "total_profit": 123.0, "count": 107},
            "01810": {"code": "01810", "name": "小米集团", "holders": 60,
                      "total_position": 4972.0, "total_profit": -30.0, "count": 60},
            "600745": {"code": "600745", "name": "*ST闻泰", "holders": 42,
                       "total_position": 2000.0, "total_profit": -10.0, "count": 42},
        }
        stock_stats_q = {   # 优质: 东方海洋 2 家, 小米 1 家, *ST闻泰 0 家
            "002086": {"holders": 2, "total_position": 160.0, "total_profit": 20.0},
            "01810": {"holders": 1, "total_position": 55.0, "total_profit": -2.0},
        }
        rows = _build_stock_stats(stock_stats_map, stock_stats_q)
        by = {r["c"]: r for r in rows}
        self.assertEqual(by["002086"]["qh"], 2)
        self.assertEqual(by["01810"]["qh"], 1)
        self.assertEqual(by["600745"]["qh"], 0, "无优质持有的票必须为 0, 不得串值")
        self.assertEqual(len({r["qh"] for r in rows}), 3, "三票 qh 不得全行同值")
        self.assertEqual(by["01810"]["qap"], -2.0)
        self.assertEqual(by["600745"]["qap"], 0)

    def test_empty_quality_aggregation(self):
        """优质聚合为空(无优质选手) → qh 全 0, 不报错。"""
        stock_stats_map = {
            "002086": {"code": "002086", "name": "东方海洋", "holders": 5,
                       "total_position": 300.0, "total_profit": 10.0, "count": 5},
        }
        rows = _build_stock_stats(stock_stats_map, {})
        self.assertEqual(rows[0]["qh"], 0)
        self.assertEqual(rows[0]["qtp"], 0)
        self.assertEqual(rows[0]["h"], 5, "全体口径不受影响")

    def test_sorted_by_tp_desc(self):
        rows = _build_stock_stats(
            {"A": {"code": "A", "name": "甲", "holders": 1, "total_position": 100.0,
                   "total_profit": 1.0, "count": 1},
             "B": {"code": "B", "name": "乙", "holders": 2, "total_position": 300.0,
                   "total_profit": 2.0, "count": 2}},
            {})
        self.assertEqual([r["c"] for r in rows], ["B", "A"])


if __name__ == "__main__":
    unittest.main()
