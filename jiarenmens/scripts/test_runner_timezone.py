#!/usr/bin/env python3
"""runner 时区回归（2026-11-25 加固）。

GitHub runner 的 Python 环境是 UTC：裸 date.today()/datetime.now() 在北京 00:00~07:59 会落到
前一天。本组把时钟冻结在 UTC 2026-11-24 17:30（= 北京 11-25 01:30，两边日期已经不同），验证
生产路径一律按北京日期：热层日/周快照命名、超龄清理、库龄闸门、marker；竞价快照日期校验；
陈旧池守卫；可见性状态日期。
只拦截 datetime.now()，冻结的是带显式时区的时刻 —— 本组在任意宿主机时区下结论一致。"""
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auction_scan  # noqa: E402
import release_db as rd  # noqa: E402
from src.spiders import auction_spider  # noqa: E402
from src.utils import visibility  # noqa: E402

UTC_DAY, BJ_DAY = date(2026, 11, 24), date(2026, 11, 25)
FROZEN_UTC = datetime(2026, 11, 24, 17, 30, 0, tzinfo=timezone.utc)   # = 北京 11-25 01:30


def freeze(module):
    """把模块里的 datetime 类换成冻结版: 只接管 now(), 其余类方法沿用真实 datetime。"""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN_UTC.astimezone(tz) if tz is not None else FROZEN_UTC.replace(tzinfo=None)
    return patch.object(module, "datetime", _Frozen)


class TestHotLayerDates(unittest.TestCase):
    """release_db: 日/周快照命名、超龄清理、库龄闸门、marker。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_用例前提_冻结时刻两边日期不同(self):
        self.assertEqual(FROZEN_UTC.date(), UTC_DAY)
        self.assertEqual(FROZEN_UTC.astimezone(timezone(timedelta(hours=8))).date(), BJ_DAY)

    def test_今天按北京日期(self):
        with freeze(rd):
            self.assertEqual(rd._today_bj(), BJ_DAY)

    def test_库龄默认今天按北京日期(self):
        with freeze(rd):
            self.assertEqual(rd._days_since(UTC_DAY).days, 1, "UTC 口径会算成 0 天")
            self.assertIsNone(rd._days_since(None))

    def test_marker的checked_at是北京时间且带时区(self):
        p = self.tmp / ".auction_restore_source"
        with freeze(rd):
            rd._write_marker("hot", False, path=p)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["checked_at"],
                         "2026-11-25T01:30:00+08:00")

    def test_日周快照命名与超龄清理按北京日期(self):
        db = self.tmp / "auction.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE t (x INTEGER)")
        c.commit()
        c.close()
        targets = {"auction": dict(rd.TARGETS["auction"], db=db, daily_keep=30, weekly_keep=26)}
        # 北京口径 cutoff=10-26: 10-25 删、10-26 留; UTC 口径 cutoff=10-25 会把 10-25 也留下
        assets = [{"name": "auction-2026-10-25.db.gz", "url": "u-old"},
                  {"name": "auction-2026-10-26.db.gz", "url": "u-kept"},
                  {"name": "auction-latest.db.gz", "url": "u-latest"}]
        uploaded, deleted = [], []

        def fake_upload(tag, name, path, body=""):
            uploaded.append(name)

        with freeze(rd), \
                patch.object(rd, "TARGETS", targets), \
                patch.object(rd, "_upload_baseline_ok", return_value=True), \
                patch.object(rd, "upload_asset", side_effect=fake_upload), \
                patch.object(rd, "get_release", return_value={"assets": assets}), \
                patch.object(rd, "_token", return_value="test-token"), \
                patch.object(rd, "_api", side_effect=lambda m, u, **k: deleted.append((m, u))):
            rd.cmd_upload_what("auction")
        self.assertEqual(uploaded, ["auction-latest.db.gz",
                                    "auction-2026-11-25.db.gz", "auction-2026-W48.db.gz"])
        self.assertEqual(deleted, [("DELETE", "u-old")], "只删北京口径下超龄的那一个")
        self.assertTrue(db.exists(), "源库不受影响")


class TestAuctionSnapshotDateGuard(unittest.TestCase):
    """AuctionStore._validate_date: 竞价快照只收 最近 3 天 且 不晚于今天。"""

    def test_今天按北京日期(self):
        with freeze(auction_spider):
            self.assertEqual(auction_spider._today_bj(), BJ_DAY)

    def test_校验按北京日期(self):
        validate = auction_spider.AuctionStore._validate_date
        with freeze(auction_spider):
            validate("2026-11-25")                      # 北京今天 → 放行
            validate("2026-11-22")                      # 距今 3 天 → 放行
            with self.assertRaises(ValueError):
                validate("2026-11-26")                  # 北京明天 → 拒绝
            with self.assertRaises(ValueError):
                validate("2026-11-21")                  # 距今 4 天 → 过旧
            with self.assertRaises(ValueError):
                validate("2026-13-45")                  # 非法日历日


class TestStalePoolGuard(unittest.TestCase):
    """竞价班陈旧池守卫: 池日距今日 > 4 个自然日告警。"""

    def test_滞后天数按北京日期(self):
        with freeze(auction_scan):
            self.assertEqual(auction_scan.pool_lag_days("2026-11-24"), 1, "UTC 口径会算成 0")
            self.assertEqual(auction_scan.pool_lag_days("2026-11-20"), 5, "周五池 → 超过 4 天, 应告警")
            self.assertEqual(auction_scan.pool_lag_days("2026-11-21"), 4, "恰好 4 天, 不告警")

    def test_池日非法抛ValueError由调用方吞掉(self):
        with self.assertRaises(ValueError):
            auction_scan.pool_lag_days("not-a-date")


class TestVisibilityDate(unittest.TestCase):
    def test_首次隐藏日期按北京日期(self):
        p = Path(tempfile.mkdtemp()) / "player_visibility.json"
        with patch.object(visibility, "STATE", p), freeze(visibility):
            st = visibility.update({"zh-a": True, "zh-b": False})
        self.assertEqual(st["hidden"], {"zh-a": "2026-11-25"})
        self.assertEqual(st["updated_at"], "2026-11-25")
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["hidden"], {"zh-a": "2026-11-25"})


if __name__ == "__main__":
    unittest.main()
