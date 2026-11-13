#!/usr/bin/env python3
"""Story 1.5 单测：4 个 JSON 契约 schema（必填字段 / snapshot 枚举 / userId 非空 / 恰好 4 契约 / 禁止字段白名单）。

离线跑完整 09:26 → 哨兵 → 11:30 → 15:15 时序 + --market-only，产出 4 契约后逐项校验。
运行: python3 scripts/test_json_schema.py
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_snapshot as ps        # noqa: E402
import export_market as em          # noqa: E402
import em_contract_probe as ep      # noqa: E402

DATE = "2026-11-12"
TS = "2026-11-12T09:26:00+08:00"
MID_AT = "2026-11-12T11:30:00+08:00"
CLOSE_AT = "2026-11-12T15:15:00+08:00"

# 严格 4 个契约（PRD-Q4=6B：不产第 5 个 JSON）；market 为平铺切片逻辑集合。
CONTRACTS = ("players", "stocks", "today", "market")
# [A6] 禁止字段白名单：任何 token / HIDDEN_UIDS / hidden 标志不得进静态产物。
FORBIDDEN_KEYS = ("ctToken", "utToken", "token", "HIDDEN_UIDS", "hidden")


def assert_no_forbidden(test, obj, where):
    if isinstance(obj, dict):
        for key, value in obj.items():
            test.assertNotIn(key, FORBIDDEN_KEYS, f"{where} 含禁止字段 {key}")
            assert_no_forbidden(test, value, f"{where}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            assert_no_forbidden(test, value, f"{where}[{i}]")


def make_market_dbs(db_dir):
    """最小空表市场库（导出器对空表输出空形状，仍可校验 schema）。"""
    a = sqlite3.connect(str(Path(db_dir) / "auction.db"))
    a.execute("CREATE TABLE bid_pool (date TEXT, code TEXT, name TEXT, turnover_ratio REAL)")
    a.execute("CREATE TABLE market_breadth (date TEXT, zt INTEGER)")
    a.execute("CREATE TABLE broken_pool (date TEXT, code TEXT, name TEXT, break_times INTEGER, "
              "change_pct REAL, turnover REAL, height INTEGER)")
    a.commit()
    a.close()
    h = sqlite3.connect(str(Path(db_dir) / "hot_rank.db"))
    h.execute("CREATE TABLE hot_rank (date TEXT, snap TEXT, rank INTEGER, code TEXT, name TEXT, rise REAL)")
    h.commit()
    h.close()
    return Path(db_dir)


def build_all_four(out):
    """离线复现 4 契约完整时序（零网络、仅临时目录）。"""
    ps.cmd_rank(out, ps.FixtureClient.from_data(ps.demo_bundle(DATE)), DATE, 300)
    ep.write_em_contract(out, ep.evaluate_probe(ep.DEMO_CONTRACT, TS), DATE, TS)
    ps.cmd_snapshot(out, ps.FixtureClient.from_data(ps.demo_bundle(DATE)),
                    DATE, ps.SNAPSHOT_MIDDAY, MID_AT, 1)
    ps.cmd_snapshot(out, ps.FixtureClient.from_data(ps.demo_bundle(DATE)),
                    DATE, ps.SNAPSHOT_CLOSE, CLOSE_AT, 1)
    em.cmd_market_only(out, make_market_dbs(tempfile.mkdtemp()), DATE)


class TestFourJsonContracts(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        build_all_four(self.out)

    def load(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def test_恰好4个契约_不产第5个JSON(self):
        names = {p.name for p in self.out.iterdir() if p.suffix == ".json"}
        expected = {"players.json", "stocks.json", "today.json", *em.MARKET_SLICES}
        self.assertEqual(names, expected, "6B: 无 archive_players.json 等第 5 个契约")
        # Review m-05：常量自比较 → 改为把 CONTRACTS 与实产文件逐一绑定的实质断言
        core = {"players": "players.json", "stocks": "stocks.json", "today": "today.json"}
        for contract, fname in core.items():
            self.assertIn(contract, CONTRACTS)
            self.assertIn(fname, names, f"{contract} 契约无对应产物")
        self.assertIn("market", CONTRACTS)
        self.assertTrue(all(s in names for s in em.MARKET_SLICES),
                        "market 逻辑集合应有平铺切片产物")
        self.assertEqual(len(set(CONTRACTS)), len(CONTRACTS), "契约名不得重复")

    def test_players_必填字段与_userId_非空(self):
        doc = self.load("players.json")
        for key in ("date", "rankTime", "players"):
            self.assertIn(key, doc)
        self.assertEqual(doc["rankTime"], "09:26")
        for p in doc["players"]:
            for key in ("id", "name", "userId", "watched", "prevClose", "today"):
                self.assertIn(key, p)
            self.assertTrue(p["userId"], "no missing userId（AQ5=A）")
            self.assertIsInstance(p["watched"], bool)
        assert_no_forbidden(self, doc, "players")

    def test_stocks_snapshot枚举_stats全量字段_无quality(self):
        doc = self.load("stocks.json")
        for key in ("date", "snapshot", "updatedAt", "stats"):
            self.assertIn(key, doc)
        self.assertIn(doc["snapshot"], ("1130", "close"))
        self.assertNotIn("stockStats", doc)
        for s in doc["stats"]:
            self.assertEqual(set(s), {"code", "name", "h", "tp", "ap"})
            for key in ("qh", "qtp", "qap"):
                self.assertNotIn(key, s)
        assert_no_forbidden(self, doc, "stocks")

    def test_today_必填字段与_emContract形状(self):
        doc = self.load("today.json")
        for key in ("date", "updatedAt", "tradedPlayerIds", "emContract"):
            self.assertIn(key, doc)
        self.assertEqual(set(doc["emContract"]), {"ok", "checkedAt"})
        self.assertIsInstance(doc["emContract"]["ok"], bool)
        self.assertIsInstance(doc["tradedPlayerIds"], list)
        assert_no_forbidden(self, doc, "today")

    def test_market_平铺集合锁定_不含下线切片(self):
        names = {p.name for p in self.out.iterdir()}
        for slice_name in em.MARKET_SLICES:
            self.assertIn(slice_name, names)
            assert_no_forbidden(self, self.load(slice_name), slice_name)
        for forbidden in em.FORBIDDEN_SLICES:
            self.assertNotIn(forbidden, names)
        self.assertFalse((self.out / "market").exists(), "AQ1=A: 不迁子目录")

    def test_snapshot_枚举值域_非法值被拒(self):
        for snap in (ps.SNAPSHOT_MIDDAY, ps.SNAPSHOT_CLOSE):
            self.assertIn(ps.build_stocks_doc(DATE, snap, TS, [])["snapshot"], ("1130", "close"))
        with self.assertRaises(ValueError):
            ps.build_stocks_doc(DATE, "0900", TS, [])

    def test_全链路产物均为合法JSON_无tmp残留(self):
        for p in self.out.iterdir():
            with open(p, "r", encoding="utf-8") as f:
                json.load(f)
            self.assertFalse(p.name.endswith(".tmp"))
        self.assertFalse(any(p.name.endswith(".tmp") for p in self.out.iterdir()))


if __name__ == "__main__":
    unittest.main()
