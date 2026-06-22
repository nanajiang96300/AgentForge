#!/usr/bin/env python3
"""
多Agent框架回归测试总入口

用法:
    python gates/regression.py              # 全部门禁（不含API）
    python gates/regression.py --live       # 含API调用
    python gates/regression.py --verbose    # 详细输出
"""

import sys, json, subprocess
from pathlib import Path
from datetime import datetime, timezone

GATES_DIR = Path(__file__).resolve().parent
RESULTS_DIR = GATES_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

MODULES = [
    ("DB: CRUD + Dedup", "test_db.py"),
    ("Adapters: CLI + Config", "test_adapters.py"),
    ("Orchestrator: Workflow Engine", "test_orchestrator.py"),
    ("Engine CLI: run command", "test_engine_cli.py"),
    ("PM Engine: submit via Engine", "test_pm_engine.py"),
    ("Parallel: fan-out execution", "test_parallel.py"),
    ("Heartbeat: crash recovery", "test_heartbeat.py"),
    ("Metrics CLI: token/cost", "test_metrics_cli.py"),
    ("Conductor: auto-trigger + full auto", "test_conductor.py"),
]

def run_module(path, timeout=30):
    venv_python = str(Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python3")
    r = subprocess.run([venv_python, str(path)], capture_output=True, text=True, timeout=timeout)
    return {"file": path.name, "exit": r.returncode, "passed": r.returncode == 0, "output": r.stdout}

def main():
    skip_live = "--live" not in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    print("=" * 60)
    print("MultiAgent Framework Regression")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    results = {}
    passed = 0; total = 0

    for name, module in MODULES:
        path = GATES_DIR / module
        if not path.exists():
            print(f"\n  ⚠️  {name} — SKIP (not found)")
            continue
        print(f"\n▶ {name}")
        total += 1
        try:
            r = run_module(path)
            results[name] = r
            if r["passed"]:
                passed += 1
                print(r["output"].strip())
            else:
                print(f"  ❌ FAIL (exit={r['exit']})")
        except Exception as e:
            print(f"  ❌ ERROR: {e}")

    # Archive
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    result_file = RESULTS_DIR / f"regression_{ts}.json"
    with open(result_file, "w") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), "total": total,
                    "passed": passed, "failed": total - passed}, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Total: {passed}/{total} passed")
    print(f"Results: {result_file}")
    print(f"{'='*60}")

    return 0 if total == passed else 1

if __name__ == "__main__":
    sys.exit(main())
