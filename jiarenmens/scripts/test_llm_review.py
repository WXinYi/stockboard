#!/usr/bin/env python3
"""llm_review 健壮解析器单测(纯断言, 无网络): 用五天真实输出做 fixture。
运行: cd jiarenmens && venv/bin/python scripts/test_llm_review.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.analysis.llm_review import extract_json, _repair_quotes  # noqa: E402

FIX_DIR = Path("/tmp/dssim")
ok = 0
fail = []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print(f"  ✅ {name}")
    else:
        fail.append(name)
        print(f"  ❌ {name} {detail}")


print("== extract_json 真实 fixture ==")
# v1 deepseek-chat 五天(09-07/09-08 干净, 09-09/09-10/09-11 含数字后杂引号)
for d in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"):
    f = FIX_DIR / f"out_{d}.json"
    if not f.exists():
        print(f"  ⏭️ {d} fixture 不存在, 跳过")
        continue
    raw = json.loads(f.read_text())["raw"]
    p = extract_json(raw)
    check(f"v1 {d} 解析", p is not None and isinstance(p.get("picks"), list),
          f"got {type(p)}")

# v2 deepseek-flash 五天(09-09/09-10 截断 → 应返回 None 走降级, 其余应解析出 environment)
for d in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"):
    f = FIX_DIR / f"outv2_{d}.json"
    if not f.exists():
        print(f"  ⏭️ {d} fixture 不存在, 跳过")
        continue
    o = json.loads(f.read_text())
    raw = o["raw"]
    p = extract_json(raw)
    # 当时能解析的须解析; 截断样本只要提取出含 environment 的部分对象也算有效(由 run() 补默认键)
    check(f"v2 {d} 解析", p is not None and isinstance(p.get("environment"), dict),
          f"got {str(p)[:60]}")

print("== 合成边界 ==")
check("markdown 围栏", extract_json('```json\n{"a":1}\n```') == {"a": 1})
check("尾串容忍", extract_json('{"a":1}\n希望以上分析对你有帮助') == {"a": 1})
check("数字后杂引号", extract_json('{"rank":4","reason":"x"}') is not None)
check("前导杂文", extract_json('好的，以下是JSON：\n{"a":{"b":2}}') == {"a": {"b": 2}})
check("嵌套对象", extract_json('{"x":{"y":{"z":1}},"w":null}') == {"x": {"y": {"z": 1}}, "w": None})
check("字符串内花括号", extract_json('{"why":"缩量<3%与{大}括号","a":1}') == {"why": "缩量<3%与{大}括号", "a": 1})
check("空串/None", extract_json("") is None and extract_json(None) is None)
check("纯截断(未闭合)", extract_json('{"environment":{"regime":"空仓"') is None)
check("repair 尾逗号", json.loads(_repair_quotes('{"a":1,}')) == {"a": 1})

print(f"\n{'✅' if not fail else '❌'} 通过 {ok}, 失败 {len(fail)}: {fail}")
sys.exit(1 if fail else 0)
