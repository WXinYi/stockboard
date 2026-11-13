#!/usr/bin/env python3
"""Story 1.1/1.2/1.3 单测：--rank/--midday/--close 契约（AQ5=A / 75 次 / D3 / 灰度开关）。

覆盖：userId 非空且硬连线自 userid、10 人 watched 强制插入、恰好 75 次榜单分页、
snapshot 枚举、全量 h/tp/ap 无 quality、调仓固定 1 页当日过滤、并集/sticky carry-forward、
SB_DATA_V2=0 no-op、拒绝线上路径。
运行: python3 scripts/test_player_snapshot.py
"""
import json
import os
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_snapshot as ps  # noqa: E402

DATE = "2026-11-12"
MID_AT = "2026-11-12T11:30:00+08:00"
CLOSE_AT = "2026-11-12T15:15:00+08:00"


def client():
    return ps.FixtureClient.from_data(ps.demo_bundle(DATE))


def bundle_with_n_players(n, date):
    """构造恰好 n 名选手的离线 bundle（K1 真实调用计数用）。

    10 名 watched 内嵌于榜单，5 榜各 <= 300 行且互不重叠：``cmd_rank`` 75 次
    分页全量取回、``dedupe_rank_rows`` 去重后恰为 n 人，``force_insert_watched``
    不再新增（watched 已在榜内），故 players.json 恰 n 人 → 每人调用数可精确断言。
    """
    watched = [zh for zh, _ in ps.WATCHED_PLAYERS]
    extra = [f"9100{i:06d}" for i in range(n - len(watched))]
    ids = watched + extra
    per = (n + len(ps.RANK_TYPES) - 1) // len(ps.RANK_TYPES)
    rank = {}
    for idx, rt in enumerate(ps.RANK_TYPES):
        chunk = ids[idx * per:(idx + 1) * per]
        rank[rt] = [{"zjzh": z, "userid": f"UID{z}", "zhuheName": f"Name{z}"}
                    for z in chunk]
    return {"rank": rank, "yield": {}, "position": {}, "trades": {}}


def measure_chain(n, trade_pages=1):
    """实跑 --rank / --midday / --close，用 mock spy + FixtureClient.calls **真实计数**。

    这是 K1 验收的核心：所有数字来自被打桩记录的实际 client 调用，而非
    ``75 + 2600 + 3900 == 6575`` 之类的算术恒等式。
    """
    out = Path(tempfile.mkdtemp())
    bundle = bundle_with_n_players(n, DATE)
    rank_c = ps.FixtureClient.from_data(bundle)
    with patch.object(rank_c, "rank_page", wraps=rank_c.rank_page) as m_rank:
        rank_doc = ps.cmd_rank(out, rank_c, DATE, 300)
    result = {"players_n": len(rank_doc["players"]), "rank": m_rank.call_count,
              "rank_calls": list(rank_c.calls)}
    for key, snapshot, at in (("midday", ps.SNAPSHOT_MIDDAY, MID_AT),
                              ("close", ps.SNAPSHOT_CLOSE, CLOSE_AT)):
        c = ps.FixtureClient.from_data(bundle)
        with patch.object(c, "player_yield", wraps=c.player_yield) as m_yield, \
                patch.object(c, "player_position", wraps=c.player_position) as m_pos, \
                patch.object(c, "player_trades", wraps=c.player_trades) as m_trade:
            ps.cmd_snapshot(out, c, DATE, snapshot, at, trade_pages)
        result[key] = len(c.calls)                 # 实测总调用数
        result[key + "_calls"] = list(c.calls)     # 实测调用明细
        result[key + "_yield"] = m_yield.call_count
        result[key + "_position"] = m_pos.call_count
        result[key + "_trades"] = m_trade.call_count
    return result


class TestRank(unittest.TestCase):
    """Story 1.1 · 09:26 榜单唯一一次。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_榜单恰好75次分页_5榜x300(self):
        c = client()
        doc = ps.cmd_rank(self.tmp, c, DATE, 300)
        self.assertEqual(c.rank_calls(), 75, "5 榜 × 300/20 = 恰好 75 次（K3）")
        self.assertEqual(doc["rankTime"], "09:26")
        self.assertEqual(doc["date"], DATE)

    def test_userId_非空_且硬连线自排行榜_userid_字段(self):
        """AQ5=A：userId 直接取每行 userid，0 额外请求、无回退分支。"""
        c = client()
        doc = ps.cmd_rank(self.tmp, c, DATE, 300)
        for p in doc["players"]:
            self.assertTrue(p["userId"], f"{p['id']} 缺 userId")
        by = {p["id"]: p for p in doc["players"]}
        self.assertEqual(by["900000001"]["userId"], "1000000000000001")
        self.assertEqual(by["900293400"]["userId"], "6838395742604432")
        self.assertEqual(c.rank_calls(), 75, "不得有逐人补解析调用")

    def test_10人watched_无条件强制插入_含榜外者(self):
        c = client()
        doc = ps.cmd_rank(self.tmp, c, DATE, 300)
        watched = [p for p in doc["players"] if p["watched"]]
        self.assertEqual(len(watched), 10, "恰好 10 条 watched=true")
        ids = {p["id"] for p in doc["players"]}
        for zh_id, _ in ps.WATCHED_PLAYERS:
            self.assertIn(zh_id, ids, f"{zh_id} 无论是否入榜都必须插入")
        outside = next(p for p in doc["players"] if p["id"] == "900456476")
        self.assertTrue(outside["userId"], "榜外 watched 也必须有非空 userId")

    def test_AQ5A_缺userid_显式失败_不实现回退分支(self):
        rows = {"10004": [{"zjzh": "900000009", "zhuheName": "缺字段", "userid": ""}]}
        with self.assertRaises(ValueError) as ctx:
            ps.dedupe_rank_rows(rows)
        self.assertIn("禁止回退分支", str(ctx.exception))

    def test_每个player_必填字段齐全(self):
        doc = ps.cmd_rank(self.tmp, client(), DATE, 300)
        for p in doc["players"]:
            for key in ("id", "name", "userId", "watched", "prevClose", "today"):
                self.assertIn(key, p)

    def test_产物原子落盘(self):
        ps.cmd_rank(self.tmp, client(), DATE, 300)
        disk = json.loads((self.tmp / "players.json").read_text(encoding="utf-8"))
        self.assertEqual(disk["rankTime"], "09:26")
        self.assertFalse((self.tmp / "players.json.tmp").exists())

    def test_EM_RANK_LIMIT_可配_20时恰好5次(self):
        c = client()
        ps.cmd_rank(self.tmp, c, DATE, 20)
        self.assertEqual(c.rank_calls(), 5)

    def test_K1_请求量口径6575_真实mock计数(self):
        """K1 / §7.3 硬验收①：N=1300 → 75 + 2N + 3N = 6575，**全部由实跑计数导出**。

        AC-1.3 / 架构 §7.3：midday 2 调用/人、close 3 调用/人（close 多收益接口）。
        本用例构造恰好 1300 人的 fixture，实跑 3 子命令，直接读取 mock spy 记录的
        client 调用数并断言 —— 不是算术恒等式、不 mock 空气。
        """
        n = 1300
        m = measure_chain(n)
        self.assertEqual(m["players_n"], n, "fixture 恰好 1300 人")
        # --rank：5 榜 × 300/20 = 恰好 75 次榜单 API
        self.assertEqual(m["rank"], 75)
        self.assertEqual(sum(1 for c in m["rank_calls"] if c[0] == "rank_page"), 75)
        # --midday：2 调用/人 = 2600，且绝不调收益接口
        self.assertEqual(m["midday"], 2600)
        self.assertEqual(m["midday_position"], n)
        self.assertEqual(m["midday_trades"], n)
        self.assertEqual(m["midday_yield"], 0, "AC-1.3：midday 无 yield")
        self.assertEqual(Counter(x[0] for x in m["midday_calls"]),
                         {"position": n, "trades": n})
        # --close：3 调用/人 = 3900，额外一次仅收益接口
        self.assertEqual(m["close"], 3900)
        self.assertEqual(m["close_position"], n)
        self.assertEqual(m["close_trades"], n)
        self.assertEqual(m["close_yield"], n, "AC-1.3：close 每人 1 次 yield")
        self.assertEqual(Counter(x[0] for x in m["close_calls"]),
                         {"position": n, "trades": n, "yield": n})
        # 4-JSON 链路总请求量 = 各子命令实测调用数之和（非算术登记）
        total = m["rank"] + m["midday"] + m["close"]
        self.assertEqual(total, 6575)
        # close 与 midday 之差恰为 yield 调用（set 差集）
        diff = Counter(x[0] for x in m["close_calls"]) - \
            Counter(x[0] for x in m["midday_calls"])
        self.assertEqual(dict(diff), {"yield": n})


class TestSnapshot(unittest.TestCase):
    """Story 1.2/1.3 · 午盘/收盘两班（D3 固定 1 页）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        ps.cmd_rank(self.tmp, client(), DATE, 300)

    def _run(self, snapshot):
        c = client()
        at = MID_AT if snapshot == ps.SNAPSHOT_MIDDAY else CLOSE_AT
        info = ps.cmd_snapshot(self.tmp, c, DATE, snapshot, at, 1)
        return c, info

    def _stocks(self):
        return json.loads((self.tmp / "stocks.json").read_text(encoding="utf-8"))

    def test_midday_stocks_枚举1130_全量h_tp_ap_无quality(self):
        self._run(ps.SNAPSHOT_MIDDAY)
        doc = self._stocks()
        self.assertEqual(doc["snapshot"], "1130")
        self.assertIn("stats", doc)
        self.assertNotIn("stockStats", doc, "新 schema 不得含旧键")
        for s in doc["stats"]:
            for k in ("code", "name", "h", "tp", "ap"):
                self.assertIn(k, s)
            for k in ("qh", "qtp", "qap"):
                self.assertNotIn(k, s, "S1 全量口径, 无 quality")

    def test_close_调用集合与midday不同_恰好多一次yield(self):
        """AC-1.3（Review M-01 反转）：close 与 midday 之差恰为 yield 调用。"""
        c_mid, _ = self._run(ps.SNAPSHOT_MIDDAY)
        c_close, _ = self._run(ps.SNAPSHOT_CLOSE)
        players = json.loads((self.tmp / "players.json").read_text(encoding="utf-8"))["players"]
        n = len(players)
        self.assertNotEqual(c_mid.calls, c_close.calls, "close 与 midday 调用集合必须不同")
        # 去掉 yield 后两班调用序列完全一致
        self.assertEqual([x for x in c_close.calls if x[0] != "yield"], c_mid.calls)
        self.assertEqual(sum(1 for x in c_mid.calls if x[0] == "yield"), 0)
        self.assertEqual(sum(1 for x in c_close.calls if x[0] == "yield"), n)
        diff = Counter(x[0] for x in c_close.calls) - Counter(x[0] for x in c_mid.calls)
        self.assertEqual(dict(diff), {"yield": n}, "差异恰为每人 1 次收益接口")
        self.assertEqual(self._stocks()["snapshot"], "close")

    def test_midday_每人2核心调用_无yield_调仓固定1页_当日过滤(self):
        """AC-1.3：midday = position + trades = 2 调用/人，绝不调收益接口。"""
        c, _ = self._run(ps.SNAPSHOT_MIDDAY)
        players = json.loads((self.tmp / "players.json").read_text(encoding="utf-8"))["players"]
        n = len(players)
        self.assertEqual(sum(1 for x in c.calls if x[0] == "yield"), 0, "midday 无 yield")
        self.assertEqual(sum(1 for x in c.calls if x[0] == "position"), n, "持仓 1 调用/人")
        trades = [x for x in c.calls if x[0] == "trades"]
        self.assertEqual(len(trades), n, "D3: 每人恰好 1 页调仓, 不取历史 40 条")
        self.assertEqual(len(c.calls), 2 * n, "midday 恰 2 调用/人")
        for t in trades:
            self.assertEqual(t[2], DATE, "beginDate=当日")
            self.assertEqual(t[3], DATE, "endDate=当日")
            self.assertEqual(t[4], 1, "page 固定 1")

    def test_close_每人3核心调用_含yield(self):
        """AC-1.3：close = position + trades + yield = 3 调用/人。"""
        c, _ = self._run(ps.SNAPSHOT_CLOSE)
        n = len(json.loads((self.tmp / "players.json").read_text(encoding="utf-8"))["players"])
        self.assertEqual(sum(1 for x in c.calls if x[0] == "yield"), n)
        self.assertEqual(sum(1 for x in c.calls if x[0] == "position"), n)
        self.assertEqual(sum(1 for x in c.calls if x[0] == "trades"), n)
        self.assertEqual(len(c.calls), 3 * n)

    def test_EM_TRADE_PAGES_默认1_实跑生效(self):
        """Review m-05：常量自比较 → 改为实跑断言默认页数真正生效。"""
        self.assertEqual(ps.DEFAULT_TRADE_PAGES, 1)
        c, _ = self._run(ps.SNAPSHOT_MIDDAY)
        n = len(json.loads((self.tmp / "players.json").read_text(encoding="utf-8"))["players"])
        # 默认 1 页 → 每人恰好 1 次 trades（不是常量等于自己）
        self.assertEqual(sum(1 for x in c.calls if x[0] == "trades"), n)
        self.assertEqual(len(c.calls), 2 * n)

    def test_snapshot_非法枚举被显式拒绝(self):
        with self.assertRaises(ValueError):
            ps.build_stocks_doc(DATE, "0900", MID_AT, [])

    def test_today_tradedPlayerIds_由调仓生成_并集carry_forward(self):
        self._run(ps.SNAPSHOT_MIDDAY)
        today = json.loads((self.tmp / "today.json").read_text(encoding="utf-8"))
        self.assertEqual(today["tradedPlayerIds"], ["900000002", "900293400"])
        self._run(ps.SNAPSHOT_CLOSE)
        today2 = json.loads((self.tmp / "today.json").read_text(encoding="utf-8"))
        self.assertEqual(today2["tradedPlayerIds"], ["900000002", "900293400"])

    def test_emContract_sticky_不被重置(self):
        (self.tmp / "today.json").write_text(json.dumps({
            "date": DATE, "updatedAt": "x", "tradedPlayerIds": [],
            "emContract": {"ok": False, "checkedAt": "old"}}), encoding="utf-8")
        self._run(ps.SNAPSHOT_CLOSE)
        today = json.loads((self.tmp / "today.json").read_text(encoding="utf-8"))
        self.assertEqual(today["emContract"], {"ok": False, "checkedAt": "old"})

    def test_close_刷新prevClose_midday盘中不变(self):
        self._run(ps.SNAPSHOT_MIDDAY)
        mid = json.loads((self.tmp / "players.json").read_text(encoding="utf-8"))
        self.assertEqual(mid["players"][0]["prevClose"], {})
        self._run(ps.SNAPSHOT_CLOSE)
        close = json.loads((self.tmp / "players.json").read_text(encoding="utf-8"))
        self.assertIn("totalReturn", close["players"][0]["prevClose"])


class TestGuards(unittest.TestCase):
    """AQ3=B 线上路径硬守卫 + Epic 4 灰度开关。"""

    def test_拒绝写入线上_public_data_latest(self):
        with self.assertRaises(SystemExit):
            ps.resolve_out("/x/stockboard-app/public/data/latest")

    def test_SB_DATA_V2_0_为_noop_kill_switch_且不写任何文件(self):
        d = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"SB_DATA_V2": "0"}):
            self.assertTrue(ps.kill_switch_active())
            rc = ps.main(["--rank", "--demo", "--out", str(d)])
        self.assertEqual(rc, 0)
        self.assertEqual(list(d.iterdir()), [], "kill switch 下不产任何文件")


if __name__ == "__main__":
    unittest.main()
