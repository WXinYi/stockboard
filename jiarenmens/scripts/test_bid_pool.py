#!/usr/bin/env python3
"""bid_pool 落库口径单测(2026-09-15 静默审计 C1): 缺失字段写 NULL, 不用 0 冒充。

为什么重要: "换手 0%" 与"字段没取到"在下游曾是同一个值, 页面把缺失显示成真实 0%。
所有 bid_pool 消费方都用 `or 0` / `if not x` 判定, NULL 与 0 对其等价(腾讯补算链照样触发),
所以本改动不改变行为, 只让"没取到"不再冒充"真的是 0"。

运行: python3 scripts/test_bid_pool.py
"""
import sqlite3
import sys
import tempfile
import unittest
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.spiders.auction_spider import AuctionStore  # noqa: E402

FULL = ["000001", "平安银行", 10.5, "2.3", 100, "3.1", "1.2", "0.5", "4.4", "0.9",
        "123", "银行", "500000", "t", "x", "y", "z"]
# 空串/None 混排: 模拟 KPL 返回缺字段
MISSING = ["000002", "万科A", "", None, "", "", "", "", "", "", "", "", "", "", "", "", ""]


class TestBidPoolNull(unittest.TestCase):
    def setUp(self):
        self.d = _date.today().strftime("%Y-%m-%d")   # 动态日期: save_bid_pool 只收最近3天
        self.tmp = Path(tempfile.mkdtemp()) / "auction.db"
        self.store = AuctionStore(db_path=self.tmp)

    def _rows(self, code):
        c = sqlite3.connect(self.tmp)
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM bid_pool WHERE code=?", (code,))]

    def test_完整行照原值落库(self):
        self.store.save_bid_pool(self.d, [FULL], "KPL")
        r = self._rows("000001")[0]
        self.assertEqual(r["price"], 10.5)
        self.assertEqual(r["turnover_ratio"], 0.5)
        self.assertEqual(r["circ_mv"], 500000.0)
        self.assertEqual(r["plates"], "银行")

    def test_缺失字段写NULL而不是0(self):
        self.store.save_bid_pool(self.d, [MISSING], "KPL")
        r = self._rows("000002")[0]
        for col in ("price", "change_pct", "bid_pct", "turnover_ratio", "main_net", "circ_mv"):
            self.assertIsNone(r[col], f"{col} 应为 NULL(缺失), 实际 {r[col]!r}")

    def test_真0仍然是0_不被误判为缺失(self):
        row = list(FULL)
        row[7] = 0        # turnover_ratio(第 8 个字段)真的是 0
        self.store.save_bid_pool(self.d, [row], "KPL")
        self.assertEqual(self._rows("000001")[0]["turnover_ratio"], 0)

    def test_NULL与0在下游falsy判定上等价(self):
        """补算链触发条件 `if not ratio` 对 NULL/0 一致 —— 这是本改动安全的前提。"""
        self.store.save_bid_pool(self.d, [MISSING], "KPL")
        r = self._rows("000002")[0]
        self.assertFalse(r["turnover_ratio"] or 0)          # NULL → 触发补算
        self.assertEqual(r["circ_mv"] or "mv_prev", "mv_prev")

    def test_短行不再越界崩溃(self):
        short = FULL[:16]                                    # 16 列(< 17)
        self.store.save_bid_pool(self.d, [short], "KPL")
        self.assertEqual(len(self._rows("000001")), 1)

    def test_少于13列的行跳过不写(self):
        self.store.save_bid_pool(self.d, [FULL[:12]], "KPL")
        self.assertEqual(len(self._rows("000001")), 0)


if __name__ == "__main__":
    unittest.main()
