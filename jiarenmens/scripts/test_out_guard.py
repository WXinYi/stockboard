#!/usr/bin/env python3
"""Review M-03：``resolve_out`` 大小写变体绕过回归（3 脚本全覆盖，产物仅 /tmp）。

覆盖：全大写 / 混合大小写变体、``..`` 穿透、``//`` 双斜杠、symlink、
相对路径 + 尾斜杠、生产目录子路径、以及 inode samefile 兜底。断言所有生产路径
在 S1 全部被拒绝，且线上 production 目录零新增文件。

运行: python3 scripts/test_out_guard.py
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_snapshot as ps    # noqa: E402
import export_market as em      # noqa: E402
import em_contract_probe as ep  # noqa: E402

MODULES = (ps, em, ep)
DATE = "2026-11-12"
PROD_LATEST = ROOT.parent / "stockboard-app" / "public" / "data" / "latest"
ONLINE_STOCKS = PROD_LATEST / "stocks.json"


def production_path_variants():
    """生成所有指向真实生产 latest 目录的绕过形态。"""
    base = str(PROD_LATEST)
    return {
        "绝对小写（原守卫已拒绝）": base,
        "全大写 PUBLIC/DATA/LATEST": base.replace("public/data/latest", "PUBLIC/DATA/LATEST"),
        "混合大小写": base.replace("public/data/latest", "Public/Data/Latest"),
        "仅 LATEST 大写": base.replace("latest", "LATEST"),
        "仅 public 大写": base.replace("/public/", "/PUBLIC/"),
        ".. 穿透": base + "/../latest",
        ".. 穿透 2": base.replace("data/latest", "data/../data/latest"),
        "// 双斜杠": base.replace("/public/", "//public//"),
        "尾斜杠": base + "/",
        "尾斜杠 + 大写": base.replace("public/data/latest", "PUBLIC/DATA/LATEST") + "/",
        "生产子路径": base + "/nested/subdir",
    }


class TestResolveOutRejectsAllProductionPaths(unittest.TestCase):
    """M-03：大小写 / symlink / 点点 / 双斜杠 变体全部被拒绝，且拒绝 ALL 生产路径。"""

    def setUp(self):
        self.online_before = ONLINE_STOCKS.read_bytes()
        self.dir_before = {p.name for p in PROD_LATEST.iterdir()}

    def tearDown(self):
        self.assertEqual(ONLINE_STOCKS.read_bytes(), self.online_before,
                         "线上 stocks.json 不得被改动一个字节")
        self.assertEqual({p.name for p in PROD_LATEST.iterdir()}, self.dir_before,
                         "线上 latest/ 目录不得新增任何文件")

    def test_三脚本_全部大小写与路径变体被拒绝(self):
        variants = production_path_variants()
        for mod in MODULES:
            for label, path in variants.items():
                with self.subTest(mod=mod.__name__, case=label):
                    with self.assertRaises(SystemExit) as ctx:
                        mod.resolve_out(path)
                    self.assertIn("拒绝写入线上路径", str(ctx.exception))

    def test_三脚本_生产public目录本身与其子目录被拒绝(self):
        prod_public = str(PROD_LATEST.parent.parent)  # .../public
        cases = [prod_public, prod_public + "/data", prod_public + "/other.json"]
        for mod in MODULES:
            for path in cases:
                with self.subTest(mod=mod.__name__, case=path):
                    with self.assertRaises(SystemExit):
                        mod.resolve_out(path)

    def test_三脚本_symlink指向生产目录被拒绝(self):
        tmp = Path(tempfile.mkdtemp())
        link = tmp / "prod_alias"
        link2 = tmp / "prod_alias_upper"
        try:
            os.symlink(str(PROD_LATEST), str(link))
            os.symlink(str(PROD_LATEST), str(link2))
        except OSError:
            self.skipTest("当前环境不支持 symlink")
        for mod in MODULES:
            for lp in (str(link), str(link2), str(link) + "/", str(link) + "/../" + link.name):
                with self.subTest(mod=mod.__name__, case=lp):
                    with self.assertRaises(SystemExit):
                        mod.resolve_out(lp)

    def test_三脚本_相对路径形态被拒绝(self):
        # 从仓库根 cwd 出发的相对路径（含 .. / 大小写 / 尾斜杠）
        repo_root = str(ROOT.parent)
        rel = os.path.relpath(str(PROD_LATEST), repo_root)  # stockboard-app/public/data/latest
        cases = [
            rel,
            rel.replace("public/data/latest", "PUBLIC/DATA/LATEST"),
            rel + "/",
            "./" + rel,
            "./" + rel.replace("public/data/latest", "PUBLIC/DATA/LATEST") + "/",
        ]
        cwd = os.getcwd()
        try:
            os.chdir(repo_root)
            for mod in MODULES:
                for path in cases:
                    with self.subTest(mod=mod.__name__, case=path):
                        with self.assertRaises(SystemExit):
                            mod.resolve_out(path)
        finally:
            os.chdir(cwd)

    def test_大小写变体经canonical与samefile双重命中(self):
        upper = str(PROD_LATEST).replace("public/data/latest", "PUBLIC/DATA/LATEST")
        self.assertEqual(ps._canonical(upper), ps._canonical(PROD_LATEST),
                         "realpath+normcase+casefold 必须把大写变体归一到生产路径")
        self.assertTrue(ps._is_production_out(upper))
        self.assertTrue(em._is_production_out(upper))
        self.assertTrue(ep._is_production_out(upper))

    def test_tmp临时目录被放行_不误杀(self):
        for mod in MODULES:
            out = Path(tempfile.mkdtemp()) / "data" / "latest"  # 注意：不含 public/
            resolved = mod.resolve_out(str(out))
            self.assertTrue(resolved.is_dir(), f"{mod.__name__} 应放行 /tmp 临时目录")

    def test_三脚本main_大写out被拒绝_线上零覆盖(self):
        upper = str(PROD_LATEST).replace("public/data/latest", "PUBLIC/DATA/LATEST")
        argv_cases = [
            (ps, ["--rank", "--demo"]),
            (ps, ["--midday", "--demo"]),
            (ps, ["--close", "--demo"]),
            (em, ["--market-only", "--db-dir", "__NO_SUCH_DB__"]),
            (ep, ["--demo"]),
        ]
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        for mod, base_argv in argv_cases:
            with self.subTest(mod=mod.__name__, argv=base_argv[0]):
                # 确保 kill switch 未激活，否则会被 no-op 抢先返回
                with patch.dict(os.environ, {"SB_DATA_V2": "1"}):
                    with self.assertRaises(SystemExit):
                        with redirect_stdout(io.StringIO()):
                            mod.main(base_argv + ["--out", upper])

    def test_线上stocks_json为P3新schema(self):
        # S4 放行后 AQ3=B 生效：线上 stocks.json 已由 P3 原子切换为新契约。
        # 零兼容代码：旧 stockStats / qh/qtp/qap 不得残留。
        doc = json.loads(self.online_before.decode("utf-8"))
        self.assertEqual(set(doc.keys()), {"date", "snapshot", "updatedAt", "stats"})
        self.assertIn(doc["snapshot"], {"1130", "close"})
        self.assertNotIn("stockStats", doc)
        for row in doc["stats"][:5]:
            self.assertEqual(set(row.keys()), {"code", "name", "h", "tp", "ap"})


if __name__ == "__main__":
    unittest.main()
