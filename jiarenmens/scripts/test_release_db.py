#!/usr/bin/env python3
"""release_db ④降级链纯函数单测: 库龄计算/闸门边界。运行: python3 scripts/test_release_db.py"""
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_db import _db_age_days, MAX_STALE_DAYS  # noqa: E402


def make_db(path, last_date):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, crawl_date TEXT)")
    c.execute("INSERT INTO positions (crawl_date) VALUES (?)", (last_date,))
    c.commit(); c.close()


class TestDbAge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_库龄计算(self):
        p = Path(self.tmp) / "a.db"
        make_db(p, "2026-09-11")
        self.assertEqual(_db_age_days(p, date(2026, 9, 13)).days, 2)

    def test_周一早班用周五库_3天_闸门放行(self):
        p = Path(self.tmp) / "b.db"
        make_db(p, "2026-09-11")
        self.assertLessEqual(_db_age_days(p, date(2026, 9, 14)).days, MAX_STALE_DAYS)

    def test_国庆长假缺口_闸门拒绝(self):
        p = Path(self.tmp) / "c.db"
        make_db(p, "2026-09-30")
        self.assertGreater(_db_age_days(p, date(2026, 10, 8)).days, MAX_STALE_DAYS)

    def test_空库_无日期返回None(self):
        p = Path(self.tmp) / "d.db"
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, crawl_date TEXT)")
        c.commit(); c.close()
        self.assertIsNone(_db_age_days(p, date(2026, 9, 13)))

    def test_坏库返回None(self):
        p = Path(self.tmp) / "e.db"
        p.write_text("not a db")
        self.assertIsNone(_db_age_days(p, date(2026, 9, 13)))


if __name__ == "__main__":
    unittest.main()
