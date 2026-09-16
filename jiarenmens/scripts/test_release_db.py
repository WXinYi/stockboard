#!/usr/bin/env python3
"""release_db ④降级链纯函数单测: 库龄计算/闸门边界/auction 库龄口径。运行: python3 scripts/test_release_db.py"""
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_db import (  # noqa: E402
    _auction_last_date, _days_since, _db_age_days, _last_date_of, MAX_STALE_DAYS,
)


def make_db(path, last_date):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, crawl_date TEXT)")
    c.execute("INSERT INTO positions (crawl_date) VALUES (?)", (last_date,))
    c.commit(); c.close()


def make_auction_db(path, bid_date=None, strike_date=None):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE bid_pool (date TEXT, code TEXT)")
    c.execute("CREATE TABLE strike_pool (date TEXT, stage TEXT)")
    if bid_date:
        c.execute("INSERT INTO bid_pool VALUES (?, '000001')", (bid_date,))
    if strike_date:
        c.execute("INSERT INTO strike_pool VALUES (?, '启动')", (strike_date,))
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


class TestAuctionAge(unittest.TestCase):
    """auction.db 的库龄口径(bid_pool.date 优先): 2026-09-15 静默审计 S2 新增回退闸门依赖它。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_取竞价池日期(self):
        p = Path(self.tmp) / "auction.db"
        make_auction_db(p, bid_date="2026-09-15")
        self.assertEqual(_auction_last_date(p), date(2026, 9, 15))

    def test_竞价池为空退到出击池存档(self):
        p = Path(self.tmp) / "auction2.db"
        make_auction_db(p, strike_date="2026-09-12")
        self.assertEqual(_auction_last_date(p), date(2026, 9, 12))

    def test_两表皆空返回None(self):
        p = Path(self.tmp) / "auction3.db"
        make_auction_db(p)
        self.assertIsNone(_auction_last_date(p))

    def test_表缺失返回None不抛(self):
        p = Path(self.tmp) / "auction4.db"
        sqlite3.connect(p).close()
        self.assertIsNone(_auction_last_date(p))

    def test_过期快照过不了闸门_新鲜快照可放行(self):
        stale = Path(self.tmp) / "stale.db"
        make_auction_db(stale, bid_date="2026-08-20")
        age = _days_since(_auction_last_date(stale), date(2026, 9, 15))
        self.assertGreater(age.days, MAX_STALE_DAYS)

        fresh = Path(self.tmp) / "fresh.db"
        make_auction_db(fresh, bid_date="2026-09-12")   # 周五快照, 周一(09-15)用 = 3 天
        age2 = _days_since(_auction_last_date(fresh), date(2026, 9, 15))
        self.assertLessEqual(age2.days, MAX_STALE_DAYS)

    def test_通用取日期函数对缺失表不抛(self):
        p = Path(self.tmp) / "x.db"
        sqlite3.connect(p).close()
        self.assertIsNone(_last_date_of(p, "positions", "crawl_date"))


if __name__ == "__main__":
    unittest.main()
