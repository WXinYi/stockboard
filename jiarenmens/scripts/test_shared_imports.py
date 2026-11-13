#!/usr/bin/env python3
"""Commit-R1 共享模块 import 冒烟（AQ2=A / T2.1.3）。

在 ``main.py`` / ``notify_daily.py`` / ``export_json.py`` **已删除**的状态下，
验证 R1 共享模块与两个仍然保留的入口（watchdog / auction_scan）零 ImportError。
零网络。
"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS.parent))
sys.path.insert(0, str(SCRIPTS))


class TestLegacyModulesRemoved(unittest.TestCase):
    def test_legacy_files_gone(self):
        for name in ("main.py", "notify_daily.py", "export_json.py"):
            self.assertFalse((SCRIPTS / name).exists(),
                             f"{name} 应已随 Commit-R1 删除（R1 迁移必须先于删除）")


class TestSharedModules(unittest.TestCase):
    def test_config_package_keeps_public_names(self):
        # B1: src/config.py 包化后, 既有 from src.config import ... 必须继续可用
        from src.config import AUCTION_OUT, DATA_DIR, BASE_DIR
        self.assertTrue(str(AUCTION_OUT).endswith("latest/auction.json"))
        self.assertTrue(str(DATA_DIR).endswith("jiarenmens/data"))
        self.assertTrue(str(BASE_DIR).endswith("jiarenmens"))

    def test_watched_players_single_source(self):
        from src.config.watched import WATCHED, WATCHED_PLAYERS
        self.assertEqual(len(WATCHED_PLAYERS), 10)
        self.assertEqual(len(WATCHED), 10)
        self.assertEqual(WATCHED_PLAYERS[0][0], "900456476")   # 顺序 = 置顶顺序

    def test_quotes_and_state(self):
        from src.notify.quotes import fetch_quotes, tencent_auction_amt
        from src.notify.watchdog_state import load_watchdog_pushed
        self.assertEqual(fetch_quotes([]), {})
        self.assertTrue(callable(tencent_auction_amt))
        self.assertIsInstance(load_watchdog_pushed("1999-01-01"), set)

    def test_atomic_helpers(self):
        from src.utils.atomic import _atomic_json, _atomic_text
        self.assertTrue(callable(_atomic_json))
        self.assertTrue(callable(_atomic_text))


class TestKeptEntrypoints(unittest.TestCase):
    def test_watchdog_import(self):
        import watchdog
        from src.config.watched import WATCHED_PLAYERS
        self.assertEqual(watchdog.WATCHED_PLAYERS, WATCHED_PLAYERS)
        self.assertEqual(watchdog.WINDOW_START, "0926")
        self.assertTrue(callable(watchdog.fetch_quotes))

    def test_auction_scan_import(self):
        # B2: 竞价补算改从共享模块取, 不再依赖已删的 export_json.py
        import auction_scan
        self.assertTrue(callable(auction_scan._tencent_auction_amt))


if __name__ == "__main__":
    unittest.main(verbosity=2)
