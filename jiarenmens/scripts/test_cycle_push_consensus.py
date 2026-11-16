"""2026-11-16 午盘推送附加 A 股共识的回归测试。

锁定：
  1. 调仓共识/重仓共识都只推 A 股（6 位代码），港股（5 位）不进推送；
  2. 人均仓位用 tp/h、净买入按 buy-sell，与页面口径一致；
  3. 数据文件缺失/损坏时返回空串，不破坏原有格局推送。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cycle_push as cp  # noqa: E402


class TestConsensusPush(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.patcher = patch.object(cp, "DATA_LATEST", self.dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def _write(self, name, obj):
        (self.dir / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def test_仅推A股_港股被过滤(self):
        self._write("today.json", {"tradeConsensus": {"stocks": [
            {"code": "01810", "name": "小米集团", "buy": 99, "sell": 0},   # 港股
            {"code": "03690", "name": "美团", "buy": 88, "sell": 0},       # 港股
            {"code": "002148", "name": "北纬科技", "buy": 12, "sell": 4},  # A股
        ]}})
        self._write("stocks.json", {"stats": [
            {"code": "00700", "name": "腾讯控股", "h": 50, "tp": 5000, "ap": 100},  # 港股
            {"code": "600715", "name": "文投控股", "h": 49, "tp": 1821.31, "ap": 2306.69},  # A股
        ]})
        text = cp.build_consensus_markdown()
        self.assertIn("北纬科技", text)
        self.assertIn("文投控股", text)
        self.assertNotIn("小米集团", text, "港股不得进推送")
        self.assertNotIn("美团", text)
        self.assertNotIn("腾讯控股", text)
        self.assertIn("净+8", text, "净买入=买12-卖4")
        self.assertIn("人均仓位 37%", text, "1821.31/49≈37")

    def test_数据缺失返回空串不破坏原推送(self):
        # 两个文件都不存在
        self.assertEqual(cp.build_consensus_markdown(), "")
        # 只有 today，缺 stocks
        self._write("today.json", {"tradeConsensus": {"stocks": [
            {"code": "002148", "name": "北纬科技", "buy": 12, "sell": 4}]}})
        text = cp.build_consensus_markdown()
        self.assertIn("北纬科技", text)
        self.assertIn("暂无 A 股持仓数据", text)

    def test_无A股数据时整段返回空(self):
        self._write("today.json", {"tradeConsensus": {"stocks": [
            {"code": "01810", "name": "小米集团", "buy": 5, "sell": 0}]}})
        self._write("stocks.json", {"stats": [
            {"code": "03690", "name": "美团", "h": 9, "tp": 900, "ap": 10}]})
        # 有共识骨架但无任何 A 股行 → has_data=False → 返回空串（不推无意义内容）
        self.assertEqual(cp.build_consensus_markdown(), "")

    def test_脏数据容错(self):
        self._write("today.json", {"tradeConsensus": {"stocks": [
            {"code": "002148", "name": "北纬科技", "buy": "x", "sell": 4},  # buy 非数字
        ]}})
        self._write("stocks.json", {"stats": []})
        # 非数字字段安全降级为 0，不抛异常中断整条格局推送
        text = cp.build_consensus_markdown()
        self.assertIn("北纬科技", text)
        self.assertNotIn("净nan", text)


if __name__ == "__main__":
    unittest.main()
