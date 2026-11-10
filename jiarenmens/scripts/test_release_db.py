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


class TestUploadRollback(unittest.TestCase):
    """上传失败不得留下"旧资产已删、新资产没传"的热层断链(11-10 演练事故)。"""

    def setUp(self):
        import release_db
        self.rd = release_db
        self.tmp = Path(tempfile.mkstemp(suffix=".db.gz")[1])
        self.tmp.write_bytes(b"NEWGZ")
        self.rel = {"id": 1, "assets": [
            {"name": "crawl-latest.db.gz", "url": "https://api/asset/1",
             "browser_download_url": "https://cdn/asset/1"},
        ]}

    def tearDown(self):
        self.tmp.unlink(missing_ok=True)

    def _patched(self, post_results, backup=b"OLDGZ", backup_exc=None, api=None):
        from unittest.mock import patch
        posts = []

        def post(rel_id, name, data):
            posts.append(data)
            return post_results[len(posts) - 1]

        def dl(url):
            if backup_exc:
                raise backup_exc
            return backup

        api_patch = (patch.object(self.rd, "_api", side_effect=api) if api
                     else patch.object(self.rd, "_api", return_value=(200, {})))
        return (patch.object(self.rd, "ensure_release", return_value=self.rel),
                patch.object(self.rd, "_download_asset_bytes", side_effect=dl),
                patch.object(self.rd, "_post_asset", side_effect=post),
                api_patch,
                patch.object(self.rd, "_token", return_value="x"), posts)

    def test_上传成功不触发回滚(self):
        p1, p2, p3, p4, p5, posts = self._patched([(201, {})])
        with p1, p2, p3, p4, p5:
            self.rd.upload_asset("db-state", "crawl-latest.db.gz", self.tmp)
        self.assertEqual(posts, [b"NEWGZ"])

    def test_上传失败时回滚旧资产(self):
        p1, p2, p3, p4, p5, posts = self._patched([(500, {"err": 1}), (201, {})])
        with p1, p2, p3, p4, p5:
            with self.assertRaises(SystemExit):
                self.rd.upload_asset("db-state", "crawl-latest.db.gz", self.tmp)
        self.assertEqual(posts, [b"NEWGZ", b"OLDGZ"], "失败后必须把旧资产传回去")

    def test_旧资产备份失败时不删旧(self):
        deletes = []

        def api(method, url, **kw):
            if method == "DELETE":
                deletes.append(url)
            return (200, {})

        p1, p2, p3, p4, p5, posts = self._patched(
            [(201, {})], backup_exc=OSError("SSL EOF"), api=api)
        with p1, p2, p3, p4, p5:
            with self.assertRaises(SystemExit):
                self.rd.upload_asset("db-state", "crawl-latest.db.gz", self.tmp)
        self.assertEqual(deletes, [], "备份读不回来就绝不能删旧资产")
        self.assertEqual(posts, [], "也不应尝试上传新资产")


if __name__ == "__main__":
    unittest.main()
