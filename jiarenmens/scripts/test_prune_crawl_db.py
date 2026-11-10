#!/usr/bin/env python3
"""prune 采集日窗口单测: 两表并集 / 空日期不计入窗口 / dry-run 不删数据。"""
import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prune_crawl_db as mod  # noqa: E402


def make_db(path, trade_days, pos_days, empty_rows=False):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, crawl_date TEXT)")
    c.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, crawl_date TEXT)")
    for d in trade_days:
        c.execute("INSERT INTO trades (crawl_date) VALUES (?)", (d,))
    for d in pos_days:
        c.execute("INSERT INTO positions (crawl_date) VALUES (?)", (d,))
    if empty_rows:
        c.execute("INSERT INTO trades (crawl_date) VALUES ('')")
        c.execute("INSERT INTO positions (crawl_date) VALUES ('')")
        c.execute("INSERT INTO trades (crawl_date) VALUES (NULL)")
    c.commit()
    c.close()


def day(n):
    from datetime import date, timedelta
    return (date(2026, 1, 1) + timedelta(days=n - 1)).isoformat()


class TestPruneWindow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkstemp(suffix=".db")[1])
        # trades: 4..45 (42天), positions: 1..3 独有 + 4..45 → 并集 45 天
        # KEEP_DATES=40 → drop = 1..5, 其中 1..3 只有 positions 有(旧实现会漏)。
        trade_days = [day(n) for n in range(4, 46)]
        pos_days = [day(n) for n in range(1, 46)]
        make_db(self.tmp, trade_days, pos_days, empty_rows=True)

    def tearDown(self):
        self.tmp.unlink(missing_ok=True)

    def _run(self):
        buf = io.StringIO()
        with patch.object(mod, "DB_PATH", self.tmp), \
             patch.object(sys, "argv", ["prune_crawl_db.py"]), \
             contextlib.redirect_stdout(buf):
            mod.main()
        return buf.getvalue()

    def test_positions独有日期进入drop(self):
        out = self._run()
        # 旧实现只看 trades(42 天) → 只删 2 天且 positions 的 1..3 永远泄漏;
        # 并集后 45 天 → 删 5 天, 其中 positions 行有 5 行(1..5 全在 drop 内)。
        self.assertIn("将删除 5 个采集日", out)
        self.assertIn("positions 5 行", out)
        self.assertIn("trades 2 行", out)

    def test_空日期不占窗口(self):
        out = self._run()
        # 45 个有效日期 - 40 = 5; 若空串/NULL 被计入窗口, drop 会变成 6 或 7。
        self.assertIn("将删除 5 个采集日", out, "空串/NULL 日期不应计入窗口")
        self.assertIn(day(1), out)

    def test_dry_run不删数据(self):
        self._run()
        c = sqlite3.connect(self.tmp)
        n_trades = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        n_pos = c.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        c.close()
        self.assertEqual(n_trades, 44)   # 42 天 + 空串 + NULL
        self.assertEqual(n_pos, 46)     # 45 天 + 空串


if __name__ == "__main__":
    unittest.main()
