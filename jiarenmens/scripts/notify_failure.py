#!/usr/bin/env python3
"""CI 失败告警: 任何 workflow 失败时向钉钉发一条明确消息。

由来(2026-09-15 静默审计): 9 个 workflow 全线没有 if: failure() 步骤, 失败只能表现为
"钉钉少一条推送 / 页面还是旧数据", 无人能第一时间知道。本脚本补上这条链路。

用法(workflow 末尾独立 job, 仓库已 checkout):
  cd jiarenmens && python3 scripts/notify_failure.py "<工作流名>" "<run链接>" '<toJSON(needs)>'
环境变量: DINGTALK_URL / DINGTALK_SECRET(与 notify_daily 同一套 secret)

约定: 告警失败不抛出——避免"告警发不出去"再制造一次失败, 只在日志留痕(::warning::)。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.notify.dingtalk import DingTalk  # noqa: E402  复用既有加签客户端, 不另写请求逻辑


def job_states(needs_json: str):
    """从 toJSON(needs) 里挑出失败/取消的 job 名。"""
    failed, cancelled = [], []
    try:
        needs = json.loads(needs_json) if needs_json else {}
    except Exception:
        return failed, cancelled
    for name, st in (needs or {}).items():
        if not isinstance(st, dict):
            continue
        if st.get("result") == "failure":
            failed.append(name)
        elif st.get("result") == "cancelled":
            cancelled.append(name)
    return failed, cancelled


def main() -> int:
    wf = sys.argv[1] if len(sys.argv) > 1 else "workflow"
    run_url = sys.argv[2] if len(sys.argv) > 2 else ""
    needs_json = sys.argv[3] if len(sys.argv) > 3 else ""

    failed, cancelled = job_states(needs_json)
    lines = [f"### ❌ CI 失败: {wf}", ""]
    if failed:
        lines.append(f"- 失败 job: {'、'.join(failed)}")
    if cancelled:
        lines.append(f"- 被取消 job: {'、'.join(cancelled)}")
    if not failed and not cancelled:
        lines.append("- 失败 job: (未取到 needs 信息, 请点日志自查)")
    if run_url.startswith("https://"):
        lines.append(f"- 运行日志: {run_url}")
    lines += ["", "> 本班数据可能未更新(页面仍是上一次采集), 看完日志再决定是否手动重跑。"]
    text = "\n".join(lines)

    if not os.environ.get("DINGTALK_URL"):
        print("::warning::未配置 DINGTALK_URL, 失败告警未发送", file=sys.stderr)
        print(text)
        return 0
    try:
        DingTalk().send_markdown(f"❌ {wf} 失败", text)
        print("失败告警已发送")
    except Exception as e:  # 告警失败不再制造一次失败
        print(f"::warning::失败告警发送失败(不改变本 job 结果): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
