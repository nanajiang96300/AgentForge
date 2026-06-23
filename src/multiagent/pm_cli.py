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
from .config.loader import find_state_db, find_workflow_yaml


def _cmd_agent(argv):
    """Agent registry management."""
    from .runtime.registry import AgentRegistry

    if not argv:
        print("Usage: multiagent agent <list|show <name>>")
        return

    cmd = argv[0]
    if cmd == "list":
        agents = AgentRegistry.list_all()
        print(f"\nRegistered Agents ({len(agents)}):")
        print(f"{'Name':<16} {'Model':<24} {'Timeout':<8} Description")
        print("-" * 80)
        for a in agents:
            print(f"{a.name:<16} {a.model:<24} {a.timeout:<8} {a.description[:50]}")
        print()
    elif cmd == "show" and len(argv) > 1:
        a = AgentRegistry.get(argv[1])
        if not a:
            print(f"Agent not found: {argv[1]}")
            return
        print(f"\nAgent: {a.name}")
        print(f"  Description: {a.description}")
        print(f"  Model: {a.model}")
        print(f"  Timeout: {a.timeout}s")
        print(f"  Session: {a.session}")
        print(f"  Skill: {a.skill}")
        print(f"  Memory: {a.memory}")
        print(f"  Runtime: {a.runtime or 'default'}")
        print(f"  Output Required: {a.output_required}")
        print(f"  Permissions:")
        print(f"    Write: {a.permissions.get('write', [])}")
        print(f"    Read:  {a.permissions.get('read', [])}")
        print(f"    Deny:  {a.permissions.get('deny', [])}")
        print()
    elif cmd == "register" and len(argv) > 1:
        # Register from YAML file
        count = AgentRegistry.load_from_yaml(argv[1])
        print(f"Registered {count} agents from {argv[1]}")
    else:
        print("Usage: multiagent agent <list|show <name>|register <yaml>>")


def _cmd_dashboard(argv):
    """启动 Web Dashboard"""
    port = 5001
    host = "127.0.0.1"
    for i, a in enumerate(argv):
        if a.startswith("--port="):
            port = int(a.split("=")[1])
        elif a.startswith("--host="):
            host = a.split("=")[1]

    from .dashboard import create_dashboard_app
    app = create_dashboard_app()
    print(f"AgentForge Dashboard: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)

def cmd_init(args):
    """初始化 .pm/ 工作目录"""
    pm_dir = Path.cwd() / ".pm"
    for sub in ["inbox", "outbox", "archive"]:
        (pm_dir / sub).mkdir(parents=True, exist_ok=True)
    print(f"PM workspace initialized: {pm_dir}")
    print("  inbox/   ← 放置 requirements.md")
    print("  outbox/  ← PM 分析结果")
    print("  archive/ ← 已完成任务")

def cmd_submit(args, db=None, auto_run=False, dry_run=False, workflow_path=None):
    """提交需求文档。如果 auto_run=True，自动通过 Engine 执行工作流。"""
    if len(args) < 1:
        print("Usage: multiagent pm submit <requirements.md> [--run] [--dry-run]")
        return 1
    req_path = Path(args[0])
    if not req_path.exists():
        print(f"File not found: {req_path}")
        return 1

    requirements = req_path.read_text()
    task_id = f"task-{uuid.uuid4().hex[:8]}"

    _close_db = False
    if db is None:
        db = StateDB(find_state_db()); db.connect()
        _close_db = True

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

    print(f"Task submitted: {task_id}")
    print(f"  Type: feature")
    print(f"  Status: pending")

    if auto_run:
        print(f"  Auto-running workflow...")
        from .engine_cli import cmd_run
        wf = workflow_path or find_workflow_yaml()
        # Pass existing db connection to avoid filesystem search
        result_task_id = cmd_run(
            db=db,
            workflow_path=wf,
            task_id=task_id,
            dry_run=dry_run,
        )
        if result_task_id:
            print(f"  Workflow complete. Run 'multiagent pm status {task_id}' for details.")
        else:
            print(f"  Workflow failed. Check 'multiagent pm status {task_id}'.")
    else:
        print(f"  Run 'multiagent pm status {task_id}' to check progress")
        print(f"  Run 'multiagent run <workflow.yaml> --task-id {task_id} --dry-run' to execute")

    if _close_db:
        db.close()
    return 0


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

def cmd_status(args, db=None):
    """查看任务详情"""
    if len(args) < 1:
        print("Usage: multiagent pm status <task_id>")
        return 1
    task_id = args[0]

    _close_db = False
    if db is None:
        db = StateDB(find_state_db()); db.connect()
        _close_db = True

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

    if _close_db:
        db.close()

def main():
    if len(sys.argv) < 2:
        print("MultiAgent CLI v0.3.0")
        print("Commands:")
        print("  multiagent run <workflow.yaml>           Run a workflow through Engine")
        print("  multiagent metrics [--agent] [--json]     View token/cost metrics")
        print("  multiagent conductor start|status|stop    Conductor monitoring loop")
        print("  multiagent dashboard                       Web Dashboard (http://127.0.0.1:5001)")
        print("  multiagent pm init                       Initialize .pm/ workspace")
        print("  multiagent pm submit <requirements.md>    Submit requirements")
        print("  multiagent pm list                        List all tasks")
        print("  multiagent pm status <task_id>            Show task details")
        return

    # Dispatch: multiagent run → engine_cli
    if sys.argv[1] == "run":
        from .engine_cli import main as engine_main
        engine_main()
        return

    # Dispatch: multiagent metrics → metrics_cli
    if sys.argv[1] == "metrics":
        from .metrics_cli import main as metrics_main
        metrics_main()
        return

    # Dispatch: multiagent conductor → conductor_cli
    if sys.argv[1] == "conductor":
        from .conductor_cli import main as conductor_main
        conductor_main()
        return

    # Dispatch: multiagent dashboard → dashboard server
    if sys.argv[1] == "dashboard":
        _cmd_dashboard(sys.argv[2:])
        return

    # Dispatch: multiagent agent → agent registry
    if sys.argv[1] == "agent":
        _cmd_agent(sys.argv[2:])
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
    elif cmd == "submit":
        auto_run = "--run" in args
        dry_run = "--dry-run" in args
        clean_args = [a for a in args if a not in ("--run", "--dry-run")]
        cmd_submit(clean_args, auto_run=auto_run, dry_run=dry_run)
    elif cmd == "list": cmd_list(args)
    elif cmd == "status": cmd_status(args)
    else: print(f"Unknown command: {cmd}")

if __name__ == "__main__":
    main()
