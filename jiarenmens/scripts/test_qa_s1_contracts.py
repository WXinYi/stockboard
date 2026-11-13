#!/usr/bin/env python3
"""QA · Sprint 1 独立补强：4 JSON 契约 **exact** 字段级校验 + 原子写等价/中断安全。

本文件不修改任何实现源码。用 --demo / 临时 SQLite 离线通道在 /tmp 复现完整时序
（09:26 榜单 → 哨兵 → 11:30 → 15:15 → --market-only），再对落盘 JSON 做严格断言：
  * exact 必填字段集合（不是 assertIn）；
  * 每个 players[].userId 非空、恰好 10 条 watched=true、prevClose/today 存在；
  * stocks.json snapshot∈{1130,close}、stats 精确字段、全文无 qh/qtp/qap；
  * today.json.emContract 恰好 {ok,checkedAt}；
  * 禁止字段白名单（token / HIDDEN_UIDS / hidden）；
  * 原子写与 export_json 逐行等价 + 中断不留截断文件。
运行: python3 scripts/test_qa_s1_contracts.py
"""
import inspect
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
import player_snapshot as ps        # noqa: E402
import export_market as em          # noqa: E402
import em_contract_probe as ep      # noqa: E402
from src.utils.atomic import _atomic_json, _atomic_text  # noqa: E402

DATE = "2026-11-12"
TS = "2026-11-12T09:26:00+08:00"
MID_AT = "2026-11-12T11:30:00+08:00"
CLOSE_AT = "2026-11-12T15:15:00+08:00"

FORBIDDEN_KEYS = ("ctToken", "utToken", "token", "HIDDEN_UIDS", "hidden")


def make_market_dbs(db_dir):
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


def build_full_pipeline(out):
    """离线复现 4 契约完整时序（零网络、仅 /tmp 临时目录）。"""
    ps.cmd_rank(out, ps.FixtureClient.from_data(ps.demo_bundle(DATE)), DATE, 300)
    ep.write_em_contract(out, ep.evaluate_probe(ep.DEMO_CONTRACT, TS), DATE, TS)
    ps.cmd_snapshot(out, ps.FixtureClient.from_data(ps.demo_bundle(DATE)),
                    DATE, ps.SNAPSHOT_MIDDAY, MID_AT, 1)
    ps.cmd_snapshot(out, ps.FixtureClient.from_data(ps.demo_bundle(DATE)),
                    DATE, ps.SNAPSHOT_CLOSE, CLOSE_AT, 1)
    em.cmd_market_only(out, make_market_dbs(tempfile.mkdtemp()), DATE)


def assert_no_forbidden(test, obj, where):
    if isinstance(obj, dict):
        for key, value in obj.items():
            test.assertNotIn(key, FORBIDDEN_KEYS, f"{where} 含禁止字段 {key}")
            assert_no_forbidden(test, value, f"{where}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            assert_no_forbidden(test, value, f"{where}[{i}]")


class TestExactContractFields(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        build_full_pipeline(self.out)

    def load(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def test_players_顶层字段_精确集合(self):
        doc = self.load("players.json")
        self.assertEqual(set(doc), {"date", "rankTime", "players"})
        self.assertEqual(doc["rankTime"], "09:26")
        self.assertEqual(doc["date"], DATE)

    def test_players_每行_精确6字段_userId非空(self):
        doc = self.load("players.json")
        self.assertTrue(doc["players"], "players[] 不得为空")
        for p in doc["players"]:
            self.assertEqual(set(p), {"id", "name", "userId", "watched", "prevClose", "today"},
                             f"{p.get('id')} 字段集合不精确")
            self.assertTrue(p["id"] and p["name"], "id/name 非空")
            self.assertTrue(p["userId"], f"{p['id']} userId 为空（AQ5=A 禁止）")
            self.assertIsInstance(p["watched"], bool)

    def test_恰好10条watched_true_其余全false(self):
        doc = self.load("players.json")
        watched = [p for p in doc["players"] if p["watched"] is True]
        self.assertEqual(len(watched), 10, "恰好 10 条 watched=true（不多不少）")
        self.assertEqual({p["id"] for p in watched},
                         {zh for zh, _ in ps.WATCHED_PLAYERS}, "10 人名单精确匹配")
        self.assertEqual(sum(1 for p in doc["players"] if p["watched"]), 10)
        non_watched = [p for p in doc["players"] if not p["watched"]]
        self.assertTrue(all(p["watched"] is False for p in non_watched))

    def test_每行_prevClose_与_today_存在且为对象(self):
        doc = self.load("players.json")
        for p in doc["players"]:
            self.assertIsInstance(p["prevClose"], dict, f"{p['id']} 缺 prevClose 对象")
            self.assertIsInstance(p["today"], dict, f"{p['id']} 缺 today 对象")
            # --close 应已刷新 prevClose（收益字段），today 应有打补丁字段
            self.assertIn("totalReturn", p["prevClose"])
            self.assertEqual(set(p["today"]), {"updatedAt", "tradeCount"})

    def test_stocks_顶层精确字段_枚举_无旧键(self):
        doc = self.load("stocks.json")
        self.assertEqual(set(doc), {"date", "snapshot", "updatedAt", "stats"})
        self.assertIn(doc["snapshot"], ("1130", "close"))
        self.assertEqual(doc["snapshot"], "close", "完整时序末态为 15:15 close")
        self.assertNotIn("stockStats", doc, "新契约不得残留旧键")

    def test_stocks_stats_精确字段_全文无_qh_qtp_qap(self):
        doc = self.load("stocks.json")
        raw = (self.out / "stocks.json").read_text(encoding="utf-8")
        for token in ("qh", "qtp", "qap", "stockStats"):
            self.assertNotIn('"%s"' % token, raw, f"stocks 中出现禁止/旧口径键 {token}")
        self.assertTrue(doc["stats"], "stats[] 不得为空")
        for s in doc["stats"]:
            self.assertEqual(set(s), {"code", "name", "h", "tp", "ap"})

    def test_today_精确字段_emContract_恰好ok与checkedAt(self):
        doc = self.load("today.json")
        self.assertEqual(set(doc), {"date", "updatedAt", "tradedPlayerIds", "emContract"})
        self.assertEqual(set(doc["emContract"]), {"ok", "checkedAt"})
        self.assertIsInstance(doc["emContract"]["ok"], bool)
        self.assertTrue(doc["emContract"]["checkedAt"])
        self.assertIsInstance(doc["tradedPlayerIds"], list)

    def test_4契约_禁止字段白名单递归扫描(self):
        for name in ("players.json", "stocks.json", "today.json", *em.MARKET_SLICES):
            assert_no_forbidden(self, self.load(name), name)

    def test_snapshot_枚举守卫_两合法三非法(self):
        for snap in ("1130", "close"):
            self.assertEqual(ps.build_stocks_doc(DATE, snap, TS, [])["snapshot"], snap)
        for bad in ("0900", "CLOSE", "", None, "1130 "):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ps.build_stocks_doc(DATE, bad, TS, [])

    def test_落盘文件集_严格3核心加4市场切片_无第5契约_no_market子目录(self):
        names = {p.name for p in self.out.iterdir()}
        self.assertEqual(names, {"players.json", "stocks.json", "today.json", *em.MARKET_SLICES})
        self.assertFalse((self.out / "archive_players.json").exists(), "6B: 不产第 5 个 JSON")
        self.assertFalse((self.out / "market").exists(), "AQ1=A: 不迁 market/ 子目录")
        self.assertNotIn("auction.json", names, "F5: --market-only 不写 auction.json")
        for downlined in ("my_positions.json", "strike_review.json", "six_history.json"):
            self.assertNotIn(downlined, names)


class TestAtomicEquivalenceAndInterruption(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_与_export_json_逐行源码等价(self):
        """F6 / §10.1 DoD：不是仅字节等价，而是函数体源码逐行等价。"""
        try:
            from export_json import _atomic_json as legacy_json, _atomic_text as legacy_text  # noqa: E402
        except ImportError:
            self.skipTest("export_json.py 已删除（S2 后不适用）")
        self.assertEqual(inspect.getsource(_atomic_json).strip(),
                         inspect.getsource(legacy_json).strip())
        self.assertEqual(inspect.getsource(_atomic_text).strip(),
                         inspect.getsource(legacy_text).strip())

    def test_多种载荷_与legacy逐字节等价(self):
        try:
            from export_json import _atomic_json as legacy  # noqa: E402
        except ImportError:
            self.skipTest("export_json.py 已删除（S2 后不适用）")
        objs = [
            {"中文": [1, 2, {"k": None}], "f": 1.25},
            {"a": [], "b": {}, "c": True, "d": False, "e": 0},
            {"unicode": "月儿兰🐟", "nested": {"x": [1.5, -2.0, 0]}},
            [1, "中文", None],
            {},
        ]
        for i, obj in enumerate(objs):
            p1, p2 = self.tmp / f"new{i}.json", self.tmp / f"old{i}.json"
            _atomic_json(p1, obj)
            legacy(p2, obj)
            self.assertEqual(p1.read_bytes(), p2.read_bytes(), f"载荷 {i} 逐字节不等价")

    def test_写入中断_os_replace前抛错_目标文件保持完整不截断(self):
        """中断发生在 os.replace 之前：目标文件仍是上一次完整值，绝不出现半截 JSON。"""
        p = self.tmp / "target.json"
        _atomic_json(p, {"v": "old", "list": [1, 2, 3]})
        before = p.read_bytes()
        with patch("src.utils.atomic.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                _atomic_json(p, {"v": "new"})
        self.assertEqual(p.read_bytes(), before, "中断后目标文件字节不变")
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"v": "old", "list": [1, 2, 3]})
        self.assertEqual(json.loads((Path(str(p) + ".tmp")).read_text(encoding="utf-8")),
                         {"v": "new"}, "新内容完整留在 .tmp，未污染目标")

    def test_成功路径_tmp经os_replace消失_且目标合法(self):
        p = self.tmp / "ok.json"
        with patch("src.utils.atomic.os.replace", wraps=os.replace) as mocked:
            _atomic_json(p, {"k": "v"})
        mocked.assert_called_once()
        self.assertEqual(Path(mocked.call_args[0][0]).name, "ok.json.tmp")
        self.assertFalse(Path(str(p) + ".tmp").exists())
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"k": "v"})


if __name__ == "__main__":
    unittest.main()
