#!/usr/bin/env python3
"""采集掐班告警: 收盘后检查当日定时采集班是否被 concurrency 掐掉。

背景: `cancel-in-progress` 取消整个 run, `cancelled` 不触发 `failure()`,
且被取消 run 里未启动的 job 不会再启动(`if: cancelled()` 也救不了) →
现有 `alert-on-failure` 看不到掐班。

2026-11-17 修订:
- 触发改为 cron-job.org -> repository_dispatch: crawl-cancel-check,
  不再依赖 GitHub Actions schedule(曾连续多次延迟到次日凌晨才跑)。
- 只统计定时采集班: repository_dispatch(crawl / crawl-eod), 以及带
  [cron] run-name 标记的 workflow_dispatch; push / 普通手动 dispatch 不计入。
- 北京时间 15:40~16:30 才检查, 迟到/早到跳过, 避免半夜补发旧账。
- 已告警 run id 落 .github/state/crawl-cancel-alert.json, 防重复推送。

用法: python scripts/crawl_cancel_alert.py [--dry-run] [--force-window]
环境变量:
  SB_RUNS_JSON         注入 runs 列表(单测/演练用; 缺省走 gh api)
  SB_ALERT_STATE_FILE  去重状态文件(缺省 .github/state/crawl-cancel-alert.json)
  GITHUB_REPOSITORY    owner/repo(CI 自带)
  GH_TOKEN             gh CLI 认证(CI 自带)
  DINGTALK_URL / DINGTALK_SECRET
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from datetime import time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.notify.dingtalk import DingTalk  # noqa: E402  复用既有加签客户端

BJ = ZoneInfo("Asia/Shanghai")
WORKFLOW = "crawl.yml"
SCHEDULED_DISPATCH_TYPES = {"crawl", "crawl-eod"}
SCHEDULED_TITLE_MARKER = "[cron]"
WINDOW_START = time(15, 40)
WINDOW_END = time(16, 30)
MAX_STATE_IDS = 200
DEFAULT_STATE_PATH = (Path(__file__).resolve().parents[2]
                      / ".github" / "state" / "crawl-cancel-alert.json")


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def state_path() -> Path:
    return Path(os.environ.get("SB_ALERT_STATE_FILE", str(DEFAULT_STATE_PATH)))


def fetch_runs(repo: str, workflow: str = WORKFLOW) -> list:
    """拉最近的 workflow runs; SB_RUNS_JSON 可直接注入(单测用)。"""
    raw = os.environ.get("SB_RUNS_JSON")
    if raw is None:
        raw = subprocess.check_output(
            ["gh", "api", f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=100",
             "--jq", ".workflow_runs"], text=True)
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data if isinstance(data, list) else (data.get("workflow_runs") or [])


def is_scheduled_crawl_run(run: dict) -> bool:
    """只认 cron-job.org 定时班: repository_dispatch(type) 或 [cron] 标记的 dispatch。"""
    event = str(run.get("event") or "")
    title = str(run.get("display_title") or "")
    if event == "repository_dispatch":
        return title in SCHEDULED_DISPATCH_TYPES
    if event == "workflow_dispatch":
        return SCHEDULED_TITLE_MARKER in title
    return False


def today_utc_runs(runs: list, day: str = "", scheduled_only: bool = True) -> list:
    """按 UTC 日期过滤。北京盘中 09:25~15:45 = UTC 01:25~07:45, 同一 UTC 日内。"""
    day = day or now_utc().date().isoformat()
    return [r for r in runs
            if str(r.get("run_started_at") or "").startswith(day)
            and (not scheduled_only or is_scheduled_crawl_run(r))]


def is_expected_trigger_window(now: Optional[datetime.datetime] = None) -> tuple:
    """只允许北京时间工作日 15:40~16:30 检查, 迟到/早到跳过。"""
    now = (now or now_utc()).astimezone(BJ)
    if now.weekday() >= 5:
        return False, f"非工作日({now:%Y-%m-%d})"
    if WINDOW_START <= now.time() <= WINDOW_END:
        return True, "窗口内"
    return False, (f"北京时间 {now:%H:%M} 不在 "
                   f"{WINDOW_START:%H:%M}-{WINDOW_END:%H:%M}")


def bj_hhmm(iso_ts: str) -> str:
    try:
        t = datetime.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return t.astimezone(BJ).strftime("%H:%M")
    except Exception:
        return iso_ts[:16] or "?"


def canceled_runs(runs: list) -> list:
    return [r for r in runs if (r.get("conclusion") or "") == "cancelled"]


def build_message(total: int, bad: list, repo: str, day: str) -> str:
    lines = [f"### ⚠️ 定时采集班被取消 {day}", "",
             f"- 当日定时采集班 {total} 个，其中 **{len(bad)} 个被取消**", ""]
    for r in bad:
        url = f"https://github.com/{repo}/actions/runs/{r.get('id')}"
        lines.append(f"- `{bj_hhmm(str(r.get('run_started_at') or ''))}` "
                     f"[run {r.get('id')}]({url}) 被取消")
    lines += ["", "> 仅统计 cron-job.org 定时班；push/手动补跑不在此告警范围。",
              "> 常见原因：上班耗时超过班距，被 `concurrency.cancel-in-progress` 掐掉。"
              "被掐班没有数据落盘，页面仍是上一次采集；看日志后再决定是否手动补跑。"]
    return "\n".join(lines)


def _id_sort_key(value: str) -> tuple:
    return (0, int(value), "") if value.isdigit() else (1, 0, value)


def load_notified_ids(path: Optional[Path] = None) -> set:
    p = path or state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ids = data.get("notifiedRunIds") or []
        return {str(x) for x in ids}
    except FileNotFoundError:
        return set()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"::warning::告警去重状态读取失败，按空状态处理: {exc}", file=sys.stderr)
        return set()


def save_notified_ids(ids: set, path: Optional[Path] = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    kept = sorted({str(x) for x in ids}, key=_id_sort_key)[-MAX_STATE_IDS:]
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"notifiedRunIds": kept}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发钉钉、不写去重状态")
    ap.add_argument("--force-window", action="store_true", help="忽略北京时间窗口检查（补发/演练用）")
    args = ap.parse_args()

    now = now_utc()
    ok, why = is_expected_trigger_window(now)
    if not ok and not args.force_window:
        print(f"跳过掐班检查：{why}")
        return 0

    repo = os.environ.get("GITHUB_REPOSITORY", "WXinYi/stockboard")
    try:
        runs = fetch_runs(repo)
    except Exception as e:
        print(f"::error::拉取采集班历史失败: {e}", file=sys.stderr)
        return 1

    today = now.date().isoformat()
    raw_todays = today_utc_runs(runs, today, scheduled_only=False)
    todays = today_utc_runs(runs, today)
    if not todays:
        if raw_todays:
            print("::warning::当日有采集 run，但没有识别到定时标记；"
                  "请检查 cron-job.org 的 workflow_dispatch 是否传 trigger_source=cron"
                  "（收盘班应为 repository_dispatch: crawl-eod）", file=sys.stderr)
        else:
            print("当日无定时采集班(休市/维护), 跳过告警")
        return 0

    bad = canceled_runs(todays)
    if not bad:
        print(f"✅ 当日 {len(todays)} 个定时采集班全部完成"
              "(含 success/failure 由既有告警覆盖)")
        return 0

    notified = load_notified_ids()
    new_bad = [r for r in bad if str(r.get("id")) not in notified]
    if not new_bad:
        print("当日取消班均已告警过，跳过重复推送")
        return 0

    text = build_message(len(todays), bad, repo, today)
    print(text)
    if args.dry_run:
        print("[dry-run] 不发送钉钉，也不写去重状态")
        return 0
    if not os.environ.get("DINGTALK_URL"):
        print("::warning::未配置 DINGTALK_URL, 掐班告警未发送", file=sys.stderr)
        return 0
    try:
        DingTalk().send_markdown(f"⚠️ 定时采集班被取消 {today}", text)
    except Exception as e:
        # 与 notify_failure 一致: 告警失败不再制造一次失败, 也不写去重状态
        print(f"::error::掐班告警发送失败(不判红): {e}", file=sys.stderr)
        return 0

    updated = notified | {str(r.get("id")) for r in bad}
    try:
        save_notified_ids(updated)
        print(f"掐班告警已发送，去重状态已更新：{state_path()}")
    except OSError as e:
        print(f"::warning::告警已发送，但去重状态写入失败: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
