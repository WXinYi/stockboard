#!/usr/bin/env python3
"""consensus_surge_push 的筛选规则回归测试(2026-11-08 立项时锁定)。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from consensus_surge_push import pick_surges, build_message   # noqa: E402


def S(c, h, tp):
    return {"c": c, "n": f"票{c}", "h": h, "tp": tp, "ap": 0}


class TestPickSurges(unittest.TestCase):
    def test_thresholds(self):
        cur = {"A": S("A", 23, 100),   # Δh=13, h≥15 → 入选
               "B": S("B", 18, 90),    # Δh=8 恰好达标 → 入选(≥)
               "C": S("C", 20, 80),    # Δh=7 → 不选
               "D": S("D", 10, 50),    # Δh=9 但 h<15 → 不选(量级门槛)
               "E": S("E", 30, 99)}    # 新票(基线无) → 不选(无基线不判)
        prev = {"A": S("A", 10, 40), "B": S("B", 10, 40),
                "C": S("C", 13, 40), "D": S("D", 1, 5)}
        got = pick_surges(cur, prev)
        self.assertEqual([g["code"] for g in got], ["A", "B"])
        self.assertEqual(got[0]["dh"], 13)

    def test_topn_desc(self):
        cur = {c: S(c, 50, 100) for c in "ABCDEFGHIJ"}           # 全部 Δh=40
        cur["K"] = S("K", 60, 120)                               # Δh=50
        prev = {c: S(c, 10, 40) for c in "ABCDEFGHIJK"}
        got = pick_surges(cur, prev)
        self.assertEqual(len(got), 10)                            # TopN=10
        self.assertEqual(got[0]["code"], "K")                     # Δh 最大在前
        self.assertTrue(all(got[i]["dh"] >= got[i+1]["dh"] for i in range(len(got)-1)))

    def test_message_lines(self):
        surges = pick_surges({"A": S("A", 23, 100)}, {"A": S("A", 10, 40)})
        msg = build_message("2026-11-08", surges)
        self.assertIn("重仓共识飙升", msg)
        self.assertIn("10→**23**", msg)
        self.assertIn("漂移", msg)                                 # 诚实脚注在场
        self.assertIn("-0.95%", msg)                               # 回测注脚在场


if __name__ == "__main__":
    unittest.main()
