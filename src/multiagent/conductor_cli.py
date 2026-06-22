"""
Conductor CLI — Phase 4 调度器命令行接口

用法:
    multiagent conductor start             启动监控循环（前台）
    multiagent conductor start --daemon    后台运行
    multiagent conductor status            查看 Conductor 和任务队列状态
    multiagent conductor stop              停止监控循环
    multiagent conductor alerts            查看待处理升级
    multiagent conductor retry <task_id>   重新执行 escalated 任务
"""

import os
import sys
import signal
import time
from pathlib import Path

from .db import StateDB
from .conductor import Conductor


def _find_db():
    """查找 state.db"""
    cwd = Path.cwd()
    for p in [cwd / "state.db", cwd / ".framework" / "workflow" / "state.db"]:
        if p.exists():
            return p
    return cwd / "state.db"


def _find_workflow():
    """查找默认 workflow YAML"""
    cwd = Path.cwd()
    candidates = [
        cwd / "architectures" / "dev-test-loop" / "workflow" / "pm-dev-test.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _find_roles():
    """查找 roles.yaml"""
    cwd = Path.cwd()
    candidates = [
        cwd / "architectures" / "dev-test-loop" / "config" / "roles.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _get_conductor(db_path, workflow_path, roles_path=None, poll_interval=5):
    """创建 Conductor 实例"""
    return Conductor(
        db_path=db_path,
        workflow_path=workflow_path,
        roles_path=roles_path,
        poll_interval=poll_interval,
    )


# ── Global conductor reference for signal handling ──
_conductor_instance = None


def cmd_start(args):
    """启动 Conductor 监控循环"""
    global _conductor_instance

    daemon = "--daemon" in args or "-d" in args
    interval = 5
    for i, a in enumerate(args):
        if a.startswith("--interval="):
            try:
                interval = int(a.split("=")[1])
            except ValueError:
                pass

    db_path = _find_db()
    workflow_path = _find_workflow()
    roles_path = _find_roles()

    if not workflow_path:
        print("Error: No workflow YAML found.")
        print("Run from project root or set up architectures/dev-test-loop/")
        return 1

    print(f"Conductor starting...")
    print(f"  Database: {db_path}")
    print(f"  Workflow: {workflow_path}")
    print(f"  Roles: {roles_path or 'auto-detect'}")
    print(f"  Poll interval: {interval}s")
    print(f"  Mode: {'daemon' if daemon else 'foreground'}")

    c = _get_conductor(db_path, workflow_path, roles_path, poll_interval=interval)
    _conductor_instance = c

    if daemon:
        # Fork to background
        pid = os.fork()
        if pid > 0:
            print(f"  PID: {pid}")
            print(f"Conductor running in background. Use 'multiagent conductor status' to check.")
            print(f"Use 'multiagent conductor stop' to stop.")
            return 0
        # Child process
        os.setsid()
        c.start(blocking=True)
    else:
        print(f"  Press Ctrl+C to stop.")
        print()
        try:
            c.start(blocking=True)
        except KeyboardInterrupt:
            print(f"\nConductor stopped.")
    return 0


def cmd_status(args):
    """查看 Conductor 状态"""
    db_path = _find_db()
    workflow_path = _find_workflow()
    if not workflow_path:
        print("Error: No workflow YAML found.")
        return 1

    c = _get_conductor(db_path, workflow_path)
    status = c.status()

    print(f"{'='*55}")
    print(f"  Conductor Status")
    print(f"{'='*55}")

    cd = status["conductor"]
    running_str = "▶ RUNNING" if cd["running"] else "■ STOPPED"
    print(f"  State:        {running_str}")
    if cd["started_at"]:
        print(f"  Started:      {cd['started_at']}")
    if cd["last_poll_at"]:
        print(f"  Last Poll:    {cd['last_poll_at']}")
    print(f"  Poll Interval: {cd['poll_interval_s']}s")
    print(f"  Tasks Processed: {cd['tasks_processed']}")
    print(f"  Tasks Failed:    {cd['tasks_failed']}")
    print(f"  Escalations:     {cd['escalations_detected']}")
    if cd["current_task_id"]:
        print(f"  Current Task:    {cd['current_task_id']}")
    if cd["tasks_in_flight"]:
        print(f"  In Flight:       {cd['tasks_in_flight']}")

    q = status["queue"]
    print(f"\n  ── Queue ──")
    print(f"  Pending:   {q['pending']}")
    print(f"  Running:   {q['running']}")
    print(f"  Escalated: {q['escalated']}")

    alerts = status["alerts"]
    print(f"\n  ── Alerts ──")
    print(f"  Pending Escalations: {alerts['pending_escalations']}")

    if status["pending_task_ids"]:
        print(f"\n  Pending Tasks:")
        for tid in status["pending_task_ids"]:
            print(f"    • {tid}")
    if status["escalated_task_ids"]:
        print(f"\n  Escalated Tasks:")
        for tid in status["escalated_task_ids"]:
            print(f"    • {tid}  (use 'conductor retry {tid}' or 'conductor alerts')")

    print()
    return 0


def cmd_stop(args):
    """停止 Conductor（通过发送 SIGTERM 或更新 workflow_state）"""
    db_path = _find_db()
    db = StateDB(db_path)
    db.connect()

    try:
        # Update workflow_state to signal stop
        db.conn.execute(
            """INSERT OR REPLACE INTO workflow_state (workflow_id, status, updated_at)
               VALUES ('conductor', 'stopped', ?)""",
            (db.now_iso() if hasattr(db, 'now_iso') else __import__('multiagent.db', fromlist=['now_iso']).now_iso()),
        )
        db.conn.commit()

        # Check for .conductor.pid file
        pid_file = Path.cwd() / ".conductor.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"Sent SIGTERM to PID {pid}")
            except (ValueError, ProcessLookupError):
                print("Conductor process not found (stale PID file).")
            pid_file.unlink(missing_ok=True)
        else:
            print("Stop signal written to workflow_state.")
            print("If conductor is running in another terminal, it will stop on next poll.")
    finally:
        db.close()
    return 0


def cmd_alerts(args):
    """查看待处理的升级事件"""
    db_path = _find_db()
    db = StateDB(db_path)
    db.connect()

    try:
        escalations = db.get_pending_escalations()
        if not escalations:
            print("No pending escalations.")
            return 0

        print(f"{'='*70}")
        print(f"  Pending Escalations ({len(escalations)})")
        print(f"{'='*70}")

        for esc in escalations:
            print(f"\n  ID:       {esc['id']}")
            print(f"  Task:     {esc['task_id']}")
            print(f"  Step:     {esc['step_id']}")
            print(f"  Reason:   {esc['reason']}")
            print(f"  Created:  {esc['created_at']}")
            print(f"  ── Actions ──")
            print(f"    multiagent conductor retry {esc['task_id']}   # Retry task")
            print(f"    multiagent conductor reject {esc['task_id']}  # Reject task")
            print(f"    multiagent pm status {esc['task_id']}          # View details")
    finally:
        db.close()
    return 0


def cmd_retry(args):
    """重新执行 escalated 任务"""
    if not args:
        print("Usage: multiagent conductor retry <task_id>")
        return 1

    task_id = args[0]
    db_path = _find_db()
    workflow_path = _find_workflow()
    roles_path = _find_roles()

    if not workflow_path:
        print("Error: No workflow YAML found.")
        return 1

    c = _get_conductor(db_path, workflow_path, roles_path)
    result = c.retry_escalated(task_id)

    if result:
        print(f"Task {task_id} re-queued. Workflow result: {result}")
        return 0
    else:
        print(f"Task {task_id} not found or not in escalated state.")
        return 1


def cmd_reject(args):
    """Reject escalated task（放弃任务）"""
    if not args:
        print("Usage: multiagent conductor reject <task_id>")
        return 1

    task_id = args[0]
    db_path = _find_db()
    db = StateDB(db_path)
    db.connect()

    try:
        task = db.get_task(task_id)
        if not task:
            print(f"Task not found: {task_id}")
            return 1

        if task.get("status") != "escalated":
            print(f"Task {task_id} is not in escalated state (current: {task.get('status')})")
            return 1

        db.update_task_status(task_id, "failed")

        # Resolve pending escalations
        for esc in db.get_pending_escalations():
            if esc["task_id"] == task_id:
                db.resolve_escalation(esc["id"], "rejected")

        print(f"Task {task_id} rejected (marked as failed).")
        return 0
    finally:
        db.close()


def main():
    if len(sys.argv) < 2:
        print("Conductor CLI v0.3.0")
        print("Commands:")
        print("  multiagent conductor start              Start monitoring loop (foreground)")
        print("  multiagent conductor start --daemon      Start in background")
        print("  multiagent conductor status              Show conductor and queue status")
        print("  multiagent conductor stop                Stop monitoring loop")
        print("  multiagent conductor alerts              Show pending escalations")
        print("  multiagent conductor retry <task_id>     Retry escalated task")
        print("  multiagent conductor reject <task_id>    Reject escalated task")
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "start": cmd_start,
        "status": cmd_status,
        "stop": cmd_stop,
        "alerts": cmd_alerts,
        "retry": cmd_retry,
        "reject": cmd_reject,
    }

    if cmd in commands:
        sys.exit(commands[cmd](args) or 0)
    else:
        print(f"Unknown command: {cmd}")
        print("Available: start, status, stop, alerts, retry, reject")
        sys.exit(1)


if __name__ == "__main__":
    main()
