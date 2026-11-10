#!/usr/bin/env python3
"""llm_review 健壮解析器单测(纯断言, 无网络): 用五天真实输出做 fixture。

原实现是脚本式 check + 模块顶层 sys.exit, 被 unittest discover 导入时抛
SystemExit:0 使整个测试集判红; 现为标准 TestCase, 断言口径不变。
运行: cd jiarenmens && venv/bin/python -m unittest scripts.test_llm_review
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.analysis.llm_review import extract_json, _repair_quotes  # noqa: E402

FIX_DIR = Path("/tmp/dssim")
DATES = ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11")


class TestExtractJson(unittest.TestCase):
    """合成边界 + 真实 fixture(存在才校验), 无网络。"""

    def test_合成边界(self):
        cases = [
            ("markdown 围栏", '```json\n{"a":1}\n```', {"a": 1}),
            ("尾串容忍", '{"a":1}\n希望以上分析对你有帮助', {"a": 1}),
            ("前导杂文", '好的，以下是JSON：\n{"a":{"b":2}}', {"a": {"b": 2}}),
            ("嵌套对象", '{"x":{"y":{"z":1}},"w":null}', {"x": {"y": {"z": 1}}, "w": None}),
            ("字符串内花括号", '{"why":"缩量<3%与{大}括号","a":1}',
             {"why": "缩量<3%与{大}括号", "a": 1}),
        ]
        for name, raw, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(extract_json(raw), expected)
        self.assertIsNotNone(extract_json('{"rank":4","reason":"x"}'))
        self.assertIsNone(extract_json(""))
        self.assertIsNone(extract_json(None))
        self.assertIsNone(extract_json('{"environment":{"regime":"空仓"'))
        self.assertEqual(json.loads(_repair_quotes('{"a":1,}')), {"a": 1})

    def test_真实fixture_存在才校验(self):
        # CI 无 /tmp/dssim 时跳过, 不影响门禁; 本地有 fixture 时逐日校验解析结果。
        for prefix, field in (("out_", "picks"), ("outv2_", "environment")):
            for d in DATES:
                f = FIX_DIR / f"{prefix}{d}.json"
                if not f.exists():
                    continue
                raw = json.loads(f.read_text())["raw"]
                with self.subTest(fixture=f.name):
                    p = extract_json(raw)
                    self.assertIsInstance(p, dict)
                    if field == "picks":
                        self.assertIsInstance(p.get("picks"), list)
                    else:
                        self.assertIsInstance(p.get("environment"), dict)


if __name__ == "__main__":
    unittest.main()
