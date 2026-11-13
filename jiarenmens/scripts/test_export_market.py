#!/usr/bin/env python3
"""Story 1.3 单测：--market-only 绝不打开 crawl_data.db / 不写 auction.json / 平铺集合锁定（F5 / AQ1=A）。

运行: python3 scripts/test_export_market.py
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_market as em  # noqa: E402

DATE = "2026-11-12"


def make_dbs(db_dir):
    """临时建两个最小市场库 + 一个应被拒绝的 crawl_data.db（模拟存量库存在）。"""
    a = sqlite3.connect(str(Path(db_dir) / "auction.db"))
    a.execute("CREATE TABLE bid_pool (date TEXT, code TEXT, name TEXT, turnover_ratio REAL)")
    a.execute("INSERT INTO bid_pool VALUES (?, '000001', '平安银行', 3.5)", (DATE,))
    a.execute("CREATE TABLE market_breadth (date TEXT, zt INTEGER, dt INTEGER)")
    a.execute("INSERT INTO market_breadth VALUES (?, 30, 5)", (DATE,))
    a.execute("CREATE TABLE broken_pool (date TEXT, code TEXT, name TEXT, break_times INTEGER, "
              "change_pct REAL, turnover REAL, height INTEGER)")
    a.execute("INSERT INTO broken_pool VALUES (?, '600001', '甲', 2, 3.0, 10.0, 2)", (DATE,))
    a.commit()
    a.close()
    h = sqlite3.connect(str(Path(db_dir) / "hot_rank.db"))
    h.execute("CREATE TABLE hot_rank (date TEXT, snap TEXT, rank INTEGER, code TEXT, name TEXT, rise REAL)")
    h.execute("INSERT INTO hot_rank VALUES (?, 'am', 1, '000001', '平安银行', 2.1)", (DATE,))
    h.commit()
    h.close()
    c = sqlite3.connect(str(Path(db_dir) / "crawl_data.db"))
    c.execute("CREATE TABLE positions (id INTEGER)")
    c.commit()
    c.close()


class TestMarketOnly(unittest.TestCase):
    def setUp(self):
        self.dbs = Path(tempfile.mkdtemp())
        self.out = Path(tempfile.mkdtemp())
        make_dbs(self.dbs)

    def test_单测断言_从不打开_crawl_data_db(self):
        """Story 1.2 AC-1.2.5：spy 所有 sqlite3.connect 调用路径。"""
        real_connect = sqlite3.connect
        opened = []

        def spy(*args, **kwargs):
            opened.append(str(args[0]))
            return real_connect(*args, **kwargs)

        with patch("export_market.sqlite3.connect", side_effect=spy):
            em.cmd_market_only(self.out, self.dbs, DATE)
        self.assertEqual(len(opened), 2, "恰好打开两个市场库")
        self.assertFalse(any("crawl_data.db" in p for p in opened),
                         f"--market-only 打开了 crawl_data.db: {opened}")
        self.assertTrue(all(("auction.db" in p or "hot_rank.db" in p) for p in opened))
        self.assertTrue(all("mode=ro" in p for p in opened), "只读方式打开")

    def test_白名单硬拒绝_crawl_data_db(self):
        with self.assertRaises(ValueError):
            em.connect_ro(self.dbs / "crawl_data.db")

    def test_不写_auction_json_与三个下线切片(self):
        em.cmd_market_only(self.out, self.dbs, DATE)
        names = {p.name for p in self.out.iterdir()}
        for forbidden in em.FORBIDDEN_SLICES:
            self.assertNotIn(forbidden, names, f"不得产出 {forbidden}（F5 / Q1=B）")

    def test_产物恰好4个_平铺无子目录(self):
        written = em.cmd_market_only(self.out, self.dbs, DATE)
        self.assertEqual(len(written), 4)
        self.assertEqual(tuple(sorted(written)), tuple(sorted(em.MARKET_SLICES)))
        for p in self.out.iterdir():
            self.assertTrue(p.is_file(), "AQ1=A: FLAT 平铺, 无 market/ 子目录")
        self.assertFalse((self.out / "market").exists())

    def test_切片内容来自市场库且形状可校验(self):
        em.cmd_market_only(self.out, self.dbs, DATE)
        hot = json.loads((self.out / "hot_rank.json").read_text(encoding="utf-8"))
        self.assertEqual(hot["src"], "hot_rank.db")
        self.assertEqual(hot["rows"][0]["code"], "000001")
        breadth = json.loads((self.out / "market_breadth.json").read_text(encoding="utf-8"))
        self.assertEqual(breadth["row"]["zt"], 30)
        broken = json.loads((self.out / "broken_pool.json").read_text(encoding="utf-8"))
        self.assertEqual(broken["rows"][0]["code"], "600001")
        lianban = json.loads((self.out / "lianban_bid.json").read_text(encoding="utf-8"))
        self.assertEqual(lianban["top"][0]["code"], "000001")

    def test_拒绝线上路径(self):
        with self.assertRaises(SystemExit):
            em.resolve_out("/x/stockboard-app/public/data/latest")

    def test_SB_DATA_V2_0_noop_不写任何文件(self):
        with patch.dict(os.environ, {"SB_DATA_V2": "0"}):
            rc = em.main(["--market-only", "--out", str(self.out), "--db-dir", str(self.dbs)])
        self.assertEqual(rc, 0)
        self.assertEqual(list(self.out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
