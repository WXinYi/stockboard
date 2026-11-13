#!/usr/bin/env python3
"""F6 前置：src/utils/atomic.py 原子写单测（.tmp → os.replace / 与 export_json 逐行等价 / 无截断残留）。

运行: python3 scripts/test_atomic.py
"""
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
from src.utils.atomic import (  # noqa: E402
    _atomic_json, _atomic_text, atomic_json, atomic_text,
)


class TestAtomicJson(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_写入内容正确_且_tmp_不残留(self):
        p = self.tmp / "a.json"
        _atomic_json(p, {"中文": "值", "n": 1})
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"中文": "值", "n": 1})
        self.assertFalse(Path(str(p) + ".tmp").exists(), "os.replace 后 .tmp 不应残留")

    def test_紧凑分隔_且中文不转义(self):
        p = self.tmp / "b.json"
        _atomic_json(p, {"a": 1, "b": "中文"})
        raw = p.read_text(encoding="utf-8")
        self.assertIn('"a":1,"b":"中文"', raw)
        self.assertNotIn("\\u", raw)

    def test_必经_os_replace_且源为同目录_tmp(self):
        p = self.tmp / "c.json"
        with patch("src.utils.atomic.os.replace", wraps=os.replace) as mocked:
            _atomic_json(p, {"x": 1})
        mocked.assert_called_once()
        self.assertEqual(Path(mocked.call_args[0][0]).name, "c.json.tmp")
        self.assertEqual(Path(mocked.call_args[0][1]).name, "c.json")

    def test_覆盖已有文件_不留截断(self):
        p = self.tmp / "d.json"
        _atomic_json(p, {"v": 1})
        _atomic_json(p, {"v": 2, "more": [1, 2]})
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")),
                         {"v": 2, "more": [1, 2]})

    def test_写入中途异常_目标旧值不被破坏(self):
        """Failure ≠ Fact：dump 抛错时 os.replace 未执行，旧文件保持完整。"""
        p = self.tmp / "e.json"
        _atomic_json(p, {"v": "old"})
        with patch("src.utils.atomic.json.dump", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                _atomic_json(p, {"v": "new"})
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"v": "old"})

    def test_atomic_text(self):
        p = self.tmp / "f.txt"
        _atomic_text(p, "hello 中文")
        self.assertEqual(p.read_text(encoding="utf-8"), "hello 中文")
        self.assertFalse(Path(str(p) + ".tmp").exists())

    def test_语义化别名指向同一函数对象(self):
        self.assertIs(atomic_json, _atomic_json)
        self.assertIs(atomic_text, _atomic_text)

    def test_与_export_json_实现逐行等价(self):
        """F6：新模块与既有 export_json._atomic_json 输出逐字节一致。

        S2 的 Commit-R1 删除 export_json.py 后本用例自动 skip，
        保证删除后 suite 仍 0 failures / 0 errors / 无 ImportError（§7.4）。
        """
        try:
            from export_json import _atomic_json as legacy  # noqa: E402
        except ImportError:
            self.skipTest("export_json.py 已删除（S2 后逐行等价核对不再适用）")
        p1, p2 = self.tmp / "g1.json", self.tmp / "g2.json"
        obj = {"中文": [1, 2, {"k": None}], "f": 1.25}
        _atomic_json(p1, obj)
        legacy(p2, obj)
        self.assertEqual(p1.read_bytes(), p2.read_bytes(), "逐字节等价")


if __name__ == "__main__":
    unittest.main()
