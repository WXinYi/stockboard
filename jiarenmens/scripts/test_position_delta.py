#!/usr/bin/env python3
"""position_delta 持仓推算回归测试(2026-11-10 分层采集设计)。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.position_delta import apply_trades_to_positions   # noqa: E402


def P(code, nm, ratio, cost=10.0):
    return {"stock_code": code, "stock_name": nm, "position_ratio": ratio,
            "cost_price": cost, "current_price": 11.0, "profit_ratio": 10.0}


class TestApplyTrades(unittest.TestCase):
    def test_buy_new_stock_adds(self):
        prev = [P("000525", "红太阳", 99.9)]
        trades = [{"stock_code": "000530", "stock_name": "冰山冷热", "direction": "买入",
                   "position_ratio": "9成以上", "price": 10.4}]
        out = apply_trades_to_positions(prev, trades)
        codes = {x["stock_code"]: x for x in out}
        self.assertIn("000530", codes)
        self.assertEqual(codes["000530"]["position_ratio"], 95)
        self.assertEqual(codes["000530"]["cost_price"], 10.4)
        self.assertIsNone(codes["000530"]["current_price"], "推算行现价必须 None(诚实)")

    def test_sell_low_ratio_clears(self):
        prev = [P("600537", "*ST亿晶", 99.0), P("000525", "红太阳", 5)]
        # 狼之行一 11-10 实例: 卖*ST亿晶(9成以上=卖出时点)——非清仓档, 但他实际清了?
        # 保守规则: 档位>2成不动 → *ST亿晶保留; 红太阳卖(1成以下)→ 清仓
        trades = [{"stock_code": "600537", "stock_name": "*ST亿晶", "direction": "卖出",
                   "position_ratio": "9成以上", "price": 2.0},
                  {"stock_code": "000525", "stock_name": "红太阳", "direction": "卖出",
                   "position_ratio": "1成以下", "price": 5.5}]
        out = apply_trades_to_positions(prev, trades)
        codes = {x["stock_code"] for x in out}
        self.assertIn("600537", codes, "大档位卖出保守保留(等校准)")
        self.assertNotIn("000525", codes, "小档位卖出=清仓移出")

    def test_sequential_trades_t0(self):
        # 佩斯 11-10 实例: 同票先买(9成以上)后卖(8成) → 保守: 买建仓, 卖不动(8成>2成)
        prev = [P("002086", "东方海洋", 99.9)]
        trades = [{"stock_code": "000530", "stock_name": "冰山冷热", "direction": "买入",
                   "position_ratio": "9成以上", "price": 10.4},
                  {"stock_code": "000530", "stock_name": "冰山冷热", "direction": "卖出",
                   "position_ratio": "8成", "price": 10.53}]
        out = apply_trades_to_positions(prev, trades)
        codes = {x["stock_code"]: x for x in out}
        self.assertIn("000530", codes)
        self.assertIn("002086", codes)

    def test_no_trades_no_change(self):
        prev = [P("000938", "紫光股份", 74.8)]
        out = apply_trades_to_positions(prev, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["current_price"], 11.0, "无调仓不动原持仓(现价保留)")

    def test_input_not_mutated(self):
        prev = [P("000525", "红太阳", 99.9)]
        trades = [{"stock_code": "000525", "stock_name": "红太阳", "direction": "卖出",
                   "position_ratio": "1成以下", "price": 5.5}]
        apply_trades_to_positions(prev, trades)
        self.assertEqual(len(prev), 1, "入参不可被修改")


if __name__ == "__main__":
    unittest.main()
