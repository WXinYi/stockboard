#!/usr/bin/env python3
"""Story 1.4 / 5A P1 硬门槛：契约哨兵成功+失败两分支、ok=false→0 请求、emContract 字段。

运行: python3 scripts/test_em_contract_probe.py
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
import em_contract_probe as ep  # noqa: E402

DATE = "2026-11-12"
TS = "2026-11-12T09:26:00+08:00"


class TestProbeBranches(unittest.TestCase):
    """4 项契约（HTTP200 / CORS / 无 token code 成功 / 数据非空）成功与失败分支。"""

    def test_成功分支_4项全真(self):
        r = ep.evaluate_probe(ep.DEMO_CONTRACT, TS)
        self.assertTrue(r["ok"])
        self.assertTrue(all(r["checks"].values()))
        self.assertEqual(tuple(r["checks"]), ep.CHECK_KEYS)
        self.assertEqual(r["checkedAt"], TS)

    def test_失败分支_非200(self):
        r = ep.evaluate_probe(dict(ep.DEMO_CONTRACT, status=503), TS)
        self.assertFalse(r["ok"])
        self.assertFalse(r["checks"]["http200"])

    def test_失败分支_无CORS回显(self):
        r = ep.evaluate_probe(dict(ep.DEMO_CONTRACT, headers={"Content-Type": "application/json"}), TS)
        self.assertFalse(r["ok"])
        self.assertFalse(r["checks"]["cors"])

    def test_失败分支_无token下code非0(self):
        r = ep.evaluate_probe(dict(ep.DEMO_CONTRACT, body={"code": 9035, "data": {}}), TS)
        self.assertFalse(r["ok"])
        self.assertFalse(r["checks"]["no_token_success"])

    def test_失败分支_数据为空(self):
        r = ep.evaluate_probe(dict(ep.DEMO_CONTRACT, body={"code": 0, "data": {}}), TS)
        self.assertFalse(r["ok"])
        self.assertFalse(r["checks"]["data_non_empty"])

    def test_失败分支_携带登录token即判失败(self):
        r = ep.evaluate_probe(dict(ep.DEMO_CONTRACT, token_present=True), TS)
        self.assertFalse(r["ok"])
        self.assertFalse(r["checks"]["no_token_success"])

    def test_浏览器UA_非EMProjJs_且无任何登录token(self):
        self.assertNotIn("EMProjJs", ep.BROWSER_UA)
        self.assertIn("Mozilla", ep.BROWSER_UA)
        payload = json.dumps(ep.DEMO_CONTRACT)
        for forbidden in ("ctToken", "utToken", "EM_CT_TOKEN", "EM_UT_TOKEN"):
            self.assertNotIn(forbidden, payload)
        self.assertTrue(ep.FAKE_CLIENT_VERSION, "假 clientVersion 占位必须存在")


class TestSkipContract(unittest.TestCase):
    """T1.4.2 契约级定义与占位验证：ok=false → 下游 0 请求 / 全站 🟡（前端属 S4）。"""

    def test_ok_false_跳过直连_0次EM请求(self):
        contract = ep.evaluate_probe(dict(ep.DEMO_CONTRACT, status=503), TS)
        self.assertFalse(contract["ok"])
        calls = []
        if not ep.should_skip_direct_connect(contract):
            calls.append("rtV2")          # S4 useEmPlayer.js 的等价占位
        self.assertEqual(calls, [], "ok=false → 0 次请求, 不发版全站 🟡")

    def test_ok_true_放行直连(self):
        contract = ep.evaluate_probe(ep.DEMO_CONTRACT, TS)
        self.assertFalse(ep.should_skip_direct_connect(contract))

    def test_缺失emContract_默认跳过_fail_safe(self):
        self.assertTrue(ep.should_skip_direct_connect({}))
        self.assertTrue(ep.should_skip_direct_connect(None))
        self.assertTrue(ep.should_skip_direct_connect({"ok": False}))


class TestWriteEmContract(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def test_写_today_json_emContract_仅ok与checkedAt(self):
        c = ep.evaluate_probe(ep.DEMO_CONTRACT, TS)
        doc = ep.write_em_contract(self.out, c, DATE, TS)
        self.assertEqual(set(doc["emContract"]), {"ok", "checkedAt"})
        disk = json.loads((self.out / "today.json").read_text(encoding="utf-8"))
        self.assertTrue(disk["emContract"]["ok"])
        self.assertEqual(disk["emContract"]["checkedAt"], TS)

    def test_失败分支落盘ok_false(self):
        c = ep.evaluate_probe(dict(ep.DEMO_CONTRACT, status=503), TS)
        ep.write_em_contract(self.out, c, DATE, TS)
        disk = json.loads((self.out / "today.json").read_text(encoding="utf-8"))
        self.assertFalse(disk["emContract"]["ok"])

    def test_carry_forward_保留既有字段_sticky(self):
        prior = {"date": DATE, "updatedAt": "11:30", "tradedPlayerIds": ["900293400"],
                 "emContract": {"ok": False, "checkedAt": "old"}}
        (self.out / "today.json").write_text(json.dumps(prior), encoding="utf-8")
        c = ep.evaluate_probe(dict(ep.DEMO_CONTRACT, status=503), TS)
        doc = ep.write_em_contract(self.out, c, DATE, "15:15")
        self.assertEqual(doc["tradedPlayerIds"], ["900293400"])
        self.assertEqual(doc["updatedAt"], "11:30")
        self.assertFalse(doc["emContract"]["ok"], "当日 sticky 不重置为 true")

    def test_拒绝线上路径(self):
        with self.assertRaises(SystemExit):
            ep.resolve_out("/x/stockboard-app/public/data/latest")

    def test_SB_DATA_V2_0_noop_不写任何文件(self):
        with patch.dict(os.environ, {"SB_DATA_V2": "0"}):
            rc = ep.main(["--demo", "--out", str(self.out)])
        self.assertEqual(rc, 0)
        self.assertEqual(list(self.out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
