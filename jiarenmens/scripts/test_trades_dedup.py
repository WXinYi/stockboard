#!/usr/bin/env python3
"""trades 写前内容比对去重单测(2026-11-12)。

验证「不重写」用 id 集合: DELETE+INSERT 会分配新的 AUTOINCREMENT id,
而 CURRENT_TIMESTAMP 只有秒级精度, 不能用来判断是否真的写了库。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.storage.sqlite_storage import SQLiteStorage  # noqa: E402

DAY = "2026-11-12"


def _t(code, dr="买入", tc=1, price=10.0, td=DAY):
    return {"stock_name": f"股{code}", "stock_code": code,
            "trades_count": tc, "position_ratio": "1成",
            "position_value": 0.0, "price": price,
            "trade_date": td, "direction": dr, "position_change": 0}


class TestTradesDedup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.st = SQLiteStorage(self.db)
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("SB_TRADE_DEDUP", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _ids(self, zh=None, day=DAY):
        with sqlite3.connect(self.db) as c:
            if zh:
                return [r[0] for r in c.execute(
                    "SELECT id FROM trades WHERE zh_id=? AND crawl_date=? ORDER BY id", (zh, day))]
            return [r[0] for r in c.execute("SELECT id FROM trades ORDER BY id")]

    def _rows(self, zh=None, day=DAY):
        with sqlite3.connect(self.db) as c:
            if zh:
                return c.execute(
                    "SELECT stock_code, direction, trades_count, price, trade_date "
                    "FROM trades WHERE zh_id=? AND crawl_date=? ORDER BY id", (zh, day)).fetchall()
            return c.execute("SELECT zh_id, stock_code FROM trades ORDER BY id").fetchall()

    def test_首次写入正常落库(self):
        self.st.save_trades_batch([("zh1", [_t("600371"), _t("000002", "卖出")])], DAY)
        self.assertEqual(len(self._ids()), 2)
        self.assertEqual(len(self._rows("zh1")), 2)

    def test_相同数据第二次不重写(self):
        data = [("zh1", [_t("600371"), _t("000002", "卖出")])]
        self.st.save_trades_batch(data, DAY)
        before = self._ids()
        self.st.save_trades_batch(data, DAY)
        self.assertEqual(self._ids(), before, "id 未变 = 没有 DELETE+INSERT")
        self.assertEqual(self._rows("zh1"), [
            ("600371", "买入", 1, 10.0, DAY), ("000002", "卖出", 1, 10.0, DAY)])

    def test_新增一笔会重写(self):
        self.st.save_trades_batch([("zh1", [_t("600371")])], DAY)
        before = self._ids()
        self.st.save_trades_batch([("zh1", [_t("600371"), _t("000002", "卖出")])], DAY)
        self.assertNotEqual(self._ids(), before)
        self.assertEqual(len(self._rows("zh1")), 2)

    def test_次数或价格变化会重写(self):
        self.st.save_trades_batch([("zh1", [_t("600371", tc=1, price=10.0)])], DAY)
        before = self._ids()
        self.st.save_trades_batch([("zh1", [_t("600371", tc=2, price=10.5)])], DAY)
        self.assertNotEqual(self._ids(), before)
        self.assertEqual(self._rows("zh1")[0][2], 2)

    def test_行顺序不同但内容相同_不重写(self):
        a, b = _t("600371"), _t("000002", "卖出")
        self.st.save_trades_batch([("zh1", [a, b])], DAY)
        before = self._ids()
        self.st.save_trades_batch([("zh1", [b, a])], DAY)
        self.assertEqual(self._ids(), before)

    def test_空数据仍清空已有记录_保留旧语义(self):
        self.st.save_trades_batch([("zh1", [_t("600371")])], DAY)
        self.assertEqual(len(self._ids()), 1)
        self.st.save_trades_batch([("zh1", [])], DAY)
        self.assertEqual(self._ids(), [], "空数据必须清空, 不能被去重跳过")

    def test_空数据且库内也空_无副作用(self):
        self.st.save_trades_batch([("zh1", [])], DAY)
        self.assertEqual(self._ids(), [])

    def test_混合批次只重写变化的选手(self):
        self.st.save_trades_batch([("zh1", [_t("600371")]), ("zh2", [_t("000002")])], DAY)
        ids_before = {z: self._ids(z) for z in ("zh1", "zh2")}
        self.st.save_trades_batch([("zh1", [_t("600371")]),               # 未变
                                   ("zh2", [_t("000002"), _t("600519")])], DAY)  # 变化
        self.assertEqual(self._ids("zh1"), ids_before["zh1"], "未变选手不应重写")
        self.assertNotEqual(self._ids("zh2"), ids_before["zh2"], "变化选手应重写")
        self.assertEqual(len(self._rows("zh2")), 2)

    def test_跨日期分区互不影响(self):
        y = "2026-11-11"
        self.st.save_trades_batch([("zh1", [_t("600371", td=y)])], y)
        ids_y = self._ids("zh1", y)
        self.st.save_trades_batch([("zh1", [_t("600519", td=DAY)])], DAY)
        self.assertEqual(self._ids("zh1", y), ids_y, "新日期写入不能动旧分区")
        self.assertEqual(len(self._ids("zh1", DAY)), 1)

    def test_灰度开关0恢复无条件重写(self):
        data = [("zh1", [_t("600371")])]
        self.st.save_trades_batch(data, DAY)
        before = self._ids()
        with patch.dict(os.environ, {"SB_TRADE_DEDUP": "0"}):
            self.st.save_trades_batch(data, DAY)
        self.assertNotEqual(self._ids(), before, "关闭去重后应恢复每班重写")
        self.assertEqual(len(self._rows("zh1")), 1)

    def test_同批次同一选手出现两次_以最后一次为准(self):
        self.st.save_trades_batch([("zh1", [_t("600371")]),
                                   ("zh1", [_t("600371"), _t("000002")])], DAY)
        self.assertEqual(len(self._rows("zh1")), 2)

    def test_浮点归一化_微小差异视为相同(self):
        self.st.save_trades_batch([("zh1", [_t("600371", price=10.0)])], DAY)
        before = self._ids()
        self.st.save_trades_batch([("zh1", [_t("600371", price=10.0000001)])], DAY)
        self.assertEqual(self._ids(), before)

    def test_trades字段别名与trades_count等价(self):
        self.st.save_trades_batch([("zh1", [_t("600371", tc=3)])], DAY)
        before = self._ids()
        alias = dict(_t("600371"), trades=3)
        alias.pop("trades_count", None)
        self.st.save_trades_batch([("zh1", [alias])], DAY)
        self.assertEqual(self._ids(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
