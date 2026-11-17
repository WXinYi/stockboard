#!/usr/bin/env python3
"""掐班告警单测: 定时班过滤、迟到保护、去重、休市/失败容错、dry-run 无副作用。"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_cancel_alert as alert  # noqa: E402

FIXED_UTC = datetime.datetime(2026, 11, 17, 7, 45, tzinfo=datetime.timezone.utc)  # 北京周二 15:45
TODAY_UTC = FIXED_UTC.date().isoformat()


def _ts(hhmm_utc="02:00", day=None):
    day = day or TODAY_UTC
    return f"{day}T{hhmm_utc}:00Z"


def _run(conclusion, rid, ts=None, event="workflow_dispatch", title="[cron] auto"):
    return {"id": rid, "conclusion": conclusion,
            "run_started_at": ts or _ts(), "event": event, "display_title": title}


class TestCancelAlert(unittest.TestCase):
    def setUp(self):
        self.sent = []

        def fake_send(title, text):
            self.sent.append((title, text))
            return "ok"

        self.dt = patch.object(alert, "DingTalk", return_value=type(
            "FakeDT", (), {"send_markdown": staticmethod(fake_send)})())
        self.dt.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "state.json"
        self.env = patch.dict(os.environ, {
            "DINGTALK_URL": "https://example.com",
            "DINGTALK_SECRET": "s",
            "GITHUB_REPOSITORY": "WXinYi/stockboard",
            "SB_ALERT_STATE_FILE": str(self.state_file),
        })
        self.env.start()
        self.now = patch.object(alert, "now_utc", return_value=FIXED_UTC)
        self.now.start()

    def tearDown(self):
        self.now.stop()
        self.env.stop()
        self.dt.stop()
        self.tmp.cleanup()

    def _main(self, runs, extra_argv=()):
        with patch.dict(os.environ, {"SB_RUNS_JSON": json.dumps(runs)}), \
             patch.object(sys, "argv", ["crawl_cancel_alert.py", *extra_argv]):
            return alert.main()

    def _state_ids(self):
        if not self.state_file.exists():
            return set()
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        return set(data.get("notifiedRunIds") or [])

    def test_全部成功不发告警(self):
        rc = self._main([_run("success", 1), _run("success", 2)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_被取消的定时班发一条且列出时刻与链接(self):
        rc = self._main([_run("cancelled", 11, _ts("01:26")),
                         _run("cancelled", 12, _ts("01:55")),
                         _run("success", 13)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        title, text = self.sent[0]
        self.assertIn("定时采集班被取消", title)
        self.assertIn("2 个被取消", text)
        self.assertIn("09:26", text)          # UTC 01:26 → 北京 09:26
        self.assertIn("09:55", text)
        self.assertIn("/actions/runs/11", text)
        self.assertEqual(self._state_ids(), {"11", "12"})

    def test_failure由既有告警覆盖不重复打扰(self):
        rc = self._main([_run("failure", 21)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_休市无班次不发告警(self):
        self.assertEqual(self._main([]), 0)
        self.assertEqual(self.sent, [])

    def test_跨UTC日的取消不计入今日(self):
        yest = (FIXED_UTC.date() - datetime.timedelta(days=1)).isoformat()
        rc = self._main([_run("cancelled", 31, _ts("01:26", yest)),
                         _run("success", 32)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_凌晨迟到跳过不报昨日旧账(self):
        late = datetime.datetime(2026, 11, 16, 17, 28, tzinfo=datetime.timezone.utc)
        with patch.object(alert, "now_utc", return_value=late):
            rc = self._main([_run("cancelled", 41)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.state_file.exists())

    def test_force_window可显式补发(self):
        late = datetime.datetime(2026, 11, 16, 17, 28, tzinfo=datetime.timezone.utc)
        late_ts = _ts("17:28", late.date().isoformat())
        with patch.object(alert, "now_utc", return_value=late):
            rc = self._main([_run("cancelled", 42, late_ts)], ("--force-window",))
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)

    def test_非工作日跳过(self):
        weekend = datetime.datetime(2026, 11, 21, 7, 45, tzinfo=datetime.timezone.utc)
        with patch.object(alert, "now_utc", return_value=weekend):
            rc = self._main([_run("cancelled", 43)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_push和普通手动dispatch不算定时班(self):
        runs = [
            _run("cancelled", 51, event="push",
                 title="数据采集 + push(main)"),
            _run("cancelled", 52, event="workflow_dispatch",
                 title="数据采集 + 部署"),
            _run("success", 53, event="workflow_dispatch", title="[cron] auto"),
        ]
        rc = self._main(runs)
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])

    def test_repository_dispatch_eod算定时班(self):
        rc = self._main([_run("cancelled", 61, event="repository_dispatch",
                              title="crawl-eod")])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)

    def test_同一取消run重复检查不重复推送(self):
        runs = [_run("cancelled", 71)]
        self.assertEqual(self._main(runs), 0)
        self.assertEqual(self._main(runs), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self._state_ids(), {"71"})

    def test_新增取消run会补发(self):
        first = [_run("cancelled", 81)]
        second = first + [_run("cancelled", 82)]
        self.assertEqual(self._main(first), 0)
        self.assertEqual(self._main(second), 0)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self._state_ids(), {"81", "82"})

    def test_dry_run不发钉钉且不写状态(self):
        rc = self._main([_run("cancelled", 91)], ("--dry-run",))
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.state_file.exists())

    def test_告警发送失败不判红且不写状态(self):
        def boom(title, text):
            raise RuntimeError("钉钉挂了")
        self.dt.stop()
        self.dt = patch.object(alert, "DingTalk", return_value=type(
            "FakeDT", (), {"send_markdown": staticmethod(boom)})())
        self.dt.start()
        rc = self._main([_run("cancelled", 101)])
        self.assertEqual(rc, 0)
        self.assertFalse(self.state_file.exists())

    def test_拉取历史失败返回非零(self):
        with patch.object(alert, "fetch_runs", side_effect=RuntimeError("API 挂了")), \
             patch.object(sys, "argv", ["crawl_cancel_alert.py"]):
            self.assertEqual(alert.main(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
