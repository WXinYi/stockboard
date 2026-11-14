#!/usr/bin/env python3
"""QA · Sprint 1 独立补强：--rank / EM_RANK_LIMIT / EM_TRADE_PAGES / AQ5=A 硬连线无回退。

补既有 test_player_snapshot.py 的覆盖缺口：
  * ``EM_RANK_LIMIT`` 通过 **main() 的 os.environ**（而非直接传 cmd_rank 参数）生效；
  * 默认 limit=300 → 恰好 75 次分页；多值 override（20/40/300/500）逐项核对；
  * ``EM_TRADE_PAGES`` 通过 **main() 的 os.environ** 生效（D3 默认 1，可 override）；
  * AQ5=A：userId 硬连线自排行榜每行 ``userid``、0 额外请求、无回退分支：
    字段冲突时以榜单行值胜出；缺字段显式失败；dedupe 纯函数源码无 DB/映射表引用。
本文件不修改任何实现源码；全程离线（--demo / FixtureClient），产物仅 /tmp。
运行: python3 scripts/test_qa_s1_rank_env.py
"""
import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_snapshot as ps  # noqa: E402

DATE = "2026-11-12"


def clean_env(**overrides):
    """构造不含 3 个灰度开关的确定性环境（clear=True 全量替换，避免外部污染）。"""
    env = {k: v for k, v in os.environ.items()
           if k not in ("EM_RANK_LIMIT", "EM_TRADE_PAGES", "SB_DATA_V2")}
    env.update(overrides)
    return env


class TestRankLimitEnv(unittest.TestCase):
    """EM_RANK_LIMIT 灰度开关经 main() 生效（不是绕过 main 直接调 cmd_rank）。"""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def _run_rank_main(self, env_overrides):
        client = ps.FixtureClient.from_data(ps.demo_bundle(DATE))
        with patch.dict(os.environ, clean_env(**env_overrides), clear=True):
            with patch.object(ps, "make_client", return_value=client):
                rc = ps.main(["--rank", "--demo", "--out", str(self.out), "--date", DATE])
        self.assertEqual(rc, 0)
        return client

    def test_默认limit300_恰好75次分页_经main(self):
        self.assertEqual(ps.DEFAULT_RANK_LIMIT, 300)
        self.assertEqual(ps.RANK_PAGE_SIZE, 20)
        self.assertEqual(len(ps.RANK_TYPES), 5)
        client = self._run_rank_main({})   # env 完全不含 EM_RANK_LIMIT
        self.assertEqual(client.rank_calls(), 75, "5 榜 × 300/20 = 75（K3）")

    def test_EM_RANK_LIMIT_override_多值(self):
        cases = {"20": 5, "40": 10, "60": 15, "300": 75, "500": 125}
        for value, expected in cases.items():
            with self.subTest(EM_RANK_LIMIT=value):
                c = self._run_rank_main({"EM_RANK_LIMIT": value})
                self.assertEqual(c.rank_calls(), expected,
                                 f"EM_RANK_LIMIT={value} 应产生 {expected} 次分页")

    def test_EM_RANK_LIMIT_分页offset递增_每榜独立(self):
        c = self._run_rank_main({"EM_RANK_LIMIT": "40"})
        pages = [x for x in c.calls if x[0] == "rank_page"]
        self.assertEqual(len(pages), 10)
        for rank_type in ps.RANK_TYPES:
            offsets = [p[2] for p in pages if p[1] == rank_type]
            self.assertEqual(offsets, [0, 20], f"{rank_type} 分页 offset 应为 [0,20]")
            for p in pages:
                if p[1] == rank_type:
                    self.assertLessEqual(p[3], ps.RANK_PAGE_SIZE)

    def test_EM_RANK_LIMIT_非法值_显式失败_FailureNeFact(self):
        with patch.dict(os.environ, clean_env(EM_RANK_LIMIT="abc"), clear=True):
            with patch.object(ps, "make_client",
                              return_value=ps.FixtureClient.from_data(ps.demo_bundle(DATE))):
                with self.assertRaises(ValueError):
                    ps.main(["--rank", "--demo", "--out", str(self.out), "--date", DATE])


class TestAq5HardwiredNoFallback(unittest.TestCase):
    """AQ5=A：userId 直接取排行榜行 userid；无回退分支、无逐人补解析。"""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def test_字段冲突_榜单行userid胜出_不回退映射表(self):
        """把入榜 watched 选手的 userid 改成与 NEW_USER_IDS 不同的哨兵值，
        产物必须采用榜单行值 → 证明硬连线、无 mapping fallback。"""
        bundle = ps.demo_bundle(DATE)
        sentinel = "SENTINEL_RANK_ROW_UID_001"
        for rt in bundle["rank"]:
            for row in bundle["rank"][rt]:
                if row["zjzh"] == "900293400":
                    row["userid"] = sentinel
        client = ps.FixtureClient.from_data(bundle)
        doc = ps.cmd_rank(self.out, client, DATE, 300)
        by = {p["id"]: p for p in doc["players"]}
        self.assertEqual(by["900293400"]["userId"], sentinel, "榜单行 userid 必须胜出")
        self.assertNotEqual(by["900293400"]["userId"],
                            ps.WATCHED_USER_IDS["900293400"], "不得回退静态映射值")
        self.assertEqual(client.rank_calls(), 75, "0 额外补解析请求")

    def test_大规模榜单_每行userId非空且逐行可追溯(self):
        """5 榜各 60 行 + 名单内 watched 强制插入 → 60+N 人，逐行 userId 可追溯、无缺失。"""
        rows = [{"zjzh": f"9001{i:05d}", "zhuheName": f"选手{i}",
                 "userid": f"UID{i:06d}"} for i in range(60)]
        bundle = {"rank": {rt: list(rows) for rt in ps.RANK_TYPES},
                  "yield": {}, "position": {}, "trades": {}}
        client = ps.FixtureClient.from_data(bundle)
        doc = ps.cmd_rank(self.out, client, DATE, 300)
        players = doc["players"]
        n = len(ps.WATCHED_PLAYERS)
        self.assertEqual(len(players), 60 + n, "60 去重 + N 榜外 watched")
        by = {p["id"]: p for p in players}
        for r in rows:
            self.assertEqual(by[r["zjzh"]]["userId"], r["userid"])
            self.assertEqual(by[r["zjzh"]]["name"], r["zhuheName"])
            self.assertFalse(by[r["zjzh"]]["watched"])
        for p in players:
            self.assertTrue(p["userId"], f"{p['id']} 缺 userId")
        self.assertEqual(sum(1 for p in players if p["watched"]), n)
        self.assertEqual(client.rank_calls(), 75)

    def test_缺userid键_显式抛错_不静默填空(self):
        rows = {"10004": [{"zjzh": "900000009", "zhuheName": "缺字段"}]}
        with self.assertRaises(ValueError) as ctx:
            ps.dedupe_rank_rows(rows)
        self.assertIn("禁止回退分支", str(ctx.exception))

    def test_dedupe纯函数源码_无DB与映射表引用_无回退分支(self):
        """静态守卫：dedupe_rank_rows 不得引用 DB / NEW_USER_IDS / 网络客户端。"""
        src = inspect.getsource(ps.dedupe_rank_rows)
        for forbidden in ("WATCHED_USER_IDS", "NEW_USER_IDS", "sqlite3",
                          "crawl_data", "requests", "api_detail", "player_detail"):
            self.assertNotIn(forbidden, src, f"dedupe_rank_rows 出现回退依赖: {forbidden}")
        self.assertIn('row.get("userid"', src, "必须硬连线从行取 userid")
        self.assertIn("raise ValueError", src, "缺字段必须显式抛错")


class TestTradePagesEnv(unittest.TestCase):
    """D3 固定 1 页 + EM_TRADE_PAGES 经 main() 生效；作用域仅本链路。"""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        ps.cmd_rank(self.out, ps.FixtureClient.from_data(ps.demo_bundle(DATE)), DATE, 300)
        self.n = len(json.loads((self.out / "players.json").read_text(encoding="utf-8"))["players"])

    def _run_snapshot_main(self, subcmd, env_overrides):
        client = ps.FixtureClient.from_data(ps.demo_bundle(DATE))
        with patch.dict(os.environ, clean_env(**env_overrides), clear=True):
            with patch.object(ps, "make_client", return_value=client):
                rc = ps.main([subcmd, "--demo", "--out", str(self.out), "--date", DATE])
        self.assertEqual(rc, 0)
        return client

    def test_默认EM_TRADE_PAGES_1_每人恰好1页(self):
        self.assertEqual(ps.DEFAULT_TRADE_PAGES, 1)
        c = self._run_snapshot_main("--midday", {})
        trades = [x for x in c.calls if x[0] == "trades"]
        self.assertEqual(len(trades), self.n, "D3: 每人恰好 1 页当日调仓")
        for t in trades:
            self.assertEqual(t[2], DATE, "beginDate=当日")
            self.assertEqual(t[3], DATE, "endDate=当日")
            self.assertEqual(t[4], 1, "page 固定 1")

    def test_EM_TRADE_PAGES_override_2与3_每页仅当日(self):
        for pages in (2, 3):
            with self.subTest(EM_TRADE_PAGES=pages):
                c = self._run_snapshot_main("--midday", {"EM_TRADE_PAGES": str(pages)})
                trades = [x for x in c.calls if x[0] == "trades"]
                self.assertEqual(len(trades), self.n * pages)
                self.assertEqual({t[4] for t in trades}, set(range(1, pages + 1)))
                self.assertTrue(all(t[2] == DATE and t[3] == DATE for t in trades),
                                "任一页都不得取历史 40 条")

    def test_close经main比midday恰好多N次yield_调仓页数一致(self):
        """AC-1.3（Review M-01 反转）：--close 仅比 --midday 多每人 1 次收益接口；
        两班均遵守 EM_TRADE_PAGES（默认 1 页），其余调用集合完全一致。"""
        from collections import Counter
        c_mid = self._run_snapshot_main("--midday", {})
        c_close = self._run_snapshot_main("--close", {})
        self.assertNotEqual(c_mid.calls, c_close.calls, "两班调用集合必须不同")
        self.assertEqual([x for x in c_close.calls if x[0] != "yield"], c_mid.calls,
                         "剔除 yield 后调用序列完全一致")
        self.assertEqual(sum(1 for x in c_mid.calls if x[0] == "yield"), 0)
        diff = Counter(x[0] for x in c_close.calls) - Counter(x[0] for x in c_mid.calls)
        self.assertEqual(dict(diff), {"yield": self.n}, "差异恰为 N 次 yield")
        self.assertEqual(sum(1 for x in c_mid.calls if x[0] == "trades"),
                         sum(1 for x in c_close.calls if x[0] == "trades"),
                         "两班调仓页数一致（D3）")
        doc = json.loads((self.out / "stocks.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["snapshot"], "close")

    def test_EM_TRADE_PAGES_非法值_显式失败(self):
        with patch.dict(os.environ, clean_env(EM_TRADE_PAGES="x"), clear=True):
            with patch.object(ps, "make_client",
                              return_value=ps.FixtureClient.from_data(ps.demo_bundle(DATE))):
                with self.assertRaises(ValueError):
                    ps.main(["--midday", "--demo", "--out", str(self.out), "--date", DATE])


if __name__ == "__main__":
    unittest.main()
