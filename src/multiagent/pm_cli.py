"""
PM CLI — Phase 2 项目管理命令行接口

用法:
    multiagent pm init                    初始化 .pm/ 目录
    multiagent pm submit <requirements.md> 提交需求文档
    multiagent pm list                     列出所有任务
    multiagent pm status <id>              查看任务状态
"""

import sys, uuid, json
from pathlib import Path
from datetime import datetime, timezone
from .db import StateDB, Task, now_iso

def find_state_db():
    for p in [Path.cwd()] + list(Path.cwd().parents):
        for pat in ["**/state.db", ".framework/workflow/state.db"]:
            m = list(p.glob(pat))
            if m: return m[0]
    return Path.cwd() / "state.db"

def cmd_init(args):
    """初始化 .pm/ 工作目录"""
    pm_dir = Path.cwd() / ".pm"
    for sub in ["inbox", "outbox", "archive"]:
        (pm_dir / sub).mkdir(parents=True, exist_ok=True)
    print(f"PM workspace initialized: {pm_dir}")
    print("  inbox/   ← 放置 requirements.md")
    print("  outbox/  ← PM 分析结果")
    print("  archive/ ← 已完成任务")

def cmd_submit(args):
    """提交需求文档"""
    if len(args) < 1:
        print("Usage: multiagent pm submit <requirements.md>")
        return 1
    req_path = Path(args[0])
    if not req_path.exists():
        print(f"File not found: {req_path}")
        return 1

    requirements = req_path.read_text()
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    db = StateDB(find_state_db()); db.connect()

    task = Task(
        id=task_id, type="feature", source="pm",
        workflow_id="pm-dev-test-loop", current_step="pm_analyze",
        context={"requirements_text": requirements, "source_file": str(req_path.resolve())},
        dedup_key=None, created_at=now_iso(),
    )
    db.insert_task(task)

    # 复制到 inbox
    inbox_dir = Path.cwd() / ".pm" / "inbox" / task_id
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / "requirements.md").write_text(requirements)

    db.close()
    print(f"Task submitted: {task_id}")
    print(f"  Type: feature")
    print(f"  Status: pending → PM analyzing...")
    print(f"  Run 'multiagent pm status {task_id}' to check progress")

def cmd_list(args):
    """列出所有任务"""
    db = StateDB(find_state_db()); db.connect()
    rows = db.conn.execute(
        "SELECT id, type, status, current_step, created_at FROM tasks ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    db.close()
    if not rows:
        print("No tasks found.")
        return
    print(f"{'Task ID':<14} {'Type':<10} {'Status':<12} {'Step':<16} {'Created'}")
    print("-" * 70)
    for r in rows:
        print(f"{r[0]:<14} {r[1]:<10} {r[2]:<12} {r[3] or '-':<16} {r[4] or '-'}")

def cmd_status(args):
    """查看任务详情"""
    if len(args) < 1:
        print("Usage: multiagent pm status <task_id>")
        return 1
    task_id = args[0]
    db = StateDB(find_state_db()); db.connect()

    task = db.get_task(task_id)
    if not task:
        print(f"Task not found: {task_id}")
        db.close(); return 1

    print(f"Task: {task_id}")
    print(f"  Type: {task.get('type', '-')}")
    print(f"  Status: {task.get('status', '-')}")
    print(f"  Current Step: {task.get('current_step', '-')}")
    print(f"  Retries: {task.get('retry_count', 0)}")
    print(f"  Rejections: {task.get('rejection_count', 0)}")

    # 查看步骤结果
    steps = db.conn.execute(
        "SELECT step_id, agent, status, error, started_at, completed_at FROM step_results WHERE task_id = ? ORDER BY id",
        (task_id,)
    ).fetchall()
    if steps:
        print(f"\n  Steps:")
        for s in steps:
            status_icon = "✅" if s[2] == "completed" else ("❌" if s[2] in ("failed","crashed") else "⏳")
            print(f"    {status_icon} {s[0]} ({s[1]}): {s[2]}")
            if s[3]:
                print(f"       Error: {s[3][:80]}")

    # 查看指标
    metrics = db.conn.execute(
        "SELECT agent, input_tokens, output_tokens, cost_usd, duration_ms FROM agent_metrics WHERE task_id = ?",
        (task_id,)
    ).fetchall()
    if metrics:
        print(f"\n  Token Usage:")
        for m in metrics:
            print(f"    {m[0]}: {m[1]:,} in / {m[2]:,} out, ${m[3]:.4f}, {m[4]:,}ms")

    db.close()

def main():
    if len(sys.argv) < 2:
        print("MultiAgent PM CLI v0.2.0")
        print("Commands:")
        print("  multiagent pm init                     Initialize .pm/ workspace")
        print("  multiagent pm submit <requirements.md>  Submit requirements")
        print("  multiagent pm list                      List all tasks")
        print("  multiagent pm status <task_id>          Show task details")
        return

    # Support both: multiagent pm <cmd>  and  multiagent <cmd>
    if sys.argv[1] == "pm":
        if len(sys.argv) < 3:
            print("Usage: multiagent pm <init|submit|list|status>")
            return
        cmd = sys.argv[2]
        args = sys.argv[3:]
    else:
        cmd = sys.argv[1]
        args = sys.argv[2:]

    if cmd == "init": cmd_init(args)
    elif cmd == "submit": cmd_submit(args)
    elif cmd == "list": cmd_list(args)
    elif cmd == "status": cmd_status(args)
    else: print(f"Unknown command: {cmd}")

if __name__ == "__main__":
    main()
