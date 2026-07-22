"""
可视化数据看板 — 读取 SQLite 数据，生成交互式 HTML 报告并在浏览器中打开

用法:
    source venv/bin/activate && python scripts/dashboard.py
    source venv/bin/activate && python scripts/dashboard.py --date 2026-07-22
"""
import sqlite3
import json
import sys
import webbrowser
import argparse
from pathlib import Path
from datetime import date
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "crawl_data.db"
OUTPUT_PATH = ROOT / "data" / "dashboard.html"


def load_data(db_path: Path, crawl_date: str | None = None):
    """从 SQLite 加载所有可视化所需数据"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    players = [dict(r) for r in cur.execute(
        "SELECT * FROM players ORDER BY total_return DESC"
    ).fetchall()]

    # 解析 JSON 字符串字段
    for p in players:
        for key in ("labels", "ranks"):
            if isinstance(p.get(key), str):
                try:
                    p[key] = json.loads(p[key])
                except (json.JSONDecodeError, TypeError):
                    p[key] = []

    # 置顶关注的选手 ID 列表
    WATCHED_IDS = ["900240956"]  # 股得猫咛

    if crawl_date:
        where_pos, where_trade = "WHERE p.crawl_date = ?", "WHERE t.crawl_date = ?"
        params = (crawl_date,)
    else:
        latest = cur.execute("SELECT MAX(crawl_date) FROM positions WHERE crawl_date != ''").fetchone()[0]
        crawl_date = latest or date.today().isoformat()
        where_pos, where_trade = "WHERE p.crawl_date = ?", "WHERE t.crawl_date = ?"
        params = (crawl_date,)

    positions = [dict(r) for r in cur.execute(f"""
        SELECT p.*, pl.name as player_name, pl.total_return as player_return
        FROM positions p LEFT JOIN players pl ON p.zh_id = pl.zh_id
        {where_pos}""", params).fetchall()]

    trades = [dict(r) for r in cur.execute(f"""
        SELECT t.*, pl.name as player_name
        FROM trades t LEFT JOIN players pl ON t.zh_id = pl.zh_id
        {where_trade} ORDER BY t.trade_date DESC""", params).fetchall()]

    # 按选手分组
    player_positions = defaultdict(list)
    player_trades = defaultdict(list)
    for p in positions:
        player_positions[p["zh_id"]].append(p)
    for t in trades:
        player_trades[t["zh_id"]].append(t)

    for p in players:
        zh = p["zh_id"]
        p["_positions"] = player_positions.get(zh, [])
        p["_trades"] = player_trades.get(zh, [])
        p["_total_position"] = sum(
            (pos.get("position_ratio") or 0) for pos in p["_positions"]
        )

    # 股票统计 — 按加权仓位（总仓位）排序，重仓比持有多更有意义
    stock_stats = {}
    for p in positions:
        code = p.get("stock_code", "")
        if not code:
            continue
        if code not in stock_stats:
            stock_stats[code] = {"code": code, "name": p.get("stock_name", ""), "holders": 0, "total_position": 0.0, "total_profit": 0.0, "count": 0}
        stock_stats[code]["holders"] += 1
        stock_stats[code]["total_position"] += p.get("position_ratio") or 0
        stock_stats[code]["total_profit"] += p.get("profit_ratio") or 0
        stock_stats[code]["count"] += 1
    # 按加权总仓位降序（而非简单持有人数）
    stock_stats_list = sorted(stock_stats.values(), key=lambda x: x["total_position"], reverse=True)

    # 仓位分布
    dist = {"空仓": 0, "1成以下": 0, "1-3成": 0, "3-5成": 0, "5-7成": 0, "7-9成": 0, "9成以上": 0}
    for p in players:
        total = p["_total_position"]
        if total == 0:         dist["空仓"] += 1
        elif total < 10:       dist["1成以下"] += 1
        elif total < 30:       dist["1-3成"] += 1
        elif total < 50:       dist["3-5成"] += 1
        elif total < 70:       dist["5-7成"] += 1
        elif total < 90:       dist["7-9成"] += 1
        else:                  dist["9成以上"] += 1

    # 调仓共识：按股票聚合，统计买入/卖出人数
    trade_consensus = {}
    for t in trades:
        code = t.get("stock_code", "")
        if not code:
            continue
        if code not in trade_consensus:
            trade_consensus[code] = {
                "code": code, "name": t.get("stock_name", ""),
                "buy_count": 0, "sell_count": 0,
                "buy_players": [], "sell_players": [],
            }
        direction = t.get("direction", "")
        if direction == "买入":
            trade_consensus[code]["buy_count"] += t.get("trades_count", 1) or 1
            if t.get("player_name") not in trade_consensus[code]["buy_players"]:
                trade_consensus[code]["buy_players"].append(t.get("player_name", ""))
        elif direction == "卖出":
            trade_consensus[code]["sell_count"] += t.get("trades_count", 1) or 1
            if t.get("player_name") not in trade_consensus[code]["sell_players"]:
                trade_consensus[code]["sell_players"].append(t.get("player_name", ""))
    # 按总交易热度降序
    trade_consensus_list = sorted(
        trade_consensus.values(),
        key=lambda x: len(x["buy_players"]) + len(x["sell_players"]),
        reverse=True
    )

    conn.close()
    return {
        "crawl_date": crawl_date,
        "player_count": len(players),
        "position_count": len(positions),
        "trade_count": len(trades),
        "players": players,
        "positions": positions,
        "trades": trades,
        "stock_stats": stock_stats_list,
        "position_distribution": dist,
        "watched_ids": WATCHED_IDS,
        "trade_consensus": trade_consensus_list,
    }


def generate_html(data: dict) -> str:
    """通过字符串模板生成 HTML，避免 f-string 中大括号转义问题"""
    # 将 JSON 数据嵌入为 <script> 标签
    EMBED = {
        "PLAYERS": json.dumps(data["players"], ensure_ascii=False),
        "STOCK_STATS": json.dumps(data["stock_stats"], ensure_ascii=False),
        "TRADES": json.dumps(data["trades"], ensure_ascii=False),
        "POSITIONS": json.dumps(data["positions"], ensure_ascii=False),
        "DIST": json.dumps(data["position_distribution"], ensure_ascii=False),
        "WATCHED_IDS": json.dumps(data["watched_ids"], ensure_ascii=False),
        "TRADE_CONSENSUS": json.dumps(data["trade_consensus"], ensure_ascii=False),
        "CRAWL_DATE": data["crawl_date"],
        "PLAYER_COUNT": str(data["player_count"]),
        "POSITION_COUNT": str(data["position_count"]),
        "TRADE_COUNT": str(data["trade_count"]),
        "STOCK_COUNT": str(len(data["stock_stats"])),
    }

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>数据看板 — __CRAWL_DATE__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f0f2f5; color: #1a1a1a; }
.header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); color: white; padding: 28px 40px; }
.header h1 { font-size: 26px; font-weight: 700; margin-bottom: 6px; }
.header .subtitle { opacity: 0.7; font-size: 13px; }
.stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; padding: 24px 40px; margin-top: -20px; }
.stat-card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); text-align: center; }
.stat-card .value { font-size: 32px; font-weight: 700; }
.stat-card .label { font-size: 12px; color: #888; margin-top: 2px; }
.stat-card.c1 .value { color: #2980b9; } .stat-card.c2 .value { color: #e74c3c; }
.stat-card.c3 .value { color: #27ae60; } .stat-card.c4 .value { color: #8e44ad; }
.container { max-width: 1400px; margin: 0 auto; padding: 0 40px 40px; }
.tabs { display: flex; margin-bottom: 20px; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.tab { padding: 12px 24px; cursor: pointer; font-size: 13px; font-weight: 500; color: #666; border: none; background: white; transition: all 0.2s; }
.tab:hover { color: #2980b9; }
.tab.active { color: white; background: #2980b9; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
.card { background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.card h2 { font-size: 17px; margin-bottom: 16px; color: #333; border-bottom: 2px solid #f0f0f0; padding-bottom: 12px; }
.card h2 .badge { font-size: 11px; background: #e8f4fd; color: #2980b9; padding: 2px 8px; border-radius: 10px; margin-left: 8px; font-weight: 400; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 9px 10px; background: #f8f9fa; color: #666; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.3px; border-bottom: 2px solid #e9ecef; }
td { padding: 9px 10px; border-bottom: 1px solid #f0f0f0; }
tr:hover td { background: #f8f9ff; }
tr.clickable { cursor: pointer; }
tr.clickable:hover td { background: #e8f4fd; }
.positive { color: #e74c3c; font-weight: 600; }
.negative { color: #27ae60; font-weight: 600; }
.buy { color: #e74c3c; font-weight: 600; }
.sell { color: #27ae60; font-weight: 600; }
.progress-bar { width: 70px; height: 5px; background: #eee; border-radius: 3px; display: inline-block; vertical-align: middle; margin-right: 6px; }
.progress-bar .fill { height: 5px; border-radius: 3px; background: #2980b9; }
.search-box { margin-bottom: 14px; }
.search-box input { width: 100%; padding: 9px 14px; border: 1px solid #ddd; border-radius: 8px; font-size: 13px; outline: none; transition: border 0.2s; }
.search-box input:focus { border-color: #2980b9; }
.chart-wrap { position: relative; height: 280px; }
.chart-wrap.tall { height: 360px; }
.footer { text-align: center; padding: 24px; color: #999; font-size: 12px; }
.player-detail { display: none; }
.player-detail.active { display: block; }
.player-meta { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 20px; }
.player-meta-item { background: #f8f9fa; border-radius: 8px; padding: 14px; text-align: center; }
.player-meta-item .val { font-size: 20px; font-weight: 700; }
.player-meta-item .lbl { font-size: 11px; color: #888; margin-top: 2px; }
.back-btn { display: inline-block; padding: 8px 20px; background: #2980b9; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; margin-bottom: 16px; }
.back-btn:hover { background: #2471a3; }
.empty-state { text-align: center; padding: 40px; color: #999; }
.filter-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
.filter-row label { font-size: 12px; color: #666; white-space: nowrap; }
.filter-row select, .filter-row input { padding: 5px 8px; border: 1px solid #ddd; border-radius: 6px; font-size: 12px; outline: none; }
.filter-row .filter-badge { font-size: 11px; padding: 3px 10px; border-radius: 10px; cursor: pointer; border: 1px solid #ddd; background: white; color: #666; white-space: nowrap; }
.filter-row .filter-badge.active { background: #2980b9; color: white; border-color: #2980b9; }
.consensus-card { display: inline-block; background: #f8f9fa; border-radius: 10px; padding: 14px 18px; margin: 0 12px 12px 0; min-width: 240px; vertical-align: top; }
.consensus-card h3 { font-size: 14px; margin-bottom: 6px; color: #333; }
.consensus-card .code { font-size: 11px; color: #999; margin-bottom: 8px; }
.consensus-card .bar { display: flex; gap: 8px; font-size: 12px; margin-bottom: 4px; }
.consensus-card .bar .buy-bar { color: #e74c3c; }
.consensus-card .bar .sell-bar { color: #27ae60; }
.consensus-card .players { font-size: 11px; color: #888; margin-top: 6px; line-height: 1.5; }

</style>
</head>
<body>

<div class="header">
    <h1>📊 数据看板</h1>
    <div class="subtitle">更新日期: __CRAWL_DATE__ · 数据来源: 东方财富</div>
</div>

<div class="stats-row">
    <div class="stat-card c1"><div class="value">__PLAYER_COUNT__</div><div class="label">选手总数</div></div>
    <div class="stat-card c2"><div class="value">__POSITION_COUNT__</div><div class="label">持仓记录</div></div>
    <div class="stat-card c3"><div class="value">__TRADE_COUNT__</div><div class="label">调仓记录</div></div>
    <div class="stat-card c4"><div class="value">__STOCK_COUNT__</div><div class="label">涉及股票</div></div>
</div>

<div class="container">
    <div class="tabs" id="mainTabs">
        <button class="tab active" onclick="switchTab('overview')">📋 总览</button>
        <button class="tab" onclick="switchTab('rankings')">🏆 选手排行</button>
        <button class="tab" onclick="switchTab('stocks')">📈 股票分析</button>
        <button class="tab" onclick="switchTab('trades')">🤝 调仓共识</button>
    </div>

    <div id="tab-overview" class="tab-content active">
        <div class="grid-2">
            <div class="card"><h2>选手仓位分布</h2><div class="chart-wrap"><canvas id="chartPositionDist"></canvas></div></div>
            <div class="card"><h2>日收益 Top 10</h2><div id="dailyTopList"></div></div>
        </div>
        <div class="grid-2">
            <div class="card"><h2>股票盈亏分布</h2><div class="chart-wrap"><canvas id="chartProfitDist"></canvas></div></div>
            <div class="card"><h2>总收益 Top 10</h2><div id="totalTopList"></div></div>
        </div>
    </div>

    <div id="tab-rankings" class="tab-content">
        <div class="card">
            <div class="search-box"><input type="text" id="playerSearch" placeholder="🔍 搜索选手名称或ID... 点击行可查看详情" oninput="filterPlayers()"></div>
            <div class="filter-row">
                <span style="font-size:12px;color:#888;">排序:</span>
                <button class="filter-badge active" onclick="setSortKey('total_return', this)">总收益</button>
                <button class="filter-badge" onclick="setSortKey('yearly_return', this)">年收益</button>
                <button class="filter-badge" onclick="setSortKey('monthly_return', this)">月收益</button>
                <button class="filter-badge" onclick="setSortKey('weekly_return', this)">周收益</button>
                <button class="filter-badge" onclick="setSortKey('daily_return', this)">日收益</button>
                <button class="filter-badge" onclick="setSortKey('net_value', this)">净值</button>
                <button class="filter-badge" onclick="setSortKey('followers', this)">关注</button>
                <span style="margin-left:12px;font-size:12px;color:#888;">质量:</span>
                <button class="filter-badge" id="qualityBtn" onclick="toggleQuality(this)">高质量</button>
                <span style="font-size:11px;color:#aaa;">(≥200天 · ≤30%回撤)</span>
                <span id="filteredCount" style="font-size:11px;color:#888;margin-left:auto;"></span>
            </div>
            <div style="max-height:600px; overflow-y:auto;">
                <table><thead><tr>
                    <th>#</th><th>选手</th><th>ID</th><th>总收益</th><th>年收益</th><th>月收益</th><th>周收益</th><th>日收益</th><th>净值</th><th>回撤</th><th>仓位</th><th>运行</th>
                </tr></thead><tbody id="playerTableBody"></tbody></table>
            </div>
        </div>
    </div>

    <div id="tab-player-detail" class="player-detail">
        <button class="back-btn" onclick="backToList()">← 返回选手列表</button>
        <div class="player-meta" id="playerMeta"></div>
        <div class="grid-2">
            <div class="card">
                <h2>📦 当前持仓</h2>
                <div id="playerPositions"></div>
            </div>
            <div class="card">
                <h2>🔄 调仓记录</h2>
                <div id="playerTrades"></div>
            </div>
        </div>
    </div>

    <div id="tab-stocks" class="tab-content">
        <div class="grid-2">
            <div class="card">
                <h2>重仓共识 <span class="badge">Top 20</span></h2>
                <p style="font-size:11px;color:#888;margin-bottom:10px;">按加权总仓位排序（仓位比例高 > 持有者多），反映选手真金白银的重仓方向</p>
                <div style="max-height:500px; overflow-y:auto;">
                    <table><thead><tr><th>#</th><th>股票</th><th>代码</th><th>持有人</th><th>总仓位</th><th>平均盈亏</th></tr></thead>
                    <tbody id="stockTableBody"></tbody></table>
                </div>
            </div>
            <div class="card">
                <h2>重仓共识图表</h2>
                <div class="chart-wrap tall"><canvas id="chartStocks"></canvas></div>
            </div>
        </div>
    </div>

    <div id="tab-trades" class="tab-content">
        <div class="card">
            <h2>今日调仓共识 <span class="badge" id="consensusCount"></span></h2>
            <p style="font-size:12px;color:#888;margin-bottom:14px;">同一只股票被多个选手买入/卖出 = 共识信号。点击选手名可查看详情。</p>
            <div class="search-box"><input type="text" id="tradeSearch" placeholder="🔍 搜索股票名..." oninput="filterTrades()"></div>
            <div id="tradeConsensusArea" style="max-height:600px; overflow-y:auto;"></div>
            <div style="margin-top:16px; border-top:1px solid #f0f0f0; padding-top:16px;">
                <details>
                    <summary style="cursor:pointer;font-size:13px;color:#888;">📋 查看全部调仓明细</summary>
                    <div style="max-height:400px; overflow-y:auto; margin-top:10px;">
                        <table><thead><tr><th>日期</th><th>选手</th><th>方向</th><th>股票</th><th>代码</th><th>笔数</th></tr></thead>
                        <tbody id="tradeTableBody"></tbody></table>
                    </div>
                </details>
            </div>
        </div>
    </div>
</div>

<div class="footer">数据看板 · __CRAWL_DATE__</div>

<script>
var PLAYERS = __PLAYERS__;
var STOCK_STATS = __STOCK_STATS__;
var TRADES = __TRADES__;
var POSITIONS = __POSITIONS__;
var DIST = __DIST__;
var WATCHED_IDS = __WATCHED_IDS__;
var TRADE_CONSENSUS = __TRADE_CONSENSUS__;

function switchTab(name) {
    document.querySelectorAll('#mainTabs .tab').forEach(function(t) { t.classList.remove('active'); });
    var tabBtn = document.querySelector('#mainTabs .tab[onclick*="' + name + '"]');
    if (tabBtn) tabBtn.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    var panel = document.getElementById('tab-' + name);
    if (panel) panel.classList.add('active');
    document.querySelectorAll('.player-detail').forEach(function(c) { c.classList.remove('active'); });
    if (name === 'rankings' && !document.getElementById('playerTableBody').innerHTML) renderPlayers();
    if (name === 'stocks' && !document.getElementById('stockTableBody').innerHTML) renderStocks();
    if (name === 'trades' && !document.getElementById('tradeConsensusArea').innerHTML) renderTrades();
    if (name === 'overview') initCharts();
}

function pct(v) {
    var n = parseFloat(v);
    if (isNaN(n)) return '—';
    return n >= 0 ? '<span class="positive">+' + n.toFixed(2) + '%</span>' : '<span class="negative">' + n.toFixed(2) + '%</span>';
}
function pctPlain(v) {
    var n = parseFloat(v);
    if (isNaN(n)) return '—';
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}
function posLabel(total) {
    if (total === 0) return '空仓'; if (total < 10) return '1成以下'; if (total < 30) return '1-3成';
    if (total < 50) return '3-5成'; if (total < 70) return '5-7成'; if (total < 90) return '7-9成';
    return '9成以上';
}

// === 总览: Top 榜单 ===
(function() {
    var ds = PLAYERS.slice().sort(function(a,b) { return (b.daily_return||0) - (a.daily_return||0); }).slice(0,10);
    var html = `<table><thead><tr><th>#</th><th>选手</th><th>日收益</th><th>总收益</th></tr></thead><tbody>`;
    ds.forEach(function(p, i) {
        html += `<tr><td>${i+1}</td><td><a href="javascript:showPlayer('${p.zh_id}')" style="color:#2980b9;text-decoration:none;">${p.name||p.zh_id}</a></td><td>${pct(p.daily_return)}</td><td>${pct(p.total_return)}</td></tr>`;
    });
    html += `</tbody></table>`;
    document.getElementById('dailyTopList').innerHTML = html;

    var ts = PLAYERS.slice().sort(function(a,b) { return (b.total_return||0) - (a.total_return||0); }).slice(0,10);
    html = `<table><thead><tr><th>#</th><th>选手</th><th>总收益</th><th>日收益</th><th>净值</th></tr></thead><tbody>`;
    ts.forEach(function(p, i) {
        html += `<tr><td>${i+1}</td><td><a href="javascript:showPlayer('${p.zh_id}')" style="color:#2980b9;text-decoration:none;">${p.name||p.zh_id}</a></td><td>${pct(p.total_return)}</td><td>${pct(p.daily_return)}</td><td>${(p.net_value||0).toFixed(3)}</td></tr>`;
    });
    html += `</tbody></table>`;
    document.getElementById('totalTopList').innerHTML = html;
})();

// === 选手排行 ===
var sortKey = 'total_return';
var qualityOn = false;

function isQualityPlayer(p) {
    return (p.days||0) >= 200 && (p.max_drawdown||0) <= 30;
}

function setSortKey(key, btn) {
    sortKey = key;
    document.querySelectorAll('.filter-row .filter-badge').forEach(function(b) { b.classList.remove('active'); });
    if (btn) btn.classList.add('active');
    if (qualityOn) document.getElementById('qualityBtn').classList.add('active');
    renderPlayers();
    filterPlayers();
}

function toggleQuality(btn) {
    qualityOn = !qualityOn;
    if (qualityOn) { btn.classList.add('active'); }
    else { btn.classList.remove('active'); }
    renderPlayers();
    filterPlayers();
}

function renderPlayers() {
    var sorted = PLAYERS.slice().sort(function(a,b) { return (b[sortKey]||0) - (a[sortKey]||0); });

    // 先分离置顶选手（不受质量筛选影响）
    var watchedSet = new Set(WATCHED_IDS);
    var pinned = sorted.filter(function(p) { return watchedSet.has(p.zh_id); });
    var rest = sorted.filter(function(p) { return !watchedSet.has(p.zh_id); });

    // 质量筛选只作用于普通选手
    if (qualityOn) {
        rest = rest.filter(isQualityPlayer);
    }

    // 置顶选手的真实排名（从全量排序中获取）
    var rankMap = {};
    sorted.forEach(function(p, i) { rankMap[p.zh_id] = i + 1; });

    var html = '';
    pinned.forEach(function(p) {
        html += rowHTML(p, rankMap[p.zh_id] || 1, true);
    });
    rest.forEach(function(p, i) {
        html += rowHTML(p, rankMap[p.zh_id] || (pinned.length + i + 1), false);
    });
    document.getElementById('playerTableBody').innerHTML = html;
    document.getElementById('filteredCount').textContent = '显示 ' + sorted.length + ' 人';
}

function rowHTML(p, rank, isPinned) {
    var star = isPinned ? ' ⭐' : '';
    var badge = isQualityPlayer(p) ? ' 🏅' : '';
    var bg = isPinned ? ' style="background:#fef9e7;"' : '';
    var rankTags = (p.ranks && p.ranks.length > 0) ? ' <span style="font-size:10px;color:#888;">(' + p.ranks.length + '榜)</span>' : '';
    return `<tr class="clickable"${bg} onclick="showPlayer('${p.zh_id}')">` +
        `<td>${rank}</td>` +
        `<td><strong style="color:#2980b9;">${p.name||p.zh_id}${star}${badge}</strong></td>` +
        `<td style="color:#999;font-size:11px;">${p.zh_id}${rankTags}</td>` +
        `<td>${pct(p.total_return)}</td>` +
        `<td>${pct(p.yearly_return)}</td>` +
        `<td>${pct(p.monthly_return)}</td>` +
        `<td>${pct(p.weekly_return)}</td>` +
        `<td>${pct(p.daily_return)}</td>` +
        `<td>${(p.net_value||0).toFixed(3)}</td>` +
        `<td>${(p.max_drawdown||0).toFixed(1)}%</td>` +
        `<td>${posLabel(p._total_position||0)}</td>` +
        `<td>${p.days||0}天</td>` +
        `</tr>`;
}

function filterPlayers() {
    var q = document.getElementById('playerSearch').value.toLowerCase();
    document.querySelectorAll('#playerTableBody tr').forEach(function(r) {
        r.style.display = r.textContent.toLowerCase().indexOf(q) >= 0 ? '' : 'none';
    });
}

// === 选手详情 ===
function showPlayer(zhId) {
    var p = PLAYERS.find(function(x) { return x.zh_id === zhId; });
    if (!p) return;

    document.querySelectorAll('#mainTabs .tab').forEach(function(t) { t.classList.remove('active'); });
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    document.getElementById('tab-player-detail').classList.add('active');

    var dr = p.daily_return||0, tr = p.total_return||0;

    document.getElementById('playerMeta').innerHTML =
        '<div class="player-meta-item"><div class="val" style="color:#2980b9;">' + (p.name||p.zh_id) + '</div><div class="lbl">选手</div></div>' +
        '<div class="player-meta-item"><div class="val" style="color:' + (tr>=0?'#e74c3c':'#27ae60') + '">' + pctPlain(tr) + '</div><div class="lbl">总收益</div></div>' +
        '<div class="player-meta-item"><div class="val" style="color:' + (dr>=0?'#e74c3c':'#27ae60') + '">' + pctPlain(dr) + '</div><div class="lbl">日收益</div></div>' +
        '<div class="player-meta-item"><div class="val">' + (p.net_value||0).toFixed(3) + '</div><div class="lbl">净值</div></div>' +
        '<div class="player-meta-item"><div class="val">' + (p.max_drawdown||0).toFixed(1) + '%</div><div class="lbl">最大回撤</div></div>' +
        '<div class="player-meta-item"><div class="val">' + posLabel(p._total_position||0) + '</div><div class="lbl">当前仓位</div></div>' +
        '<div class="player-meta-item"><div class="val">' + (p.win_rate||0).toFixed(1) + '%</div><div class="lbl">胜率</div></div>' +
        '<div class="player-meta-item"><div class="val">' + (p.days||0) + '天</div><div class="lbl">运行天数</div></div>' +
        '<div class="player-meta-item"><div class="val">' + ((p.followers||0).toLocaleString()) + '</div><div class="lbl">关注人数</div></div>' +
        '<div class="player-meta-item" style="grid-column:span 3;"><div style="font-size:13px;color:#666;text-align:left;">' + (p.intro||p.concept||'暂无简介') + '</div><div class="lbl">简介</div></div>' +
        (p.manager_name ? '<div class="player-meta-item"><div class="val" style="font-size:16px;">' + p.manager_name + '</div><div class="lbl">管理人</div></div>' : '') +
        (p.ranks && p.ranks.length > 0 ? '<div class="player-meta-item" style="grid-column:span 3;"><div style="font-size:12px;color:#666;text-align:left;">上榜: ' + p.ranks.map(function(r) { return '<span style="background:#e8f4fd;color:#2980b9;padding:1px 6px;border-radius:8px;margin:0 2px;font-size:11px;">' + r + '</span>'; }).join(' ') + '</div><div class="lbl">榜单</div></div>' : '');

    // 持仓
    var positions = p._positions || [];
    var posHtml = '';
    if (positions.length === 0) {
        posHtml = '<div class="empty-state">📭 暂无持仓数据</div>';
    } else {
        posHtml = '<table><thead><tr><th>股票</th><th>代码</th><th>成本价</th><th>现价</th><th>盈亏</th><th>仓位</th></tr></thead><tbody>';
        positions.forEach(function(x) {
            posHtml += `<tr>` +
                `<td><strong>${x.stock_name||''}</strong></td>` +
                `<td style="color:#999;">${x.stock_code||''}</td>` +
                `<td>${(x.cost_price||0).toFixed(3)}</td>` +
                `<td>${(x.current_price||0).toFixed(3)}</td>` +
                `<td>${pct(x.profit_ratio)}</td>` +
                `<td><span class="progress-bar"><span class="fill" style="width:${Math.min(100, (x.position_ratio||0))}%"></span></span>${(x.position_ratio||0).toFixed(1)}%</td>` +
                `</tr>`;
        });
        posHtml += '</tbody></table>';
    }
    document.getElementById('playerPositions').innerHTML = posHtml;

    // 调仓
    var trades = p._trades || [];
    var tradeHtml = '';
    if (trades.length === 0) {
        tradeHtml = '<div class="empty-state">📭 暂无调仓记录</div>';
    } else {
        tradeHtml = '<table><thead><tr><th>日期</th><th>方向</th><th>股票</th><th>代码</th><th>笔数</th><th>仓位</th></tr></thead><tbody>';
        trades.forEach(function(x) {
            tradeHtml += `<tr>` +
                `<td>${x.trade_date||''}</td>` +
                `<td>${x.direction === '买入' ? '<span class="buy">买入</span>' : '<span class="sell">卖出</span>'}</td>` +
                `<td><strong>${x.stock_name||''}</strong></td>` +
                `<td style="color:#999;">${x.stock_code||''}</td>` +
                `<td>${x.trades_count||1}笔</td>` +
                `<td>${x.position_ratio||''}</td>` +
                `</tr>`;
        });
        tradeHtml += '</tbody></table>';
    }
    document.getElementById('playerTrades').innerHTML = tradeHtml;

    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function backToList() {
    switchTab('rankings');
    setTimeout(function() { renderPlayers(); }, 50);
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// === 股票分析 ===
function renderStocks() {
    var html = '';
    STOCK_STATS.slice(0,20).forEach(function(s, i) {
        html += `<tr>` +
            `<td>${i+1}</td>` +
            `<td><strong>${s.name}</strong></td>` +
            `<td style="color:#999;">${s.code}</td>` +
            `<td>${s.holders}人</td>` +
            `<td><span class="progress-bar"><span class="fill" style="width:${Math.min(100, s.total_position/s.count)}%"></span></span>${s.total_position.toFixed(0)}%</td>` +
            `<td>${pct(s.total_profit/s.count)}</td>` +
            `</tr>`;
    });
    document.getElementById('stockTableBody').innerHTML = html;
}

// === 调仓共识 ===
function renderTrades() {
    // 共识卡片
    var html = '';
    TRADE_CONSENSUS.forEach(function(c) {
        var total = c.buy_players.length + c.sell_players.length;
        if (total < 1) return;
        var signal = total >= 5 ? '🔥' : total >= 3 ? '📈' : '';
        html += '<div class="consensus-card">' +
            '<h3>' + signal + ' ' + c.name + '</h3>' +
            '<div class="code">' + c.code + ' · ' + total + '人交易</div>' +
            (c.buy_players.length > 0 ? '<div class="bar"><span class="buy-bar">🟢 买入 ' + c.buy_players.length + '人</span></div>' +
            '<div class="players">' + c.buy_players.join('、') + '</div>' : '') +
            (c.sell_players.length > 0 ? '<div class="bar" style="margin-top:4px;"><span class="sell-bar">🔴 卖出 ' + c.sell_players.length + '人</span></div>' +
            '<div class="players">' + c.sell_players.join('、') + '</div>' : '') +
            '</div>';
    });
    document.getElementById('tradeConsensusArea').innerHTML = html || '<div class="empty-state">📭 今日暂无调仓记录</div>';
    document.getElementById('consensusCount').textContent = TRADE_CONSENSUS.length + '只股票';

    // 明细表
    var detailHtml = '';
    TRADES.forEach(function(t) {
        detailHtml += `<tr>` +
            `<td>${t.trade_date||''}</td>` +
            `<td style="cursor:pointer;color:#2980b9;" onclick="showPlayer('${t.zh_id}')">${t.player_name||t.zh_id}</td>` +
            `<td>${t.direction === '买入' ? '<span class="buy">买入</span>' : '<span class="sell">卖出</span>'}</td>` +
            `<td><strong>${t.stock_name||''}</strong></td>` +
            `<td style="color:#999;">${t.stock_code||''}</td>` +
            `<td>${t.trades_count||1}笔</td>` +
            `</tr>`;
    });
    document.getElementById('tradeTableBody').innerHTML = detailHtml;
}

function filterTrades() {
    var q = document.getElementById('tradeSearch').value.toLowerCase();
    document.querySelectorAll('#tradeConsensusArea .consensus-card').forEach(function(c) {
        c.style.display = c.textContent.toLowerCase().indexOf(q) >= 0 ? '' : 'none';
    });
}

// === 图表 ===
var chartsInit = false;
function initCharts() {
    if (chartsInit) return; chartsInit = true;

    var distLabels = ['9成以上','7-9成','5-7成','3-5成','1-3成','1成以下','空仓'];
    var distColors = ['#e74c3c','#e67e22','#f39c12','#f1c40f','#2ecc71','#3498db','#95a5a6'];
    new Chart(document.getElementById('chartPositionDist'), {
        type: 'doughnut',
        data: { labels: distLabels, datasets: [{ data: distLabels.map(function(l) { return DIST[l]||0; }), backgroundColor: distColors }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: {
                        generateLabels: function(chart) {
                            var total = chart.data.datasets[0].data.reduce(function(a,b) { return a+b; }, 0);
                            return chart.data.labels.map(function(label, i) {
                                var v = chart.data.datasets[0].data[i];
                                var pct = total > 0 ? ((v/total)*100).toFixed(1) + '%' : '0%';
                                return { text: label + '  ' + pct, fillStyle: chart.data.datasets[0].backgroundColor[i], index: i, strokeStyle: '#fff' };
                            });
                        }
                    }
                }
            }
        }
    });

    var bins = {'<-10%':0,'-10~0%':0,'0~10%':0,'10~20%':0,'>20%':0};
    POSITIONS.forEach(function(p) {
        var r = p.profit_ratio||0;
        if (r < -10) bins['<-10%']++;
        else if (r < 0) bins['-10~0%']++;
        else if (r < 10) bins['0~10%']++;
        else if (r < 20) bins['10~20%']++;
        else bins['>20%']++;
    });
    new Chart(document.getElementById('chartProfitDist'), {
        type: 'bar',
        data: { labels: Object.keys(bins), datasets: [{ label: '股票数量', data: Object.values(bins), backgroundColor: '#2980b9', borderRadius: 6 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
    });

    var top10 = STOCK_STATS.slice(0,10);
    new Chart(document.getElementById('chartStocks'), {
        type: 'bar',
        data: { labels: top10.map(function(s) { return s.name; }), datasets: [{ label: '持有人数', data: top10.map(function(s) { return s.holders; }), backgroundColor: '#8e44ad', borderRadius: 6 }] },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { stepSize: 1 } } } }
    });
}

initCharts();
</script>
</body>
</html>"""

    # 替换占位符
    for key, value in EMBED.items():
        html = html.replace(f"__{key}__", value)

    return html


def main():
    parser = argparse.ArgumentParser(description="生成可视化数据看板")
    parser.add_argument("--date", type=str, help="指定分析日期 (YYYY-MM-DD)，默认最新")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        print("   请先运行: python main.py --test")
        sys.exit(1)

    print(f"📊 加载数据: {DB_PATH}")
    data = load_data(DB_PATH, args.date)
    print(f"   选手: {data['player_count']} · 持仓: {data['position_count']} · 调仓: {data['trade_count']}")

    html = generate_html(data)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"✅ 看板已生成: {OUTPUT_PATH} ({len(html):,} bytes)")

    if not args.no_open:
        webbrowser.open(f"file://{OUTPUT_PATH}")
        print("🌐 已在浏览器中打开")


if __name__ == "__main__":
    main()
