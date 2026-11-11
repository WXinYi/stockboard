#!/usr/bin/env python3
"""采集掐班告警单测: 只报 cancelled、不重复报 failure、休市不打扰、dry-run 无副作用。"""
import datetime
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_cancel_alert as alert  # noqa: E402


def _ts(hhmm_utc="02:00", day=None):
    day = day or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    return f"{day}T{hhmm_utc}:00Z"


def _run(conclusion, rid, ts=None):
    return {"id": rid, "conclusion": conclusion,
            "run_started_at": ts or _ts()}


class TestCancelAlert(unittest.TestCase):
    def setUp(self):
        self.sent = []

        def fake_send(title, text):
            self.sent.append((title, text))
            return "ok"

        self.dt = patch.object(alert, "DingTalk", return_value=type(
            "FakeDT", (), {"send_markdown": staticmethod(fake_send)})())
        self.dt.start()
        self.env = patch.dict(os.environ, {"DINGTALK_URL": "https://example.com",
                                           "DINGTALK_SECRET": "s",
                                           "GITHUB_REPOSITORY": "WXinYi/stockboard"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dt.stop()

    def _main(self, runs, extra_argv=()):
        with patch.dict(os.environ, {"SB_RUNS_JSON": json.dumps(runs)}), \
             patch.object(sys, "argv", ["crawl_cancel_alert.py", *extra_argv]):
            return alert.main()

    def test_全部成功不发告警(self):
        rc = self._main([_run("success", 1), _run("success", 2)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_被取消的班发一条且列出时刻与链接(self):
        rc = self._main([_run("cancelled", 11, _ts("01:26")),
                         _run("cancelled", 12, _ts("01:55")),
                         _run("success", 13)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        title, text = self.sent[0]
        self.assertIn("采集班次被取消", title)
        self.assertIn("2 个被取消", text)
        self.assertIn("09:26", text)          # UTC 01:26 → 北京 09:26
        self.assertIn("09:55", text)
        self.assertIn("/actions/runs/11", text)

    def test_failure由既有告警覆盖不重复打扰(self):
        rc = self._main([_run("failure", 21)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_休市无班次不发告警(self):
        self.assertEqual(self._main([]), 0)
        self.assertEqual(self.sent, [])

    def test_跨UTC日的取消不计入今日(self):
        yest = (datetime.datetime.now(datetime.timezone.utc).date()
                - datetime.timedelta(days=1)).isoformat()
        rc = self._main([_run("cancelled", 31, _ts("01:26", yest)),
                         _run("success", 32)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_dry_run不发钉钉(self):
        rc = self._main([_run("cancelled", 41)], ("--dry-run",))
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_告警发送失败不判红(self):
        def boom(title, text):
            raise RuntimeError("钉钉挂了")
        self.dt.stop()
        self.dt = patch.object(alert, "DingTalk", return_value=type(
            "FakeDT", (), {"send_markdown": staticmethod(boom)})())
        self.dt.start()
        rc = self._main([_run("cancelled", 51)])
        self.assertEqual(rc, 0)

    def test_拉取历史失败返回非零(self):
        with patch.object(alert, "fetch_runs", side_effect=RuntimeError("API 挂了")), \
             patch.object(sys, "argv", ["crawl_cancel_alert.py"]):
            self.assertEqual(alert.main(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
