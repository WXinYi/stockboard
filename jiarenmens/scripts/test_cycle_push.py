#!/usr/bin/env python3
"""尾盘/午盘推送幂等状态单测: 同一交易日同一场次只推一次。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cycle_push import state_key, already_sent, mark_sent  # noqa: E402


class TestPushDedup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        self.tmp.unlink()   # 从"文件不存在"开始

    def tearDown(self):
        self.tmp.unlink(missing_ok=True)

    def test_文件不存在时当作未推过(self):
        self.assertFalse(already_sent(state_key("2026-11-11", "eod"), self.tmp))

    def test_推送后同键命中(self):
        k = state_key("2026-11-11", "eod")
        mark_sent(k, self.tmp)
        self.assertTrue(already_sent(k, self.tmp))

    def test_不同日期或场次不误跳过(self):
        mark_sent(state_key("2026-11-11", "eod"), self.tmp)
        self.assertFalse(already_sent(state_key("2026-11-12", "eod"), self.tmp))
        self.assertFalse(already_sent(state_key("2026-11-11", "midday"), self.tmp))

    def test_状态文件损坏时当作未推过_宁可多推不漏推(self):
        self.tmp.write_text("not-json", encoding="utf-8")
        self.assertFalse(already_sent(state_key("2026-11-11", "eod"), self.tmp))

    def test_状态含交易日与可读时间(self):
        import json
        mark_sent(state_key("2026-11-11", "eod"), self.tmp)
        d = json.loads(self.tmp.read_text(encoding="utf-8"))
        self.assertEqual(d["key"], "2026-11-11|eod")
        self.assertIn("sent_at", d)


class TestMainDedup(unittest.TestCase):
    """main() 分支: state 命中跳过钉钉 / 首次推送落状态 / force 补推 / dry-run 无副作用。"""

    def setUp(self):
        import cycle_push
        self.cp = cycle_push
        self.tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        self.tmp.unlink()
        key = state_key("2026-11-11", "eod")
        sent = []

        def fake_send(title, text):
            sent.append(title)
            return "ok"

        self.sent = sent
        self.base_patches = [
            patch.object(self.cp, "STATE_PATH", self.tmp),
            patch.object(self.cp, "KPLSpider"),
            patch.object(self.cp, "fetch_rt_today", return_value={"date": "2026-11-11", "rows": []}),
            patch.object(self.cp, "compute_cycle", return_value={"date": "2026-11-11"}),
            patch.object(self.cp, "build_markdown", return_value="测试文案"),
            patch("src.notify.dingtalk.DingTalk", return_value=type(
                "FakeDT", (), {"send_markdown": staticmethod(fake_send)})()),
        ]
        self.key = key

    def tearDown(self):
        self.tmp.unlink(missing_ok=True)

    def _run(self, argv):
        with patch.object(sys, "argv", ["cycle_push.py"] + argv):
            for p in self.base_patches:
                p.start()
            try:
                return self.cp.main()
            finally:
                for p in reversed(self.base_patches):
                    p.stop()

    def test_已推送过则跳过不再发钉钉(self):
        mark_sent(self.key, self.tmp)
        rc = self._run(["--session", "eod"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [], "重复推送会打扰用户")

    def test_首次推送成功并落状态(self):
        rc = self._run(["--session", "eod"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(already_sent(self.key, self.tmp))

    def test_force可手动补推(self):
        mark_sent(self.key, self.tmp)
        rc = self._run(["--session", "eod", "--force"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)

    def test_dry_run不推送也不写状态(self):
        rc = self._run(["--session", "eod", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.tmp.exists())

    def test_推送失败时不落状态_下个通道可重试(self):
        def boom(title, text):
            raise RuntimeError("钉钉挂了")
        self.base_patches[-1] = patch(
            "src.notify.dingtalk.DingTalk",
            return_value=type("FakeDT", (), {"send_markdown": staticmethod(boom)})())
        rc = self._run(["--session", "eod"])
        self.assertEqual(rc, 1)
        self.assertFalse(already_sent(self.key, self.tmp), "失败不能占住状态, 否则尾盘彻底不推")


if __name__ == "__main__":
    unittest.main()
