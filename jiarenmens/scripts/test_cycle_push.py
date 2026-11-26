#!/usr/bin/env python3
"""尾盘/午盘推送幂等状态单测: 同一交易日同一场次只推一次。"""
import contextlib
import io
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

    def test_多场次互不覆盖_midday与eod交错补跑(self):
        """单 key 覆盖会导致: 先推 midday → 推 eod → midday 再跑被判"未推"重复推送。"""
        mark_sent(state_key("2026-11-11", "midday"), self.tmp)
        mark_sent(state_key("2026-11-11", "eod"), self.tmp)
        self.assertTrue(already_sent(state_key("2026-11-11", "midday"), self.tmp))
        self.assertTrue(already_sent(state_key("2026-11-11", "eod"), self.tmp))
        self.assertFalse(already_sent(state_key("2026-11-12", "midday"), self.tmp))

    def test_旧格式单key状态仍可读_升级不丢历史(self):
        import json
        self.tmp.write_text(json.dumps({"key": "2026-11-11|eod", "sent_at": "2026-11-11T15:45:00+08:00"}),
                            encoding="utf-8")
        self.assertTrue(already_sent(state_key("2026-11-11", "eod"), self.tmp))
        self.assertFalse(already_sent(state_key("2026-11-11", "midday"), self.tmp))

    def test_原子写不留tmp残留(self):
        mark_sent(state_key("2026-11-11", "eod"), self.tmp)
        self.assertFalse(Path(str(self.tmp) + ".tmp").exists())
        self.assertTrue(self.tmp.exists())

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

    def test_非钉钉异常也写失败标志(self):
        self.base_patches[2] = patch.object(
            self.cp, "fetch_rt_today", side_effect=RuntimeError("KPL挂了"))
        flag = self.tmp.with_suffix(".failed")
        rc = self._run(["--session", "eod", "--fail-flag", str(flag)])
        self.assertEqual(rc, 1)
        self.assertIn("KPL挂了", flag.read_text(encoding="utf-8"))

    def test_午盘共识段打印到日志可事后核对(self):
        self.base_patches.append(patch.object(
            self.cp, "build_consensus_markdown", return_value="CONSENSUS-MARK"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self._run(["--session", "midday", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("CONSENSUS-MARK", buf.getvalue())


class TestFmtTime(unittest.TestCase):
    """fmt_time 时区回归(2026-11-24 修复): runner 是 UTC, 裸 fromtimestamp 会把封板
    时间显示成北京时间-8h(09:25 → 01:25), 且"午后上板 >= 13:00"判定永远为假。
    修复后显式 BJ_TZ, 本组用例在任意宿主机时区下答案一致。"""

    def setUp(self):
        import cycle_push
        self.fmt_time = cycle_push.fmt_time

    def test_真实封板时间_北京上午(self):
        # 真实库值: 2026-11-23 首个涨停封板 = 北京 09:25:00(TZ=UTC 下修复前显示 01:25)
        self.assertEqual(self.fmt_time(1795397100), "09:25")

    def test_午后封板时间_能过13点阈值(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ts = int(datetime(2026, 11, 23, 14, 30,
                          tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        self.assertEqual(self.fmt_time(ts), "14:30")
        self.assertGreaterEqual(self.fmt_time(ts), "13:00")

    def test_跨日时刻仍按北京时区(self):
        # 北京 00:30 = UTC 前一日 16:30 —— 宿主机无论什么时区都应显示 00:30
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ts = int(datetime(2026, 11, 23, 0, 30,
                          tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        self.assertEqual(self.fmt_time(ts), "00:30")

    def test_非数值返回问号(self):
        for bad in (None, "abc", "", object()):
            with self.subTest(bad=bad):
                self.assertEqual(self.fmt_time(bad), "?")


if __name__ == "__main__":
    unittest.main()
