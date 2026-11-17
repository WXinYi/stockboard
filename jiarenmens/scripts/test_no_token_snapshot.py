"""2026-11-16 周一 midday 卡死后新增：no-token 并发采集的契约回归。

锁住四件事：
  1. payload 形状与前端 useEmPlayer.js 一致，且**绝不携带 token**；
  2. 并发下 2N/3N 调用次数、顺序与串行完全一致（K1 口径不变）；
  3. 确定性失败码不重试、瞬时失败只重试 1 次（8s × 2 上限）；
  4. --demo 仍零网络（NoTokenClient 不被 demo 路径触碰）。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_snapshot as ps  # noqa: E402


class FakeResp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class TestNoTokenClient(unittest.TestCase):
    def setUp(self):
        self.sent = []

    def transport(self, responses):
        it = iter(responses)

        def post(url, json=None, timeout=None):
            self.sent.append({"url": url, "payload": json, "timeout": timeout})
            nxt = next(it)
            if isinstance(nxt, Exception):
                raise nxt
            return FakeResp(nxt)
        return post

    def test_payload_与前端一致且无token(self):
        c = ps.NoTokenClient(transport=self.transport([{"code": 0, "data": []}]))
        c.player_position("900001", "u1")
        self.assertEqual(len(self.sent), 1)
        p = self.sent[0]
        self.assertEqual(p["url"], ps.V2_API_URL)
        self.assertEqual(p["timeout"], ps.NO_TOKEN_TIMEOUT)
        payload = p["payload"]
        self.assertEqual(payload["client"], "ios")
        self.assertEqual(payload["clientType"], "cfw")
        self.assertEqual(payload["clientVersion"], ps.NO_TOKEN_VERSION)
        self.assertEqual(payload["method"], ps.NO_TOKEN_METHODS["position"])
        self.assertEqual(set(payload["args"]), {"userId", "combinationId"})
        blob = json.dumps(payload)
        for banned in ("ctToken", "utToken", "randomCode", "deviceId"):
            self.assertNotIn(banned, blob, f"no-token payload 不得含 {banned}")

    def test_持仓分块解析与收益字段映射(self):
        pos = [{"data": [{"__code": "300757", "__name": "罗博特科",
                          "positionRateDetail": "22.139188", "webYkRate": "-1.89"}]}]
        yld = {"totalYieldRate": "34.6279", "dayYieldRate": "-0.0739",
               "monthYieldRate": "-0.1928", "yearYieldRate": "0.1202",
               "netWorth": "35.628"}
        c = ps.NoTokenClient(transport=self.transport([
            {"code": 0, "data": pos}, {"code": 0, "data": yld}]))
        rows = c.player_position("900001", "u1")
        self.assertEqual(rows, [{"code": "300757", "name": "罗博特科",
                                 "ratio": 22.139188, "profit": -1.89}])
        y = c.player_yield("900001", "u1")
        self.assertEqual(set(y), {"totalReturn", "dailyReturn", "monthlyReturn",
                                  "yearlyReturn", "netValue"})
        # _pct 口径 0.4570 → 45.70（与 EmClient 既有换算一致）
        self.assertAlmostEqual(y["totalReturn"], 3462.79, places=2)
        self.assertAlmostEqual(y["monthlyReturn"], -19.28, places=2)
        self.assertEqual(y["netValue"], 35.628)

    def test_缺失收益写None不用0冒充(self):
        c = ps.NoTokenClient(transport=self.transport([{"code": 0, "data": {"netWorth": "1"}}]))
        y = c.player_yield("900001", "u1")
        self.assertEqual(y["totalReturn"], None)
        self.assertEqual(y["monthlyReturn"], None)
        self.assertEqual(y["netValue"], 1.0)

    def test_确定性失败不重试_瞬时失败只1次(self):
        c = ps.NoTokenClient(transport=self.transport([{"code": 9061, "message": "gone"}]))
        with patch.object(ps.time, "sleep") as slp:
            c.player_position("900001", "u1")
        self.assertEqual(len(self.sent), 1, "9061 是确定性失败，不应重试")
        slp.assert_not_called()

        before = len(self.sent)
        c2 = ps.NoTokenClient(transport=self.transport([
            TimeoutError("slow"), {"code": 0, "data": []}]))
        with patch.object(ps.time, "sleep") as slp:
            c2.player_position("900001", "u1")
        self.assertEqual(len(self.sent) - before, 2, "MAX_RETRY=1 → 最多 2 次尝试")
        self.assertEqual(slp.call_count, 1)

    def test_调仓当日过滤(self):
        data = {"pages": [
            {"bizDate": "20261116", "stkCode": "000001"},
            {"bizDate": "20261113", "stkCode": "000002"},
        ]}
        c = ps.NoTokenClient(transport=self.transport([{"code": 0, "data": data}]))
        rows = c.player_trades("900001", "u1", "2026-11-16", "2026-11-16", 1)
        self.assertEqual(len(rows), 1, "历史行必须被 D3 当日过滤剔除")
        self.assertEqual(rows[0]["stock_code"], "000001")

    def test_调仓日期发接口必须是紧凑YYYYMMDD(self):
        """11-16 事故回归：带连字符的 2026-11-16 会让 rtV2 静默返回 totalPages=0。

        同一选手实测：空日期/紧凑 20261116 → 当日 5 条；2026-11-16 → 0 条。
        客户端 D3 过滤仍用 YYYY-MM-DD，只有发给接口的 args 转紧凑格式。
        """
        c = ps.NoTokenClient(transport=self.transport([{"code": 0, "data": {"pages": []}}]))
        c.player_trades("900001", "u1", "2026-11-16", "2026-11-30", 2)
        self.assertEqual(len(self.sent), 1)
        args = self.sent[0]["payload"]["args"]
        self.assertEqual(args["beginDate"], "20261116")
        self.assertEqual(args["endDate"], "20261130")
        self.assertEqual(args["pageNum"], 2)
        self.assertEqual(args["pageSize"], ps.RANK_PAGE_SIZE)
        self.assertNotIn("-", args["beginDate"], "接口日期不得含连字符")

    def test_紧凑日期helper幂等(self):
        self.assertEqual(ps._compact_date("2026-11-16"), "20261116")
        self.assertEqual(ps._compact_date("20261116"), "20261116")
        self.assertEqual(ps._compact_date(""), "")


class TestConcurrentSnapshotContract(unittest.TestCase):
    """并发与串行的调用次数/顺序必须完全一致（K1 口径不变）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        n = 12
        players = [{"id": f"900{i:05d}", "name": f"p{i}", "userId": f"u{i}",
                    "watched": False, "prevClose": {}, "today": {}} for i in range(n)]
        (self.out / "players.json").write_text(
            json.dumps({"date": "2026-11-16", "rankTime": "09:26", "players": players}),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, snapshot, concurrency):
        master = ps.NoTokenClient(transport=lambda *a, **k: FakeResp({"code": 0, "data": {}}))
        master.calls = []
        factory = lambda: ps.NoTokenClient(
            transport=lambda *a, **k: FakeResp({"code": 0, "data": {}}))
        ps.cmd_snapshot(self.out, master, "2026-11-16", snapshot,
                        "2026-11-16T11:30:00+08:00", 1,
                        client_factory=factory, concurrency=concurrency,
                        progress_every=0)
        return master.calls

    def test_midday_并发与串行调用序列一致_2N(self):
        serial = self._run(ps.SNAPSHOT_MIDDAY, 1)
        parallel = self._run(ps.SNAPSHOT_MIDDAY, 4)
        self.assertEqual(len(parallel), len(serial))
        self.assertEqual(len(parallel), 24)          # 12 人 × 2
        self.assertEqual(parallel, serial)            # 顺序也一致
        self.assertEqual(sum(1 for c in parallel if c[0] == "yield"), 0)

    def test_close_并发与串行调用序列一致_3N(self):
        serial = self._run(ps.SNAPSHOT_CLOSE, 1)
        parallel = self._run(ps.SNAPSHOT_CLOSE, 4)
        self.assertEqual(len(parallel), len(serial))
        self.assertEqual(len(parallel), 36)          # 12 人 × 3
        self.assertEqual(parallel, serial)
        self.assertEqual(sum(1 for c in parallel if c[0] == "yield"), 12)

    def test_apiCalls_日志计数等于实际调用数(self):
        """2026-11-16 用户选B：让日志打出真实调用数，apiCalls 必须=2N/3N。"""
        for snapshot, expected in ((ps.SNAPSHOT_MIDDAY, 24), (ps.SNAPSHOT_CLOSE, 36)):
            master = ps.NoTokenClient(transport=lambda *a, **k: FakeResp({"code": 0, "data": {}}))
            factory = lambda: ps.NoTokenClient(
                transport=lambda *a, **k: FakeResp({"code": 0, "data": {}}))
            info = ps.cmd_snapshot(self.out, master, "2026-11-16", snapshot,
                                   "2026-11-16T15:15:00+08:00", 1,
                                   client_factory=factory, concurrency=4,
                                   progress_every=0)
            self.assertEqual(info["apiCalls"], expected,
                             f"{snapshot} apiCalls 应等于实际 rtV2 调用数")
            self.assertEqual(info["apiCalls"], len(master.calls))
            self.assertEqual(info["playerCount"], 12)


class TestTradeConsensus(unittest.TestCase):
    """2026-11-16 调仓共识页：买卖人数去重 / 净买入排序 / 方向未知不猜测。"""

    def test_买卖人数按选手去重(self):
        trades_by = {
            "u1": [
                {"stock_code": "002524", "stock_name": "光正眼科", "bs": "B", "time": "2026-11-16 10:00:37"},
                {"stock_code": "002524", "stock_name": "光正眼科", "bs": "B", "time": "2026-11-16 10:05:00"},
            ],
            "u2": [{"stock_code": "002524", "stock_name": "光正眼科", "bs": "S", "time": "2026-11-16 11:00:00"}],
            "u3": [{"stock_code": "600000", "stock_name": "浦发银行", "bs": "B", "time": "2026-11-16 09:35:00"}],
        }
        c = ps.aggregate_trade_consensus(trades_by)
        self.assertEqual(c["totalTrades"], 4)
        self.assertEqual(c["totalBuyers"], 2)          # u1 + u3
        by_code = {s["code"]: s for s in c["stocks"]}
        gd = by_code["002524"]
        self.assertEqual(gd["buy"], 1, "u1 买两次只算 1 人")
        self.assertEqual(gd["sell"], 1)
        self.assertEqual(gd["buyTrades"], 2, "笔数不去重")
        self.assertEqual(gd["net"], 0)
        self.assertEqual(gd["lastTime"], "11:00:00", "取最后一笔时间")
        self.assertEqual(gd["name"], "光正眼科")

    def test_按净买入排序_方向未知不算(self):
        trades_by = {
            "u1": [{"stock_code": "A", "bs": "B"}, {"stock_code": "B", "bs": "S"}],
            "u2": [{"stock_code": "A", "bs": "B"}, {"stock_code": "C", "bs": ""}],
        }
        c = ps.aggregate_trade_consensus(trades_by)
        self.assertEqual(c["stocks"][0]["code"], "A")
        codes = {s["code"] for s in c["stocks"]}
        self.assertNotIn("C", codes, "bs 为空的方向未知票不进共识")
        self.assertEqual(c["stocks"][0]["buy"], 2)

    def test_today_doc默认带空共识_保持向后兼容(self):
        d = ps.build_today_doc("2026-11-16", "2026-11-16T15:15:00+08:00", ["u1"])
        self.assertEqual(d["tradeConsensus"], {
            "totalTrades": 0, "totalBuyers": 0,
            "aTotalTrades": 0, "aTotalBuyers": 0, "stocks": [],
        })
        self.assertEqual(d["tradedPlayerIds"], ["u1"])

    def test_A股口径统计独立于全市场(self):
        trades_by = {
            "u1": [{"stock_code": "002524", "bs": "B"}, {"stock_code": "01810", "bs": "B"}],
            "u2": [{"stock_code": "600000", "bs": "B"}, {"stock_code": "002524", "bs": "S"}],
            "u3": [{"stock_code": "03690", "bs": "B"}],
        }
        c = ps.aggregate_trade_consensus(trades_by)
        self.assertEqual(c["totalTrades"], 5)
        self.assertEqual(c["totalBuyers"], 3)
        self.assertEqual(c["aTotalTrades"], 3)
        self.assertEqual(c["aTotalBuyers"], 2)

    def test_token版player_trades日期也走紧凑格式(self):
        c = ps.EmClient(spider=object(), tokens={"ctToken": "x", "utToken": "y"})
        with patch.object(ps._api_detail, "_call_api_new", return_value={"pages": []}) as call:
            c.player_trades("900001", "u1", "2026-11-16", "2026-11-16", 1)
        args = call.call_args.args[1]
        self.assertEqual(args["beginDate"], "20261116")
        self.assertEqual(args["endDate"], "20261116")

    def test_player_trades保留完整字段(self):
        data = {"pages": [{"bizDate": "20261116", "stkCode": "002524",
                           "stkName": "光正眼科", "bsMark": "B",
                           "relocateTime": "2026-11-16 10:00:37",
                           "positionRatio": "2成", "relocatePrice": "3.540"}]}
        c = ps.NoTokenClient(transport=lambda *a, **k: FakeResp({"code": 0, "data": data}))
        rows = c.player_trades("900001", "u1", "2026-11-16", "2026-11-16", 1)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["bs"], "B")
        self.assertEqual(r["stock_name"], "光正眼科")
        self.assertEqual(r["position_ratio"], "2成")
        self.assertEqual(r["price"], "3.540")
        self.assertEqual(r["time"], "2026-11-16 10:00:37")


if __name__ == "__main__":
    unittest.main()
