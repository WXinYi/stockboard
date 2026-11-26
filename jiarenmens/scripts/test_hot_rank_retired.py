#!/usr/bin/env python3
"""人气榜链路下线锁（2026-11-25 用户拍板下线）。

链路 = crawl.yml「人气榜快照」步骤 + auction.yml 竞价主流程里的 am 快照 + HotRankStore(hot_rank.db)
+ export_market 的 hot_rank.json 切片。下线依据: 前端源码与线上 bundle 对人气榜引用为 0。
本文件把下线钉死, 防止后续改动把任一环悄悄加回来或只恢复一半:
  ① 后端入口不存在; ② 导出器只读 auction.db、只产 2 切片;
  ③ workflow 非注释行不再引用; ④ crawl 提交白名单 == 实际产出的契约与切片;
  ⑤ 数据文件已移除, 前端不得请求已下线的切片。
不依赖 PyYAML(CI 门禁只装 requests/bs4/lxml), workflow 一律按纯文本断言。"""
import re
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]          # 仓库根
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auction_scan  # noqa: E402
import export_market as em  # noqa: E402
from src.spiders import auction_spider  # noqa: E402

WF = ROOT / ".github" / "workflows"
PRODUCED = {"players.json", "stocks.json", "today.json", *em.MARKET_SLICES}


def _code_lines(text):
    """去掉注释行, 只留可执行内容(文档说明里允许出现 hot_rank 字样)。"""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


class TestBackendRetired(unittest.TestCase):
    def test_采集入口与存储类已删除(self):
        for name in ("collect_em_hot", "hot_rank_job"):
            self.assertFalse(hasattr(auction_scan, name), f"auction_scan.{name} 不应存在")
        self.assertFalse(hasattr(auction_spider, "HotRankStore"), "HotRankStore 不应存在")

    def test_命令行拒绝_hot_rank参数(self):
        err = StringIO()
        with patch.object(sys, "argv", ["auction_scan.py", "--hot-rank"]), redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                auction_scan.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("unrecognized arguments", err.getvalue())

    def test_源码无残留实现(self):
        for rel in ("scripts/auction_scan.py", "src/spiders/auction_spider.py",
                    "scripts/export_market.py", "scripts/intraday_monitor.py"):
            code = _code_lines((ROOT / "jiarenmens" / rel).read_text(encoding="utf-8"))
            for needle in ("emappdata", "getAllCurrentList", "HotRankStore",
                           "collect_em_hot", "hot_rank_job"):
                self.assertNotIn(needle, code, f"{rel} 残留 {needle}")

    def test_导出器只读auction库且只产两切片(self):
        self.assertEqual(em.ALLOWED_DBS, ("auction.db",))
        self.assertEqual(em.MARKET_SLICES, ("lianban_bid.json", "market_breadth.json"))
        self.assertIn("hot_rank.json", em.FORBIDDEN_SLICES)

    def test_目录里没有hot_rank_db也照常经main导出(self):
        import os
        import sqlite3
        import tempfile
        dbs, out = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        a = sqlite3.connect(str(dbs / "auction.db"))
        a.execute("CREATE TABLE bid_pool (date TEXT, code TEXT, name TEXT, turnover_ratio REAL)")
        a.execute("CREATE TABLE market_breadth (date TEXT, zt INTEGER)")
        a.commit()
        a.close()
        with patch.dict(os.environ, {"SB_DATA_V2": "1"}):
            rc = em.main(["--market-only", "--out", str(out), "--db-dir", str(dbs)])
        self.assertEqual(rc, 0)
        self.assertEqual({p.name for p in out.iterdir()}, set(em.MARKET_SLICES))


class TestWorkflowsRetired(unittest.TestCase):
    def test_workflow非注释行不再引用人气榜(self):
        for name in ("crawl.yml", "auction.yml"):
            code = _code_lines((WF / name).read_text(encoding="utf-8"))
            for needle in ("hot_rank", "--hot-rank", "hotrank", "人气榜"):
                self.assertNotIn(needle, code, f"{name} 的非注释行残留 {needle!r}")
        self.assertNotIn("name: 人气榜快照", (WF / "crawl.yml").read_text(encoding="utf-8"))

    def test_crawl提交白名单等于实际产出(self):
        """只恢复一半的典型形态: 导出器又开始产切片, 但提交白名单没跟上(或反之)。"""
        text = (WF / "crawl.yml").read_text(encoding="utf-8")
        m = re.search(r"^\s*for f in ([\w ]+); do\s*$", text, flags=re.M)
        self.assertIsNotNone(m, "找不到 crawl.yml 的提交白名单循环")
        names = m.group(1).split()
        self.assertEqual({n + ".json" for n in names}, PRODUCED,
                         "提交白名单与实际产出不一致")
        self.assertEqual(names, ["players", "stocks", "today",
                                 *[s[:-5] for s in em.MARKET_SLICES]])

    def test_auction提交步骤不再提交hot_rank_db(self):
        add_lines = [l for l in (WF / "auction.yml").read_text(encoding="utf-8").splitlines()
                     if "git add" in l and "auction.json" in l]
        self.assertEqual(len(add_lines), 1, "应恰好有一行提交 auction.json")
        self.assertNotIn("hot_rank", add_lines[0])
        self.assertIn("player_visibility.json", add_lines[0], "不得连带丢掉可见性状态文件")


class TestDataAndFrontendRetired(unittest.TestCase):
    def test_数据文件已移除(self):
        for rel in ("jiarenmens/data/hot_rank.db",
                    "stockboard-app/public/data/latest/hot_rank.json"):
            self.assertFalse((ROOT / rel).exists(), f"{rel} 不应存在")

    def test_前端不得请求已下线的切片(self):
        """下线依据是前端零引用; 反向锁住 —— 将来要展示人气榜, 须先恢复数据链路。"""
        src = ROOT / "stockboard-app" / "src"
        hits = [str(p.relative_to(ROOT)) for p in src.rglob("*")
                if p.suffix in (".vue", ".js") and "__tests__" not in p.parts
                and "hot_rank" in p.read_text(encoding="utf-8", errors="ignore")]
        self.assertEqual(hits, [], f"前端引用了已下线的 hot_rank: {hits}")


if __name__ == "__main__":
    unittest.main()
