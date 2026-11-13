#!/usr/bin/env python3
"""QA · Sprint 1 独立补强：kill switch / 线上写入守卫 / --market-only / 哨兵端到端。

覆盖既有测试的缺口：
  * ``SB_DATA_V2=0`` → **3 个脚本 × 全部子命令**均 no-op、零输出文件（含 .tmp）；
  * ``--out .../public/data/latest`` 被 **main() 与 resolve_out 双重 REJECT**（AQ3=B），
    并断言真实线上 ``stocks.json`` 逐字节不变、仍为旧 ``stockStats[]`` + ``qh/qtp/qap``；
  * ``export_market.py --market-only``：只连白名单 2 库（含 crawl_data.db 存在时也不碰）、
    FLAT 平铺在 ``data/latest/`` 根、不写 auction.json 与 3 个下线切片（AQ1=A / F5）；
  * ``em_contract_probe.py`` 成功/失败两分支 **经 main() 落盘 today.json**，并从写出的
    today.json 回读验证 ``ok=false`` → 下游 0 请求（不是内存态占位）；
  * F6：3 个脚本统一 import ``src.utils.atomic._atomic_json``（同一函数对象）。
本文件不修改任何实现源码、不写线上目录、全程离线、dry-run 产物仅 /tmp。
运行: python3 scripts/test_qa_s1_guards_probe.py
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_snapshot as ps   # noqa: E402
import export_market as em     # noqa: E402
import em_contract_probe as ep # noqa: E402
from src.utils.atomic import _atomic_json  # noqa: E402

DATE = "2026-11-12"
ONLINE_LATEST = ROOT.parent / "stockboard-app" / "public" / "data" / "latest"
ONLINE_STOCKS = ONLINE_LATEST / "stocks.json"


def make_market_dbs(db_dir, with_crawl_db=True):
    """两个白名单市场库 + 可选的 crawl_data.db（真实存在，用于证明从不打开）。"""
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
    if with_crawl_db:
        c = sqlite3.connect(str(Path(db_dir) / "crawl_data.db"))
        c.execute("CREATE TABLE positions (id INTEGER)")
        c.execute("INSERT INTO positions VALUES (1)")
        c.commit()
        c.close()
    return Path(db_dir)


class TestKillSwitchAllScripts(unittest.TestCase):
    """Epic 4：SB_DATA_V2=0 → 3 脚本全部子命令 no-op、零输出文件。"""

    def test_三脚本全部子命令_零输出文件(self):
        cases = [
            ("player_snapshot --rank", ps.main, ["--rank", "--demo"]),
            ("player_snapshot --midday", ps.main, ["--midday", "--demo"]),
            ("player_snapshot --close", ps.main, ["--close", "--demo"]),
            ("export_market --market-only", em.main,
             ["--market-only", "--db-dir", "__NO_SUCH_DB_DIR__"]),
            ("em_contract_probe --demo", ep.main, ["--demo"]),
            ("em_contract_probe --fixtures", ep.main,
             ["--fixtures", "__NO_SUCH_FIXTURES__"]),
        ]
        for label, fn, base_argv in cases:
            with self.subTest(case=label):
                out = Path(tempfile.mkdtemp())
                with patch.dict(os.environ, {"SB_DATA_V2": "0"}):
                    self.assertTrue(ps.kill_switch_active())
                    self.assertTrue(em.kill_switch_active())
                    self.assertTrue(ep.kill_switch_active())
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = fn(base_argv + ["--out", str(out)])
                self.assertEqual(rc, 0, f"{label} 应正常 no-op 退出")
                self.assertIn("no-op", buf.getvalue())
                self.assertEqual(list(out.iterdir()), [], f"{label} 产出了文件")

    def test_kill_switch_仅精确匹配0_其它值不触发(self):
        for value, expected in (("0", True), (" 0 ", True), ("1", False),
                                ("", False), ("false", False), ("00", False)):
            with self.subTest(SB_DATA_V2=value):
                with patch.dict(os.environ, {"SB_DATA_V2": value}):
                    for mod in (ps, em, ep):
                        self.assertEqual(mod.kill_switch_active(), expected)


class TestOnlineWriteGuard(unittest.TestCase):
    """AQ3=B：--out public/data/latest 被拒；线上 stocks.json 保持旧 schema。"""

    def setUp(self):
        self.online_before = ONLINE_STOCKS.read_bytes()

    def tearDown(self):
        self.assertEqual(ONLINE_STOCKS.read_bytes(), self.online_before,
                         "线上 stocks.json 不得被 QA 测试改动一个字节")

    def test_线上stocks_json_为P3新schema(self):
        # S4 放行后 AQ3=B 已生效：零兼容代码，旧 stockStats / qh/qtp/qap 不得残留。
        doc = json.loads(self.online_before.decode("utf-8"))
        self.assertEqual(set(doc.keys()), {"date", "snapshot", "updatedAt", "stats"})
        self.assertNotIn("stockStats", doc)
        self.assertTrue(doc["stats"])
        first = doc["stats"][0]
        self.assertEqual(set(first.keys()), {"code", "name", "h", "tp", "ap"})

    def test_三脚本resolve_out_拒绝真实线上路径(self):
        for mod in (ps, em, ep):
            with self.subTest(mod=mod.__name__):
                with self.assertRaises(SystemExit) as ctx:
                    mod.resolve_out(str(ONLINE_LATEST))
                self.assertIn("拒绝写入线上路径", str(ctx.exception))

    def test_三脚本main_拒绝线上out_相对与绝对形式(self):
        cases = [
            (ps.main, ["--rank", "--demo"]),
            (ps.main, ["--midday", "--demo"]),
            (ps.main, ["--close", "--demo"]),
            (em.main, ["--market-only"]),
            (ep.main, ["--demo"]),
        ]
        online_args = [str(ONLINE_LATEST), "stockboard-app/public/data/latest",
                       str(ONLINE_LATEST).replace("public/data/latest", "public/data/latest/")]
        for fn, base_argv in cases:
            for out_arg in online_args:
                with self.subTest(fn=fn.__module__ + ":" + base_argv[0], out=out_arg):
                    # 确保 kill switch 未激活，否则会被 no-op 抢先返回
                    with patch.dict(os.environ, {"SB_DATA_V2": "1"}):
                        with self.assertRaises(SystemExit):
                            fn(base_argv + ["--out", out_arg])

    def test_线上目录未新增任何文件(self):
        before = {p.name for p in ONLINE_LATEST.iterdir()}
        for fn, base_argv in ((ps.main, ["--rank", "--demo"]),
                              (em.main, ["--market-only"]),
                              (ep.main, ["--demo"])):
            with patch.dict(os.environ, {"SB_DATA_V2": "1"}):
                with self.assertRaises(SystemExit):
                    fn(base_argv + ["--out", str(ONLINE_LATEST)])
        after = {p.name for p in ONLINE_LATEST.iterdir()}
        self.assertEqual(before, after, "线上 latest/ 文件集不得变化")


class TestMarketOnlyHardConstraints(unittest.TestCase):
    """AC-1.2.5/1.2.6/1.2.7 / F5 / AQ1=A。"""

    def setUp(self):
        self.dbs = make_market_dbs(tempfile.mkdtemp())
        self.out = Path(tempfile.mkdtemp())

    def test_ALLOWED_DBS_白名单恰为两市场库(self):
        self.assertEqual(em.ALLOWED_DBS, ("auction.db", "hot_rank.db"))
        self.assertTrue((self.dbs / "crawl_data.db").exists(), "存量库确实存在")

    def test_经main_只连2库_crawl_data_db存在也从不打开(self):
        opened = []
        real_connect = sqlite3.connect

        def spy(*args, **kwargs):
            opened.append(str(args[0]))
            return real_connect(*args, **kwargs)

        with patch.dict(os.environ, {"SB_DATA_V2": "1"}):
            with patch("export_market.sqlite3.connect", side_effect=spy):
                rc = em.main(["--market-only", "--out", str(self.out),
                              "--db-dir", str(self.dbs), "--date", DATE])
        self.assertEqual(rc, 0)
        self.assertEqual(len(opened), 2, "恰好 2 次连接")
        self.assertFalse(any("crawl_data.db" in p for p in opened),
                         f"打开了 crawl_data.db: {opened}")
        self.assertTrue(all("auction.db" in p or "hot_rank.db" in p for p in opened))
        self.assertTrue(all("mode=ro" in p for p in opened), "只读方式")

    def test_connect_ro_拒绝任意非白名单库名(self):
        for name in ("crawl_data.db", "players.db", "copy.db", "auction.db.bak",
                     "hot_rank.db.tmp", "core.db"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                em.connect_ro(self.dbs / name)

    def test_connect_ro_缺失白名单库_显式失败(self):
        with self.assertRaises(FileNotFoundError):
            em.connect_ro(Path(tempfile.mkdtemp()) / "auction.db")

    def test_FLAT平铺在data_latest根_无market子目录(self):
        flat = Path(tempfile.mkdtemp()) / "data" / "latest"   # 注意：不是 public/data/latest
        with patch.dict(os.environ, {"SB_DATA_V2": "1"}):
            rc = em.main(["--market-only", "--out", str(flat),
                          "--db-dir", str(self.dbs), "--date", DATE])
        self.assertEqual(rc, 0)
        names = {p.name for p in flat.iterdir()}
        self.assertEqual(names, set(em.MARKET_SLICES),
                         "AQ1=A: 4 切片平铺在 data/latest/ 根")
        self.assertFalse((flat / "market").exists(), "不迁 market/ 子目录")
        for p in flat.iterdir():
            self.assertTrue(p.is_file())

    def test_不写auction_json与3下线切片_无tmp残留(self):
        written = em.cmd_market_only(self.out, self.dbs, DATE)
        names = {p.name for p in self.out.iterdir()}
        self.assertEqual(set(written), set(em.MARKET_SLICES))
        self.assertNotIn("auction.json", names, "F5: single-writer 归 auction.yml")
        for downlined in ("my_positions.json", "strike_review.json", "six_history.json"):
            self.assertNotIn(downlined, names)
        self.assertFalse(any(n.endswith(".tmp") for n in names))
        for forbidden in em.FORBIDDEN_SLICES:
            self.assertIn(forbidden, ("auction.json", "my_positions.json",
                                      "strike_review.json", "six_history.json"))

    def test_切片内容源自对应市场库(self):
        em.cmd_market_only(self.out, self.dbs, DATE)
        self.assertEqual(json.loads((self.out / "hot_rank.json").read_text())["src"],
                         "hot_rank.db")
        for name in em.MARKET_SLICES:
            doc = json.loads((self.out / name).read_text())
            self.assertIn("date", doc)


class TestProbeEndToEnd(unittest.TestCase):
    """哨兵成功/失败两分支经 main() 落盘 today.json + ok=false 下游 0 请求。"""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.fixtures = Path(tempfile.mkdtemp())

    def _run(self, argv):
        buf = io.StringIO()
        with patch.dict(os.environ, {"SB_DATA_V2": "1"}):
            with redirect_stdout(buf):
                rc = ep.main(argv + ["--out", str(self.out), "--date", DATE])
        return rc, buf.getvalue()

    def _today(self):
        return json.loads((self.out / "today.json").read_text(encoding="utf-8"))

    def test_成功分支_写today_json_emContract_ok_true(self):
        rc, stdout = self._run(["--demo"])
        self.assertEqual(rc, 0)
        self.assertIn("emContract.ok=True", stdout)
        doc = self._today()
        self.assertEqual(set(doc["emContract"]), {"ok", "checkedAt"})
        self.assertIs(doc["emContract"]["ok"], True)
        self.assertTrue(doc["emContract"]["checkedAt"])
        self.assertNotIn("checks", doc["emContract"], "落盘只保留 ok/checkedAt")
        self.assertFalse(ep.should_skip_direct_connect(doc["emContract"]))

    def test_失败分支_写today_json_ok_false_经回读断言下游0请求(self):
        (self.fixtures / "contract.json").write_text(
            json.dumps(dict(ep.DEMO_CONTRACT, status=503)), encoding="utf-8")
        rc, stdout = self._run(["--fixtures", str(self.fixtures)])
        self.assertEqual(rc, 0)
        self.assertIn("ok=false", stdout)
        self.assertIn("0 请求", stdout)
        # ★ 从写出的 today.json 回读（不是内存态），驱动下游决策
        contract = self._today()["emContract"]
        self.assertEqual(set(contract), {"ok", "checkedAt"})
        self.assertIs(contract["ok"], False)
        self.assertTrue(contract["checkedAt"], "失败也须记录 checkedAt")
        requests_made = []
        if not ep.should_skip_direct_connect(contract):
            requests_made.append("rtV2")     # S4 useEmPlayer.js 的等价占位
        self.assertEqual(requests_made, [], "ok=false → 下游 0 次 EM 请求 / 全站 🟡")

    def test_失败分支_4项逐一失败_均落盘ok_false(self):
        bad_cases = [
            dict(ep.DEMO_CONTRACT, status=500),
            dict(ep.DEMO_CONTRACT, headers={"Content-Type": "application/json"}),
            dict(ep.DEMO_CONTRACT, body={"code": 9035, "data": {"x": 1}}),
            dict(ep.DEMO_CONTRACT, body={"code": 0, "data": {}}),
            dict(ep.DEMO_CONTRACT, token_present=True),
        ]
        for i, bad in enumerate(bad_cases):
            with self.subTest(case=i):
                (self.fixtures / "contract.json").write_text(
                    json.dumps(bad), encoding="utf-8")
                rc, _ = self._run(["--fixtures", str(self.fixtures)])
                self.assertEqual(rc, 0)
                self.assertIs(self._today()["emContract"]["ok"], False)

    def test_哨兵保留today_json其它字段_sticky语义(self):
        prior = {"date": DATE, "updatedAt": "11:30",
                 "tradedPlayerIds": ["900293400"],
                 "emContract": {"ok": False, "checkedAt": "old"}}
        (self.out / "today.json").write_text(json.dumps(prior), encoding="utf-8")
        self._run(["--demo"])
        doc = self._today()
        self.assertEqual(doc["tradedPlayerIds"], ["900293400"])
        self.assertEqual(doc["updatedAt"], "11:30")
        self.assertIs(doc["emContract"]["ok"], True, "09:26 哨兵写入成功值")

    def test_F6_三脚本统一import同一atomic函数(self):
        self.assertIs(ps._atomic_json, _atomic_json)
        self.assertIs(em._atomic_json, _atomic_json)
        self.assertIs(ep._atomic_json, _atomic_json)
        for mod in (ps, em, ep):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            self.assertIn("from src.utils.atomic import _atomic_json", src,
                          f"{mod.__name__} 未统一 import F6 原子写")


if __name__ == "__main__":
    unittest.main()
