#!/usr/bin/env python3
"""Review M-02 / F-01 / m-06：``--live`` / ``EmClient`` 与既有 EM 协议接线单测。

全程 mock ``_call_api_new`` / ``PlayerListSpider`` / ``requests``，**零真实网络**。
断言 live 路径构造的 method / args / tokens 正确，且绝不自建 requests.get/post。
同时补齐 ``make_client`` / ``load_probe_result`` / ``send_probe`` 零覆盖缺口。

运行: python3 scripts/test_live_client.py
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_snapshot as ps          # noqa: E402
import em_contract_probe as ep        # noqa: E402

DATE = "2026-11-12"
TOKENS = {"ctToken": "CT_SENTINEL", "utToken": "UT_SENTINEL", "deviceId": "DEV_SENTINEL"}
ZH = "900293400"
UID = "6838395742604432"


def make_em_client(rank_rows=None):
    """构造一个完全离线的 EmClient：注入 mock spider + 固定 token。"""
    spider = MagicMock()
    spider.fetch_rank_players.return_value = rank_rows or []
    spider.fetch_player_list.return_value = rank_rows or []
    return ps.EmClient(spider=spider, tokens=dict(TOKENS)), spider


class TestEmClientUsesRealProtocol(unittest.TestCase):
    """M-02：rtV2 走 _call_api_new（含 token），rtV1 走 PlayerListSpider。"""

    @patch.object(ps._api_detail, "_call_api_new")
    def test_yield_构造正确method_args_tokens(self, m_call):
        m_call.return_value = {"totalYieldRate": 2.4648, "dayYieldRate": -0.021,
                               "netWorth": 8.596}
        c, _ = make_em_client()
        c.player_yield(ZH, UID)
        self.assertEqual(m_call.call_count, 1)
        method, args, tokens = m_call.call_args[0]
        self.assertEqual(method, ps.V2_YIELD_HANDLER)
        # M-02 payload 缺口：ctToken/utToken 必须在 args 内
        self.assertEqual(args["ctToken"], TOKENS["ctToken"])
        self.assertEqual(args["utToken"], TOKENS["utToken"])
        self.assertEqual(args["userId"], UID)
        self.assertEqual(args["combinationId"], int(ZH))
        self.assertEqual(tokens, TOKENS, "_load_tokens 结果作为 tokens 传入")

    @patch.object(ps._api_detail, "_call_api_new")
    def test_position_构造正确method_args(self, m_call):
        m_call.return_value = []
        c, _ = make_em_client()
        c.player_position(ZH, UID)
        method, args, tokens = m_call.call_args[0]
        self.assertEqual(method, ps.V2_POSITION_HANDLER)
        self.assertEqual(args["ctToken"], TOKENS["ctToken"])
        self.assertEqual(args["combinationId"], int(ZH))

    @patch.object(ps._api_detail, "_call_api_new")
    def test_trades_单页当日过滤_参数正确(self, m_call):
        m_call.return_value = {"pages": [
            {"bizDate": "20261112", "stkCode": "002086"},   # 当日 → 保留
            {"bizDate": "20261110", "stkCode": "600715"},   # 历史 → 过滤
        ]}
        c, _ = make_em_client()
        rows = c.player_trades(ZH, UID, DATE, DATE, 1)
        method, args, tokens = m_call.call_args[0]
        self.assertEqual(method, ps.V2_TRADES_HANDLER)
        self.assertEqual(args["ctToken"], TOKENS["ctToken"])
        self.assertEqual(args["beginDate"], DATE)
        self.assertEqual(args["endDate"], DATE)
        self.assertEqual(args["pageNum"], 1)
        self.assertEqual(args["pageSize"], ps.RANK_PAGE_SIZE)  # 固定 1 页，不取历史 40 条
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_date"], DATE)

    @patch.object(ps._api_detail, "_call_api_new")
    def test_rank_复用spider_并反适配原始行键(self, m_call):
        normalized = [{"zh_id": ZH, "user_id": UID, "name": "月儿兰",
                       "followers": 10, "daily_return": 1.5}]
        c, spider = make_em_client(normalized)
        rows = c.rank_page("10004", 0, 20)
        spider.fetch_rank_players.assert_called_once_with("10004", max_count=ps.DEFAULT_RANK_LIMIT)
        self.assertEqual(rows[0]["zjzh"], ZH)
        self.assertEqual(rows[0]["userid"], UID, "AQ5=A 硬连线 userid")
        self.assertEqual(rows[0]["zhuheName"], "月儿兰")
        # 分页切片命中缓存，spider 不重复发起请求
        self.assertEqual(c.rank_page("10004", 20, 20), [])
        self.assertEqual(spider.fetch_rank_players.call_count, 1)
        m_call.assert_not_called()  # rtV1 绝不走 rtV2 helper

    @patch.object(ps._api_detail, "_call_api_new")
    def test_fetch_all_players_复用fetch_player_list(self, m_call):
        c, spider = make_em_client([{"zh_id": ZH, "user_id": UID, "name": "月儿兰"}])
        c.fetch_all_players()
        spider.fetch_player_list.assert_called_once_with(max_per_rank=ps.DEFAULT_RANK_LIMIT)
        m_call.assert_not_called()

    @patch.object(ps._api_detail, "_call_api_new")
    def test_全链路不自建requests_get_post(self, m_call):
        """M-02 核心：EmClient 绝不直接调用 requests.get/post（facade 已去除）。"""
        m_call.return_value = {}
        c, _ = make_em_client()
        with patch("requests.get", side_effect=AssertionError("no network")) as g, \
                patch("requests.post", side_effect=AssertionError("no network")) as p:
            c.player_yield(ZH, UID)
            c.player_position(ZH, UID)
            c.player_trades(ZH, UID, DATE, DATE, 1)
        g.assert_not_called()
        p.assert_not_called()
        self.assertEqual(m_call.call_count, 3)

    def test_源码引用既有helper_无硬编码token(self):
        import inspect
        src = inspect.getsource(ps.EmClient)
        self.assertIn("_call_api_new", src)
        self.assertIn("PlayerListSpider", src)
        self.assertNotIn("requests.get", src)
        self.assertNotIn("requests.post", src)
        # 仅从 _load_tokens() 取 token，绝不硬编码具体值
        self.assertIn("_load_tokens", src)

    @patch.object(ps._api_detail, "_load_tokens", return_value=dict(TOKENS))
    @patch("player_snapshot.PlayerListSpider")
    def test_默认构造使用_load_tokens与真实spider(self, m_spider, m_load):
        c = ps.EmClient()
        m_load.assert_called_once_with()
        m_spider.assert_called_once_with()
        self.assertEqual(c._tokens, TOKENS)

    @patch.object(ps._api_detail, "_call_api_new")
    def test_token缺失_诚实失败且不发请求(self, m_call):
        c = ps.EmClient(spider=MagicMock(), tokens={})
        with self.assertRaises(RuntimeError) as ctx:
            c.player_yield(ZH, UID)
        self.assertIn("token", str(ctx.exception).lower())
        m_call.assert_not_called()


class TestLiveClientFactory(unittest.TestCase):
    """m-06：make_client 的 --live 分支 + 离线分支零覆盖补齐（零网络）。"""

    @patch.object(ps._api_detail, "_load_tokens", return_value=dict(TOKENS))
    @patch("player_snapshot.PlayerListSpider")
    def test_live返回EmClient(self, m_spider, m_load):
        args = ps.build_parser().parse_args(["--rank", "--live"])
        c = ps.make_client(args, DATE)
        self.assertIsInstance(c, ps.EmClient)

    def test_demo返回FixtureClient_不构造spider(self):
        with patch("player_snapshot.PlayerListSpider") as m_spider:
            args = ps.build_parser().parse_args(["--rank", "--demo"])
            c = ps.make_client(args, DATE)
            self.assertIsInstance(c, ps.FixtureClient)
            m_spider.assert_not_called()

    def test_main_live_零网络_仍产出players_json(self):
        """--rank --live 在 mock 全部 EM helper 下产出合法 players.json（无真实网络）。"""
        out = Path(tempfile.mkdtemp())
        normalized = [{"zh_id": z, "user_id": f"UID{z}", "name": f"P{z}"}
                      for z, _ in ps.WATCHED_PLAYERS]
        with patch.object(ps._api_detail, "_load_tokens", return_value=dict(TOKENS)), \
                patch("player_snapshot.PlayerListSpider") as m_spider, \
                patch("requests.get", side_effect=AssertionError("no network")), \
                patch("requests.post", side_effect=AssertionError("no network")):
            m_spider.return_value.fetch_rank_players.return_value = normalized
            rc = ps.main(["--rank", "--live", "--out", str(out), "--date", DATE])
        self.assertEqual(rc, 0)
        doc = json.loads((out / "players.json").read_text(encoding="utf-8"))
        self.assertTrue(all(p["userId"] for p in doc["players"]), "AQ5=A userId 非空")
        self.assertEqual(doc["rankTime"], "09:26")


class TestSendProbeLivePath(unittest.TestCase):
    """F-01 / m-06：em_contract_probe.send_probe() 真实 HTTP 实现的 mock 覆盖。

    注意：哨兵按架构 §7.3 必须是「浏览器 UA + 无 token」探测，因此**不应**接入会
    注入登录态 token 的 _call_api_new —— 保持独立 requests.post 是正确设计，此处
    仅补 mock 单测验证 payload 结构，不改动其语义。
    """

    @patch("requests.post")
    def test_send_probe_构造浏览器UA无token_8秒超时(self, m_post):
        m_post.return_value.status_code = 200
        m_post.return_value.headers = {"Access-Control-Allow-Origin": "*"}
        m_post.return_value.json.return_value = {"code": 0, "data": {"x": 1}}
        r = ep.send_probe()
        self.assertEqual(m_post.call_count, 1)
        kwargs = m_post.call_args[1]
        self.assertEqual(kwargs["timeout"], 8)
        self.assertIn("Origin", kwargs["headers"])
        self.assertIn("Mozilla", kwargs["headers"]["User-Agent"])
        payload = kwargs["json"]
        self.assertEqual(payload["method"], "CombinationInfoHandler")
        self.assertNotIn("ctToken", json.dumps(payload))
        self.assertNotIn("utToken", json.dumps(payload))
        self.assertFalse(r["token_present"])
        self.assertEqual(r["status"], 200)

    @patch("em_contract_probe.send_probe")
    def test_load_probe_result_live走send_probe(self, m_send):
        m_send.return_value = dict(ep.DEMO_CONTRACT)
        args = ep.build_parser().parse_args(["--live"])
        self.assertEqual(ep.load_probe_result(args), ep.DEMO_CONTRACT)
        m_send.assert_called_once_with()

    def test_load_probe_result_demo走离线样例(self):
        args = ep.build_parser().parse_args(["--demo"])
        with patch("em_contract_probe.send_probe") as m_send:
            r = ep.load_probe_result(args)
        self.assertEqual(r, ep.DEMO_CONTRACT)
        m_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
