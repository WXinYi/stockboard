#!/usr/bin/env python3
"""
盘中实时监控: watchlist 轮询盘口五档 → 异动信号 → 买卖建议(规则话术) → 终端 + 本地HTML页

数据边界:
  - 读: stockboard-app/public/data/latest/auction.json(当日 09:25 扫描快照, Actions 提交)
        auction.db(昨日连板梯队, 只读) / hot_rank.db(人气榜, 只读)
  - 写: data/intraday.db(signals+snaps, 本地独享, 不提交) + data/intraday_page.html(本地信号页)

用法(在 jiarenmens/ 目录):
  python scripts/intraday_monitor.py                # 交易日常驻(09:26 自启, 15:10 自退)
  python scripts/intraday_monitor.py --once         # 单轮快照(接口连通性测试)
  python scripts/intraday_monitor.py --simulate     # 合成行情走通信号引擎+HTML(休市日自测)
  python scripts/intraday_monitor.py --interval 30  # 轮询间隔(默认60s, 异动票自动升频15s)
  python scripts/intraday_monitor.py --extra 002437,603110
  python scripts/intraday_monitor.py --install-launchd   # 安装/更新 LaunchAgent(交易日 09:26 自启)
"""
import argparse
import json
import sqlite3
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.spiders.auction_spider import KPLSpider  # noqa: E402
from src.analysis.emotion_cycle import compute_cycle  # noqa: E402
from src.analysis.stage_candidates import stage_pool  # noqa: E402

REPO = ROOT.parent
AUCTION_JSON = REPO / "stockboard-app" / "public" / "data" / "latest" / "auction.json"
AUCTION_DB = ROOT / "data" / "auction.db"
HOT_DB = ROOT / "data" / "hot_rank.db"
INTRADAY_DB = ROOT / "data" / "intraday.db"
HTML_PAGE = ROOT / "data" / "intraday_page.html"
EXTRA_FILE = ROOT / "data" / "watchlist_extra.txt"

MORNING = ((9, 30), (11, 30))
AFTERNOON = ((13, 0), (15, 0))
EXIT_AT = (15, 10)
BOOST_AFTER_SIGNAL = 300          # 触发信号后升频时长(s)
BOOST_INTERVAL = 15
SIGNAL_COOLDOWN = 600             # 同票同信号冷却(s)
BOARD_COOLDOWN = 300

# ---------------- 信号定义 → 买卖建议(话术, 来源: 龙头战法文章) ----------------
ADVICE = {
    "水下直线拉升": ("🚀", "green",
        "弱转强候选：低开被拉起，看是否站稳分时均价；回踩不破均价可低吸，不追已高开>7%的票"),
    "快速拉升": ("🚀", "green",
        "直线拉升：结合板块同动判断是主线修复还是个股脉冲；放量站稳均价才动手"),
    "封板": ("🔴", "bright",
        "封板：首次封板看封单是否持续增大；持仓拿住（看准坚定持有到巅峰），未持有打板需确认板块共振"),
    "炸板": ("💥", "red",
        "炸板：封单不稳，纪律第一——不板即出（非趋势/逻辑票），持仓先减半，观察回封力度"),
    "回封": ("♻️", "green",
        "回封：分歧转一致信号，封单回补才算数；二次炸板直接走"),
    "封单松动": ("⚠️", "yellow",
        "封单骤降：涨停不烂不是牛，烂板次日预期差大；尾盘松动按炸板预案处理"),
    "委比骤降": ("⚠️", "yellow",
        "委比骤降/撤单：买盘承接减弱，冲高票防回落，持仓做好兑现准备"),
    "跌破均价": ("📉", "red",
        "冲高回落跌破分时均价：冲高兑现纪律，持有减仓，勿接飞刀"),
    "急跌": ("🩸", "red",
        "急跌：止损纪律优先，退潮期不吸不及预期的杀跌"),
    "板块同动": ("🌐", "cyan",
        "板块级信号：多票同动=板块修复/启动，看板块内最强势标的，跟风只做最强（去弱留强）"),
}
BOOSTABLE = {"水下直线拉升", "快速拉升", "封板", "炸板", "回封"}



class WatchStock:
    """单票状态: 快照窗口 + 已发信号冷却"""

    def __init__(self, code: str, name: str, boards: list, tags: list):
        self.code, self.name, self.boards, self.tags = code, name, boards, tags
        self.hist = deque(maxlen=40)          # (ts, last_px, change, entrust, avg_px, seal_vol)
        self.was_sealed = False
        self.boomed = False
        self.seal_peak = 0.0
        self.last_signal_ts = {}              # signal -> ts
        self.boost_until = 0.0
        self.last_change = None
        self.role = ""
        self.height = None
        self.status = "观察"

    def feed(self, ts, last_px, change, entrust, avg_px, vol_ratio, up_px, seal_vol):
        self.hist.append((ts, last_px, change, entrust, avg_px, seal_vol))
        sealed = up_px is not None and last_px is not None and last_px >= up_px - 1e-6
        signals = []

        def emit(sig, detail):
            if ts - self.last_signal_ts.get(sig, 0) >= SIGNAL_COOLDOWN:
                self.last_signal_ts[sig] = ts
                signals.append((sig, detail))
                if sig in BOOSTABLE:
                    self.boost_until = ts + BOOST_AFTER_SIGNAL

        if seal_vol is not None and sealed:
            self.seal_peak = max(self.seal_peak, seal_vol)
        if sealed and not self.was_sealed:
            if self.boomed:
                emit("回封", f"炸板后重新封住，封单≈{seal_vol:.0f}手" if seal_vol else "炸板后重新封住")
            else:
                emit("封板", f"封单≈{seal_vol:.0f}手" if seal_vol else "")
        elif not sealed and self.was_sealed:
            self.boomed = True
            emit("炸板", "触及涨停后打开")
        elif sealed and self.seal_peak > 0 and seal_vol is not None \
                and seal_vol < self.seal_peak * 0.5:
            emit("封单松动", f"封单 {seal_vol:.0f}手 < 峰值{self.seal_peak:.0f}手的一半")
        if not sealed:
            self.seal_peak = 0.0
        self.was_sealed = sealed

        # 涨幅变化类: 回看 60s/300s 找基准点
        def tick_at(seconds_ago):
            target = ts - seconds_ago
            best = None
            for h in self.hist:
                if h[0] <= target:
                    best = h
            return best

        cur = self.hist[-1]
        c1 = cur[2]
        base60, base300, base120 = tick_at(60), tick_at(300), tick_at(120)
        if c1 is not None and base60 is not None and base60[2] is not None:
            d60, dt60 = c1 - base60[2], cur[0] - base60[0]
            if dt60 > 0:
                if d60 >= 1.5:
                    emit("快速拉升", f"{dt60:.0f}s 内 +{d60:.1f}pp（现 {c1:+.1f}%）")
                elif d60 <= -2.0:
                    emit("急跌", f"{dt60:.0f}s 内 {d60:+.1f}pp（现 {c1:+.1f}%）")
        if c1 is not None and base300 is not None and base300[2] is not None:
            d300, dt300 = c1 - base300[2], cur[0] - base300[0]
            if d300 >= 2.5 and base300[2] <= -0.5 and dt300 <= 600:
                emit("水下直线拉升", f"{dt300:.0f}s 内 {base300[2]:+.1f}% → {c1:+.1f}%")
        # 委比骤降(对比 120s 前)
        if base120 is not None and base120[3] is not None and cur[3] is not None \
                and base120[3] > 30 and cur[3] < -10:
            emit("委比骤降", f"委比 {base120[3]:.0f} → {cur[3]:.0f}")
        # 跌破分时均价(冲高回落)
        a1 = cur[4]
        if a1 and cur[1] and c1 and c1 > 1:
            above = [h for h in self.hist if h[1] is not None and h[4] and h[1] >= h[4]]
            if len(above) >= 3 and cur[1] < a1 * 0.995:
                emit("跌破均价", f"现价跌破分时均价 {a1:.2f}")
        return signals


class Monitor:
    def __init__(self, args):
        self.args = args
        self.spider = KPLSpider()
        self.date = args.date or date.today().isoformat()
        self.stocks: list[WatchStock] = []
        self.board_last_fire = {}
        self.simulate = args.simulate
        self.db = None
        self.cycle = None

    # ---------- watchlist ----------
    def git_pull(self):
        import subprocess
        try:
            r = subprocess.run(["git", "-C", str(REPO), "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=120)
            tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            print(f"  🔄 git pull: {tail[-1] if tail else 'ok'}")
        except Exception as e:
            print(f"  ⚠️ git pull 失败(用本地现有数据继续): {e}")

    def build_watchlist(self):
        """盯盘对象完全由情绪周期产生: 周期 → 阶段候选池 (+ 手动自选)"""
        j = json.loads(AUCTION_JSON.read_text()) if AUCTION_JSON.exists() else {}
        if j.get("date"):
            self.date = j["date"]
        try:
            self.cycle = compute_cycle(self.date)
        except Exception as e:
            print(f"⚠️ 周期引擎失败({e})，退化为仅手动自选模式")
            self.cycle = None
        self.stocks = []
        if self.cycle:
            c = self.cycle
            roles = {l["code"]: l["role"] for l in c["leaders"]}
            print("\n" + "=" * 60)
            print(f"🌐 当前超短格局 · {c['date']} · 周期: {c['stage']} (置信度 {c['confidence']}/9)")
            for x in c["reasons"]:
                print(f"   · {x}")
            for l in c["leaders"][:5]:
                print(f"   [{l['role']}] {l['name']} {l['pid']}板 — {l['note']}")
            print(f"   📌 阶段纪律: {c['playbook']}")
            print("=" * 60)
            pool = stage_pool(c, self.args.max)
            print(f"\n📋 阶段候选池[{c['stage']}]({len(pool)}):")
            for p in pool:
                ws = WatchStock(p["code"], p["name"], [], [p["reason"]])
                ws.status = p["status"]
                ws.height = p["height"]
                ws.role = roles.get(p["code"], "")
                ws.prio = 0 if p["status"].startswith("可做") else                     1 if p["status"].startswith("观察") else 2
                self.stocks.append(ws)
                print(f"   {p['name'] or p['code']:8s} {p['code']} [{p['reason']}] → {p['status']}")
        # 手动自选始终保留
        extra = list(self.args.extra or [])
        if EXTRA_FILE.exists():
            extra += [x.strip() for x in EXTRA_FILE.read_text().splitlines()
                      if x.strip() and not x.startswith("#")]
        have = {s.code for s in self.stocks}
        for code in extra:
            code = code.zfill(6)
            if code not in have:
                self.stocks.append(WatchStock(code, code, [], ["自选"]))
        print(f"   （共 {len(self.stocks)} 只进入盘中轮询）\n")

    # ---------- 轮询 ----------
    def poll_one(self, s: WatchStock, ts, step=None):
        try:
            if self.simulate:
                snap = simulate_tick(s, step if step is not None else 0)
            else:
                pk = self.spider.stock_pankou(s.code)
                real = pk.get("real") or {}
                wt = pk.get("weituo") or {}
                if str(pk.get("day")) != self.date.replace("-", ""):
                    s.last_change = None
                    return None
                b1 = wt.get("b1") or [None, 0]
                seal_vol = b1[1] if isinstance(b1, list) and len(b1) > 1 else None
                snap = dict(last_px=real.get("last_px"),
                            change=real.get("px_change_rate"),
                            entrust=real.get("entrust_rate"),
                            avg_px=real.get("avg_px"),
                            vol_ratio=real.get("vol_ratio"),
                            up_px=real.get("up_px"),
                            seal_vol=seal_vol,
                            name=pk.get("name") or s.name)
            if snap["name"]:
                s.name = snap["name"]
        except Exception as e:
            print(f"  ⚠️ {s.code} 拉取失败: {e}")
            return None
        sigs = s.feed(ts, snap["last_px"], snap["change"], snap["entrust"],
                      snap["avg_px"], snap["vol_ratio"], snap["up_px"], snap["seal_vol"])
        self.record_snaps(s, ts, snap)
        return snap, sigs

    def record_snaps(self, s, ts, snap):
        if self.db is None or self.simulate:
            return
        with self.db:
            self.db.execute(
                "INSERT INTO snaps(date,ts,code,last_px,change_rate,entrust_rate,vol_ratio,avg_px,sealed,seal_vol)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.date, int(ts), s.code, snap["last_px"], snap["change"], snap["entrust"],
                 snap["vol_ratio"], snap["avg_px"], int(s.was_sealed), snap["seal_vol"]))

    def save_signal(self, s: WatchStock, ts, sig, detail, advice):
        if self.db is None or self.simulate:
            return
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO signals(date,ts,code,name,signal,detail,advice,boards)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (self.date, int(ts), s.code, s.name, sig, detail, advice,
                 "/".join(s.boards[:3])))

    # ---------- 信号处理 ----------
    def handle(self, s: WatchStock, ts, snap, sigs):
        now = datetime.now()
        for sig, detail in sigs:
            emoji, color, advice = ADVICE.get(sig, ("❓", "", ""))
            line = f"{now:%H:%M:%S} {emoji}[{sig}] {s.name}({s.code}) {detail} [{'/'.join(s.tags)}]"
            print(colorize(line, color))
            print(colorize(f"          └─ 建议: {advice}", "dim"))
            self.save_signal(s, ts, sig, detail, advice)
            html_events.append({"t": now.strftime("%H:%M:%S"), "code": s.code, "name": s.name,
                                "sig": sig, "detail": detail, "advice": advice,
                                "tags": "/".join(s.tags), "color": color})
        # 板块同动(誉衡案例): 5分钟内同板块≥2只出现拉升类信号
        if sigs:
            fired = [sig for sig, _ in sigs if sig in BOOSTABLE]
            if fired:
                key = s.boards[0] if s.boards else None
                if key:
                    prev = self.board_last_fire.get(key)
                    self.board_last_fire[key] = ts
                    if prev and ts - prev <= 300:
                        if ts - self.board_last_fire.get("bd_" + key, 0) >= BOARD_COOLDOWN:
                            self.board_last_fire["bd_" + key] = ts
                            msg = (f"🌐[板块同动] {key}: {s.name} 等 5 分钟内多票拉升"
                                   f" → 板块修复/启动信号，只做板块内最强")
                            print(colorize(f"{now:%H:%M:%S} {msg}", "cyan"))
                            html_events.append({"t": now.strftime("%H:%M:%S"), "code": "",
                                                "name": key, "sig": "板块同动", "detail": msg,
                                                "advice": ADVICE["板块同动"][2],
                                                "tags": "板块", "color": "cyan"})

    # ---------- HTML ----------
    def write_html(self):
        rows = []
        for s in self.stocks:
            h = s.hist[-1] if s.hist else None
            rows.append({
                "code": s.code, "name": s.name,
                "tags": "/".join(dict.fromkeys(s.tags + ([s.role] if s.role else []))),
                "px": h[1], "chg": h[2], "entrust": h[3], "sealed": s.was_sealed,
                "status": s.status,
            })
        c = getattr(self, "cycle", None)
        cycle_line = ""
        if c:
            leads = " | ".join(f"{l['name']}{l['pid']}板[{l['role']}]" for l in c["leaders"][:4])
            cycle_line = (f"周期 <b>{c['stage']}</b> · 高度 {c['metrics']['height']}B · "
                          f"涨停 {c['metrics']['zt']} 只 · {leads}<br>"
                          f"<span class='adv'>📌 {c['playbook']}</span>")
        html = render_html(self.date, rows, list(html_events)[-80:], cycle_line)
        HTML_PAGE.write_text(html)

    # ---------- 模式 ----------
    def run_once(self):
        self.build_watchlist()
        print(f"\n== 单轮快照 {datetime.now():%H:%M:%S} ==")
        for s in self.stocks:
            try:
                pk = self.spider.stock_pankou(s.code)
                real = pk.get("real") or {}
                print(f"  {s.name or s.code:6s} {s.code} | {pk.get('day')} | "
                      f"{real.get('last_px')} ({real.get('px_change_rate'):+.2f}%) | "
                      f"委比 {real.get('entrust_rate')} | 量比 {real.get('vol_ratio')}")
            except Exception as e:
                print(f"  {s.code} ❌ {e}")
            time.sleep(0.12)

    def run_simulate(self):
        print("== 模拟模式: 合成行情走通信号引擎(不落库) ==")
        self.simulate = True
        sim_stocks = [
            ("002437", "誉衡药业", "医药"),
            ("600479", "千金药业", "医药"),
            ("300999", "金龙地产", "地产链"),
        ]
        self.stocks = [WatchStock(c, n, [b], ["模拟"]) for c, n, b in sim_stocks]
        t0 = time.time()
        for step in range(46):
            ts = t0 + step * 30
            for s in self.stocks:
                snap, sigs = self.poll_one(s, ts, step=step)
                if snap:
                    self.handle(s, ts, snap, sigs)
            self.write_html()
        print(f"\n== 模拟完成: 共 {len(html_events)} 条信号, HTML: {HTML_PAGE} ==")

    def run(self):
        init_db()
        self.db = sqlite3.connect(INTRADAY_DB)
        self.git_pull()
        self.build_watchlist()
        print(f"🖥️  盘中监控启动 {datetime.now():%Y-%m-%d %H:%M:%S} | HTML: {HTML_PAGE}")
        import webbrowser
        webbrowser.open(f"file://{HTML_PAGE}")
        refreshed = False
        while True:
            now = datetime.now()
            mins = now.hour * 60 + now.minute
            if (now.hour, now.minute) >= EXIT_AT:
                print(f"⏰ {now:%H:%M} 15:10 收工")
                break
            # 09:31 重拉一次: 09:25 扫描由 Actions 提交, 此刻应已落库
            if not refreshed and mins >= 9 * 60 + 31:
                refreshed = True
                print("🔄 09:31 重拉扫描结果, 重建 watchlist")
                self.git_pull()
                self.build_watchlist()
            in_session = (MORNING[0][0] * 60 + MORNING[0][1] <= mins < MORNING[1][0] * 60 + MORNING[1][1]) or \
                         (AFTERNOON[0][0] * 60 + AFTERNOON[0][1] <= mins < AFTERNOON[1][0] * 60 + AFTERNOON[1][1])
            if not in_session:
                print(f"  …非交易时段({now:%H:%M})待机", end="\r", flush=True)
                time.sleep(30)
                continue
            ts = time.time()
            for s in self.stocks:
                r = self.poll_one(s, ts)
                if not r:
                    continue
                snap, sigs = r
                self.handle(s, ts, snap, sigs)
                time.sleep(0.15)
            self.write_html()
            time.sleep(self.args.interval)


# ---------------- 模拟行情 ----------------
SIM_SCRIPT = [
    # (step, [(code, change%, entrust, seal_vol), ...])
    (0, [("002437", -2.0, -30, None), ("600479", 1.0, 20, None), ("300999", 0.5, 10, None)]),
    (6, [("002437", 3.2, 40, None)]),                              # 水下直线拉升
    (8, [("600479", 2.6, 45, None)]),                              # 拉升→板块同动
    (14, [("002437", 10.0, 98, 52000)]),                           # 封板
    (20, [("002437", 10.0, 98, 24000)]),                           # 封单松动
    (24, [("002437", 8.8, 20, None)]),                             # 炸板
    (30, [("002437", 10.0, 99, 61000)]),                           # 回封
    (38, [("300999", -3.2, -40, None)]),                           # 急跌
]


def simulate_tick(s: WatchStock, step: int):
    tick = None
    for st, moves in SIM_SCRIPT:
        if step >= st:
            for code, chg, entrust, seal in moves:
                if code == s.code:
                    tick = (chg, entrust, seal)
    if tick is None:
        prev = s.hist[-1][2] if s.hist else 0.0
        tick = (prev, 0, None)
    chg, entrust, seal = tick
    px = round(10 * (1 + chg / 100), 2)
    return dict(last_px=px, change=chg, entrust=entrust, avg_px=round(px * 0.99, 2),
                vol_ratio=2.0, up_px=round(10 * 1.1, 2), seal_vol=seal, name=s.name)
html_events: list = []


# ---------------- 渲染 / 存储 / 安装 ----------------
def colorize(text, color):
    codes = {"green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
             "cyan": "\033[36m", "bright": "\033[1m", "dim": "\033[2m"}
    return f"{codes.get(color, '')}{text}\033[0m" if color else text


def init_db():
    with sqlite3.connect(INTRADAY_DB) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS signals(
            date TEXT, ts INTEGER, code TEXT, name TEXT, signal TEXT,
            detail TEXT, advice TEXT, boards TEXT,
            PRIMARY KEY(date, ts, code, signal));
        CREATE TABLE IF NOT EXISTS snaps(
            date TEXT, ts INTEGER, code TEXT, last_px REAL, change_rate REAL,
            entrust_rate REAL, vol_ratio REAL, avg_px REAL, sealed INTEGER, seal_vol REAL);
        CREATE INDEX IF NOT EXISTS idx_snaps ON snaps(date, code, ts);
        """)


def render_html(day, rows, events, cycle_line=""):
    now = datetime.now().strftime("%H:%M:%S")
    ev = "".join(
        f"<div class='ev {e['color']}'><b>{e['t']} [{e['sig']}]</b> "
        f"{e['name'] or e['code']} <span class='tag'>{e['tags']}</span><br>"
        f"<small>{e['detail']}</small><br><span class='adv'>{e['advice']}</span></div>"
        for e in reversed(events)) or "<p class='dim'>暂无信号（每 15s 自动刷新）</p>"
    tr_parts = []
    for r in rows:
        chg = r["chg"]
        sealed = r["sealed"]
        if sealed:
            entrust_cell = "<span class='up'>封板</span>"
        elif r["entrust"] is not None:
            entrust_cell = f"{r['entrust']:.0f}"
        else:
            entrust_cell = "-"
        cls = "up" if (chg or 0) > 0 else "dn"
        st = r.get("status", "")
        st_cls = "up" if st.startswith("可做") else ("dim" if st == "禁碰" else "")
        tr_parts.append(
            f"<tr><td>{r['code']}</td><td>{r['name']}</td><td class='tag'>{r['tags']}</td>"
            f"<td class='{cls}'>{r['px'] or '-'} ({(chg or 0):+.2f}%)</td>"
            f"<td>{entrust_cell}</td><td class='{st_cls}'>{st}</td></tr>")
    tr = "".join(tr_parts)
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="15"><title>盘中监控 {day}</title>
<style>
body{{font-family:-apple-system,PingFangSC,sans-serif;background:#111;color:#ddd;margin:16px}}
h2{{color:#fff;margin:8px 0}} .dim{{color:#666}} .up{{color:#ff5a5a}} .dn{{color:#4cd964}}
table{{border-collapse:collapse;width:100%;margin:8px 0}}
td,th{{border:1px solid #333;padding:4px 8px;font-size:13px}}
.tag{{color:#7ab8ff;font-size:12px}}
.cycle{{background:#1b1b1b;border:1px solid #444;border-radius:6px;padding:10px 14px;margin:10px 0;line-height:1.7}}
.cycle b{{color:#f5c542}}
.ev{{border-left:4px solid #444;padding:6px 10px;margin:6px 0;background:#1b1b1b;border-radius:4px}}
.ev.green{{border-color:#4cd964}} .ev.red{{border-color:#ff5a5a}} .ev.yellow{{border-color:#f5a623}}
.ev.cyan{{border-color:#4cb9ff}} .ev.bright{{border-color:#fff}}
.adv{{color:#f5c542;font-size:13px}}
</style></head><body>
<h2>📡 盘中实时监控 · {day} · 更新 {now}</h2>
<div class="cycle">{cycle_line or '周期引擎未接入：仅异动信号'}</div>
<h2>最新信号流</h2>{ev}
<h2>Watchlist 快照（按角色排序）</h2>
<table><tr><th>代码</th><th>名称</th><th>标签/角色</th><th>现价(涨幅)</th><th>委比</th><th>状态</th></tr>{tr}</table>
</body></html>"""


def install_launchd():
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    label = "com.stockboard.intraday-monitor"
    py = str(ROOT / "venv" / "bin" / "python")
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{py}</string><string>{Path(__file__).resolve()}</string>
  </array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>StartCalendarInterval</key><array>
    {''.join(f"<dict><key>Weekday</key><integer>{d}</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>26</integer></dict>" for d in range(1, 6))}
  </array>
  <key>StandardOutPath</key><string>{ROOT / 'logs' / 'intraday.launchd.log'}</string>
  <key>StandardErrorPath</key><string>{ROOT / 'logs' / 'intraday.launchd.log'}</string>
  <key>EnvironmentVariables</key><dict>
    <key>NO_PROXY</key><string>longhuvip.com</string>
    <key>no_proxy</key><string>longhuvip.com</string>
  </dict>
</dict></plist>"""
    p = plist_dir / f"{label}.plist"
    p.write_text(plist)
    (ROOT / "logs").mkdir(exist_ok=True)
    import subprocess
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    subprocess.run(["launchctl", "load", str(p)], check=True)
    print(f"✅ LaunchAgent 已安装: {p}\n   交易日(周一~周五) 09:26 自动启动盘中监控, 15:10 自动收工\n"
          f"   卸载: launchctl unload {p}")


def main():
    ap = argparse.ArgumentParser(description="盘中实时监控")
    ap.add_argument("--date", help="数据日期(默认取 auction.json)")
    ap.add_argument("--once", action="store_true", help="单轮快照测试")
    ap.add_argument("--simulate", action="store_true", help="合成行情自测信号引擎")
    ap.add_argument("--interval", type=int, default=60, help="轮询间隔秒(默认60)")
    ap.add_argument("--max", type=int, default=30, help="watchlist 上限(默认30)")
    ap.add_argument("--extra", help="额外关注代码, 逗号分隔")
    ap.add_argument("--install-launchd", action="store_true", help="安装 LaunchAgent")
    args = ap.parse_args()
    if args.install_launchd:
        return install_launchd()
    m = Monitor(args)
    if args.once:
        return m.run_once()
    if args.simulate:
        return m.run_simulate()
    return m.run()


if __name__ == "__main__":
    sys.exit(main() or 0)
