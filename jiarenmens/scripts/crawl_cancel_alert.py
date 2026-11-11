#!/usr/bin/env python3
"""采集掐班告警(2026-11-11): 收盘后检查当日 crawl 班是否被 concurrency 掐掉。

背景: `cancel-in-progress` 取消的是整个 run, `cancelled` 不触发 `failure()`,
且被取消 run 里尚未启动的 job 不会再启动(`if: cancelled()` 也救不了) →
现有 `alert-on-failure` 完全看不到掐班。11-11 上午 4 班连环 cancelled、
10:15 前无数据落盘, 是人工巡检才发现的。

方案: 独立心跳 workflow(GitHub 原生 schedule, UTC 07:45 = 北京 15:45),
只读 GitHub Actions API + 发钉钉; 不写仓库、不改任何采集/推送链路。
只报 `cancelled` —— failure 已有 `alert-on-failure` 告警, 不重复打扰。

用法: python scripts/crawl_cancel_alert.py [--dry-run]
环境变量:
  SB_RUNS_JSON        注入 runs 列表(单测/演练用; 缺省走 gh api)
  GITHUB_REPOSITORY   owner/repo(CI 自带)
  GH_TOKEN            gh CLI 认证(CI 自带)
  DINGTALK_URL / DINGTALK_SECRET
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.notify.dingtalk import DingTalk  # noqa: E402  复用既有加签客户端

BJ = ZoneInfo("Asia/Shanghai")
WORKFLOW = "crawl.yml"


def fetch_runs(repo: str, workflow: str = WORKFLOW) -> list:
    """拉最近的 workflow runs; SB_RUNS_JSON 可直接注入(单测用)。"""
    raw = os.environ.get("SB_RUNS_JSON")
    if raw is None:
        raw = subprocess.check_output(
            ["gh", "api", f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=100",
             "--jq", ".workflow_runs"], text=True)
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data if isinstance(data, list) else (data.get("workflow_runs") or [])


def today_utc_runs(runs: list, day: str = "") -> list:
    """按 UTC 日期过滤。北京盘中 09:25~15:45 = UTC 01:25~07:45, 同一 UTC 日内,
    无跨日问题; 用 UTC 日期避免引入第二套时区判定。"""
    day = day or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    return [r for r in runs if str(r.get("run_started_at") or "").startswith(day)]


def bj_hhmm(iso_ts: str) -> str:
    try:
        t = datetime.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return t.astimezone(BJ).strftime("%H:%M")
    except Exception:
        return iso_ts[:16] or "?"


def canceled_runs(runs: list) -> list:
    return [r for r in runs if (r.get("conclusion") or "") == "cancelled"]


def build_message(total: int, bad: list, repo: str, day: str) -> str:
    lines = [f"### ⚠️ 采集班次被取消 {day}", "",
             f"- 当日采集班 {total} 个，其中 **{len(bad)} 个被取消**", ""]
    for r in bad:
        url = f"https://github.com/{repo}/actions/runs/{r.get('id')}"
        lines.append(f"- `{bj_hhmm(str(r.get('run_started_at') or ''))}` "
                     f"[run {r.get('id')}]({url}) 被取消")
    lines += ["", "> 常见原因：上班耗时超过班距，被 `concurrency.cancel-in-progress` 掐掉。"
              "被掐班没有数据落盘，页面仍是上一次采集；看日志后再决定是否手动补跑。"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发钉钉")
    args = ap.parse_args()
    repo = os.environ.get("GITHUB_REPOSITORY", "WXinYi/stockboard")
    try:
        runs = fetch_runs(repo)
    except Exception as e:
        print(f"::error::拉取采集班历史失败: {e}", file=sys.stderr)
        return 1
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    todays = today_utc_runs(runs, today)
    if not todays:
        # 休市/维护日不打扰; 已知限制: 若 cron 整天没触发, 这里也只会留日志,
        # 「预期班次未到」心跳留待波 3。
        print("当日无采集班(休市/维护), 跳过告警")
        return 0
    bad = canceled_runs(todays)
    if not bad:
        print(f"✅ 当日 {len(todays)} 个采集班全部完成(含 success/failure 由既有告警覆盖)")
        return 0
    text = build_message(len(todays), bad, repo, today)
    print(text)
    if args.dry_run:
        print("[dry-run] 不发送钉钉")
        return 0
    if not os.environ.get("DINGTALK_URL"):
        print("::warning::未配置 DINGTALK_URL, 掐班告警未发送", file=sys.stderr)
        return 0
    try:
        DingTalk().send_markdown(f"⚠️ 采集班次被取消 {today}", text)
        print("掐班告警已发送")
    except Exception as e:
        # 与 notify_failure 一致: 告警失败不再制造一次失败
        print(f"::error::掐班告警发送失败(不判红): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
