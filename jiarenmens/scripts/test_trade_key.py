#!/usr/bin/env python3
"""增量键单测: 旧键逐字兼容 / 新键抗抖动 / 迁移期不重推。"""
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.trade_key import (  # noqa: E402
    stable_trade_key, legacy_trade_key, trade_keys, is_trade_seen, seen_write_key,
)


def _old_key(t):
    """独立复刻 export_json 改造前的公式, 作为 golden 值。"""
    def sf(v):
        if v is None:
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    def si(v, d=0):
        if v is None:
            return d
        try:
            return int(v)
        except (ValueError, TypeError):
            return d

    return hashlib.md5("|".join([
        t.get("trade_date", ""), t.get("stock_code", ""), t.get("direction", ""),
        str(sf(t.get("price"))), t.get("position_ratio", "") or "",
        str(si(t.get("trades_count"), 1)),
    ]).encode()).hexdigest()[:12]


BASE = {
    "zh_id": "900240956", "trade_date": "2026-11-10", "stock_code": "301255",
    "direction": "买入", "price": 12.37, "position_ratio": "1成以下",
    "trades_count": 1,
}


class TestLegacyCompat(unittest.TestCase):
    def test_旧键与改造前公式逐字一致(self):
        for t in [BASE,
                  {**BASE, "price": None, "position_ratio": "", "trades_count": None},
                  {**BASE, "price": "9.9", "position_ratio": "9成以上", "trades_count": 3},
                  {**BASE, "trade_date": "2026-09-01", "stock_code": "000001", "direction": "卖出"}]:
            self.assertEqual(legacy_trade_key(t), _old_key(t))

    def test_旧键随价格笔数翻转_正是要修的问题(self):
        self.assertNotEqual(_old_key(BASE), _old_key({**BASE, "price": 12.5}))
        self.assertNotEqual(_old_key(BASE), _old_key({**BASE, "trades_count": 2}))


class TestStableKey(unittest.TestCase):
    def test_价格_笔数_仓位档变化不翻键(self):
        k = stable_trade_key(BASE)
        for patch in ({"price": 99.9}, {"trades_count": 7},
                      {"position_ratio": "9成以上"}, {"price": None, "trades_count": 3}):
            self.assertEqual(stable_trade_key({**BASE, **patch}), k)

    def test_身份字段变化才翻键(self):
        k = stable_trade_key(BASE)
        for patch in ({"zh_id": "900351276"}, {"trade_date": "2026-11-09"},
                      {"stock_code": "600410"}, {"direction": "卖出"}):
            self.assertNotEqual(stable_trade_key({**BASE, **patch}), k)

    def test_16位hex且支持缩写键名(self):
        k = stable_trade_key({"td": "2026-11-10", "sc": "301255", "dr": "买入"}, "900240956")
        self.assertEqual(len(k), 16)
        self.assertEqual(k, stable_trade_key(BASE))

    def test_trade_keys返回两个不同键(self):
        new, old = trade_keys(BASE)
        self.assertEqual(new, stable_trade_key(BASE))
        self.assertEqual(old, _old_key(BASE))
        self.assertNotEqual(new, old)


class TestMigration(unittest.TestCase):
    def test_历史seen只有旧键时不重推(self):
        """上线第一班: seen 里是旧键, 新键不在 → 必须判已见, 否则全量重推。"""
        new, old = trade_keys(BASE)
        seen = {old}
        t = {"_k": new, "_k_legacy": old}
        self.assertTrue(is_trade_seen(t, seen),
                        "迁移失败会把历史操作全部判新增 → 钉钉轰炸")
        self.assertEqual(seen_write_key(t), new)

    def test_完全新的操作才判新增(self):
        new, old = trade_keys(BASE)
        other_new, other_old = trade_keys({**BASE, "stock_code": "600410"})
        seen = {old}
        t = {"_k": other_new, "_k_legacy": other_old}
        self.assertFalse(is_trade_seen(t, seen))

    def test_新键生效后不再依赖旧键(self):
        new, old = trade_keys(BASE)
        self.assertTrue(is_trade_seen({"_k": new}, {new}))
        self.assertTrue(is_trade_seen({"_k_legacy": old}, {old}))

    def test_缺键时退回_id保持旧行为(self):
        self.assertEqual(seen_write_key({"_id": 42}), "42")
        self.assertIsNone(seen_write_key({}))


if __name__ == "__main__":
    unittest.main()
