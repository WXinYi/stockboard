#!/usr/bin/env python3
"""前缀映射单一真源守卫（P0-1 回归）。

背景: 2026-09-05 事故——多处自写 ``sh/sz/bj`` 映射把北交所新段 920xxx 当沪市,
行情/K线全部拉空; 2026-11-17 复核发现 ``quotes.py`` 等 6 处仍残留该写法,
导致竞价班"昨日连板换手补算"对 920xxx 静默恒 0、连板股缺席出击推送。

本文件用两类断言防复发:
  ① 行为断言: market_prefix 真值表 + tencent_auction_amt 实际拼出的 symbol;
  ② 源码守卫: src/ 与 scripts/ 中不得再出现自写前缀映射的三种历史写法。
零网络(urllib 被打桩)。
"""
import io
import json
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(ROOT))

from src.notify.quotes import tencent_auction_amt  # noqa: E402
from src.utils.market import market_prefix  # noqa: E402

# 北交所新段 920xxx + 老段 43/83/87, 以及沪深各板块代表
EXPECTED = {
    "920001": "bj",   # 北交所新段(事故主角, 首位 9 但不是沪市)
    "920819": "bj",
    "430047": "bj",   # 北交所老段 43
    "830799": "bj",   # 北交所老段 83
    "871981": "bj",   # 北交所老段 87
    "600000": "sh",   # 沪主板
    "688001": "sh",   # 科创板
    "900901": "sh",   # 沪B
    "000001": "sz",   # 深主板
    "002594": "sz",
    "300750": "sz",   # 创业板
    "301001": "sz",
    "200011": "sz",   # 深B
}


class TestMarketPrefixTruthTable(unittest.TestCase):
    def test_prefix_table(self):
        for code, want in EXPECTED.items():
            with self.subTest(code=code):
                self.assertEqual(market_prefix(code), want)

    def test_bj_before_sh_for_92(self):
        """92→bj 必须先于 9→sh：这是事故根因，单独钉死。"""
        self.assertEqual(market_prefix("920001"), "bj")
        self.assertEqual(market_prefix("900901"), "sh")


class TestAuctionAmtUsesSingleSource(unittest.TestCase):
    """行为断言: 920xxx 请求的必须是 bj920xxx, 不能是 sz920xxx。"""

    def _run(self, code, rows):
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            pfx = EXPECTED.get(code, "sz")
            body = {"data": {pfx + code: {"data": {"data": rows}}}}
            return io.BytesIO(json.dumps(body).encode())

        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            return tencent_auction_amt(code), captured.get("url", "")

    def test_bj_920_symbol_and_amount(self):
        amt, url = self._run("920001", ["0930 10.00 100 1234500"])
        self.assertIn("code=bj920001", url)
        self.assertNotIn("sz920001", url)
        self.assertEqual(amt, 1234500.0)

    def test_sz_symbol_unchanged(self):
        amt, url = self._run("000001", ["0930 12.30 50 615000"])
        self.assertIn("code=sz000001", url)
        self.assertEqual(amt, 615000.0)

    def test_sh_symbol_unchanged(self):
        amt, url = self._run("600000", ["0930 9.80 20 196000"])
        self.assertIn("code=sh600000", url)
        self.assertEqual(amt, 196000.0)

    def test_non_0930_first_row_returns_none(self):
        """分时首行不是 0930(如停牌/历史日期) → 诚实返回 None, 不猜 0。"""
        amt, _ = self._run("920001", ["0931 10.00 100 1234500"])
        self.assertIsNone(amt)

    def test_upstream_error_returns_none(self):
        with mock.patch.object(urllib.request, "urlopen", side_effect=OSError("boom")):
            self.assertIsNone(tencent_auction_amt("920001"))


class TestNoHandWrittenPrefixMapping(unittest.TestCase):
    """源码守卫: 历史三种写法不得复活(允许出现在 test_*.py 与本文件里)。"""

    BAD_PATTERNS = ('startswith("6")', 'code[0] in "65"', 'code[0] in "48"')

    def test_no_copies_in_production_code(self):
        offenders = []
        for base in ("src", "scripts"):
            for path in sorted((ROOT / base).rglob("*.py")):
                if path.name.startswith("test_"):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for pat in self.BAD_PATTERNS:
                    if pat in text:
                        offenders.append(f"{path.relative_to(ROOT)}: {pat}")
        self.assertEqual(offenders, [], "自写行情前缀映射复发，请改用 src.utils.market.market_prefix")


if __name__ == "__main__":
    unittest.main()
