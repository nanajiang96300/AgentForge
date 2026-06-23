"""
Conductor CLI — Phase 5 生产化调度器命令行接口

用法:
    multiagent conductor start                  启动监控循环（默认后台）
    multiagent conductor start --foreground     前台运行
    multiagent conductor start --discord-webhook <URL>  启用 Discord 通知
    multiagent conductor status                 查看 Conductor 和任务队列状态
    multiagent conductor stop                   停止监控循环
    multiagent conductor restart                重启监控循环
    multiagent conductor alerts                 查看待处理升级
    multiagent conductor retry <task_id>        重新执行 escalated 任务
    multiagent conductor reject <task_id>       放弃 escalated 任务
"""

import argparse
import logging
import os
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .db import StateDB
from .conductor import Conductor, ProjectConfig
from .notify import create_notifier

# ── Path discovery ──

def _find_db():
    cwd = Path.cwd()
    for p in [cwd / "state.db", cwd / ".framework" / "workflow" / "state.db"]:
        if p.exists():
            return p
    return cwd / "state.db"


def _find_workflow():
    cwd = Path.cwd()
    for p in [cwd / "architectures" / "dev-test-loop" / "workflow" / "pm-dev-test.yaml"]:
        if p.exists():
            return p
    return None


def _find_roles():
    cwd = Path.cwd()
    for p in [cwd / "architectures" / "dev-test-loop" / "config" / "roles.yaml"]:
        if p.exists():
            return p
    return None


# ── Conductor instance (global for signal handling) ──
_conductor: Conductor = None


def _setup_logging(log_dir: Path, level=logging.INFO) -> logging.Logger:
    """配置日志：控制台 + 文件轮转"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "conductor.log"

    logger = logging.getLogger("multiagent.conductor")
    logger.setLevel(level)

    # Console handler
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(ch)

    # Rotating file handler
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        fh = RotatingFileHandler(
            str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5,
        )
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)

    return logger


# ── CLI Commands ──

def cmd_start(args):
    """启动 Conductor 监控循环"""
    global _conductor

    foreground = args.foreground
    interval = args.interval
    pid_file = Path(args.pid_file) if args.pid_file else None
    discord_webhook = args.discord_webhook

    db_path = _find_db()
    workflow_path = _find_workflow()
    roles_path = _find_roles()

    if not workflow_path:
        print("Error: No workflow YAML found.")
        return 1

    # Setup logging
    log_dir = db_path.parent / "logs"
    logger = _setup_logging(log_dir)

    logger.info("Starting Conductor...")
    logger.info("  Database: %s", db_path)
    logger.info("  Workflow: %s", workflow_path)
    logger.info("  Roles: %s", roles_path or "auto-detect")
    logger.info("  Poll: %ds", interval)
    logger.info("  PID file: %s", pid_file or db_path.parent / ".conductor.pid")
    if discord_webhook:
        logger.info("  Discord: enabled")

    # Create notifiers
    notifiers = create_notifier(webhook_url=discord_webhook)

    project = ProjectConfig(
        name=db_path.parent.name or "default",
        db_path=db_path,
        workflow_path=workflow_path,
        roles_path=roles_path,
    )

    c = Conductor(
        projects=[project],
        poll_interval=interval,
        logger=logger,
        notifiers=notifiers,
        pid_file=pid_file,
        max_workers=args.workers,
    )
    if args.pm_auto_discover:
        c.pm_auto_discover = True
    _conductor = c

    if foreground:
        print(f"Database: {db_path}")
        print(f"Workflow: {workflow_path}")
        print(f"Poll interval: {interval}s")
        print(f"Press Ctrl+C to stop.\n")
        try:
            c.start(blocking=True)
        except KeyboardInterrupt:
            logger.info("Interrupted, shutting down...")
            c.stop()
    else:
        # Daemon mode: double-fork
        pid = os.fork()
        if pid > 0:
            print(f"Conductor started (PID={pid})")
            logger.info("Daemon PID: %d", pid)
            return 0

        os.setsid()
        # Redirect stdio
        devnull = open(os.devnull, "w")
        os.dup2(devnull.fileno(), sys.stdin.fileno())
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        os.dup2(devnull.fileno(), sys.stderr.fileno())

        try:
            c.start(blocking=True)
        except KeyboardInterrupt:
            c.stop()
        except SystemExit:
            c.stop()

    return 0


def cmd_status(args):
    """查看 Conductor 状态"""
    db_path = _find_db()
    workflow_path = _find_workflow()
    if not workflow_path:
        print("Error: No workflow YAML found.")
        return 1

    project = ProjectConfig(
        name=db_path.parent.name or "default",
        db_path=db_path,
        workflow_path=workflow_path,
    )

    c = Conductor(projects=[project])
    status = c.status()

    print(f"{'='*55}")
    print(f"  Conductor Status")
    print(f"{'='*55}")

    cd = status["conductor"]
    print(f"  State:        {'▶ RUNNING' if cd['running'] else '■ STOPPED'}")
    print(f"  PID:          {cd['pid']}")
    print(f"  PID File:     {cd['pid_file']} ({'exists' if cd['pid_file_exists'] else 'missing'})")
    if cd["started_at"]:
        print(f"  Started:      {cd['started_at']}")
    if cd["last_poll_at"]:
        print(f"  Last Poll:    {cd['last_poll_at']}")
    print(f"  Poll:         {cd['poll_interval_s']}s")
    print(f"  Max Workers:  {cd.get('max_workers', '?')}")
    print(f"  In Flight:    {cd.get('in_flight_count', 0)}")
    print(f"  Processed:    {cd['tasks_processed']}")
    print(f"  Failed:       {cd['tasks_failed']}")
    print(f"  Escalations:  {cd['escalations_detected']}")
    if cd["current_task_id"]:
        print(f"  Current Task: {cd['current_task_id']}")
    print(f"  Projects:     {cd['projects']}")

    for proj in status.get("projects", []):
        print(f"\n  ── {proj['name']} ──")
        print(f"  Pending:   {proj['pending']}")
        print(f"  Running:   {proj['running']}")
        print(f"  Escalated: {proj['escalated']}")
        print(f"  Alerts:    {proj['alerts']}")

    # Show in-flight task progress
    in_flight = status.get("in_flight", [])
    if in_flight:
        print(f"\n  ── In-Flight Tasks ({len(in_flight)}) ──")
        for task in in_flight:
            elapsed = ""
            if task.get("started_at"):
                try:
                    from datetime import datetime, timezone
                    start = datetime.fromisoformat(task["started_at"])
                    secs = (datetime.now(timezone.utc) - start).total_seconds()
                    elapsed = f" {int(secs)}s"
                except: pass
            step = task.get("latest_step", task.get("current_step", "?"))
            agent = task.get("latest_agent", "")
            cost = task.get("cost_usd", 0)
            tokens_in = task.get("input_tokens", 0) or 0
            tokens_out = task.get("output_tokens", 0) or 0

            # Progress bar
            bar = task.get("bar", "")
            subtasks_info = ""
            if task.get("total_subtasks", 0) > 0:
                subtasks_info = f" | Subtasks: {task.get('completed_subtasks',0)}/{task.get('total_subtasks',0)}"

            print(f"    • {task['task_id']}")
            print(f"      {bar} {step} ({agent}) | {task.get('status','?')}{elapsed}{subtasks_info}")
            print(f"      Tokens: {tokens_in:,} in / {tokens_out:,} out | Cost: \${cost:.4f}")

    # List pending/escalated tasks from DB
    db = StateDB(db_path)
    db.connect()
    try:
        pending = db.get_pending_tasks()
        if pending:
            print(f"\n  Pending Tasks:")
            for t in pending:
                print(f"    • {t['id']} ({t.get('type','?')})")

        escalated = db.get_escalated_tasks()
        if escalated:
            print(f"\n  Escalated Tasks:")
            for t in escalated:
                print(f"    • {t['id']} (use 'conductor retry {t['id']}' or 'conductor alerts')")
    finally:
        db.close()
    print()
    return 0


def cmd_stop(args):
    """停止 Conductor"""
    pid_file = Path(args.pid_file) if args.pid_file else _find_db().parent / ".conductor.pid"

    # Method 1: PID file
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to PID {pid}")
            # Wait for graceful shutdown
            for _ in range(10):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    print("Conductor stopped gracefully.")
                    pid_file.unlink(missing_ok=True)
                    return 0
            # Force kill
            print("Graceful shutdown timeout, sending SIGKILL...")
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            pid_file.unlink(missing_ok=True)
            return 0
        except (ValueError, ProcessLookupError):
            print("Conductor process not found (stale PID file).")
            pid_file.unlink(missing_ok=True)
            return 0

    # Method 2: DB stop signal
    db_path = _find_db()
    db = StateDB(db_path)
    db.connect()
    try:
        db.conn.execute(
            """INSERT OR REPLACE INTO workflow_state (workflow_id, status, updated_at)
               VALUES ('conductor', 'stopped', datetime('now'))"""
        )
        db.conn.commit()
        print("Stop signal written to database.")
        print("If conductor is running, it will stop on next poll cycle.")
    finally:
        db.close()
    return 0


def cmd_restart(args):
    """重启 Conductor"""
    print("Stopping conductor...")
    cmd_stop(args)
    time.sleep(1)
    print("Starting conductor...")
    return cmd_start(args)


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
            print(f"    multiagent conductor retry {esc['task_id']}")
            print(f"    multiagent conductor reject {esc['task_id']}")
            print(f"    multiagent pm status {esc['task_id']}")
    finally:
        db.close()
    return 0


def cmd_retry(args):
    """重新执行 escalated 任务"""
    if not args.task_id:
        print("Usage: multiagent conductor retry <task_id>")
        return 1

    db_path = _find_db()
    workflow_path = _find_workflow()
    roles_path = _find_roles()

    if not workflow_path:
        print("Error: No workflow YAML found.")
        return 1

    project = ProjectConfig(
        name=db_path.parent.name or "default",
        db_path=db_path,
        workflow_path=workflow_path,
        roles_path=roles_path,
    )
    c = Conductor(projects=[project])
    result = c.retry_escalated(args.task_id, project=project)

    if result:
        print(f"Task {args.task_id} re-queued. Result: {result}")
    else:
        print(f"Task {args.task_id} not found or not in escalated state.")
        return 1
    return 0


def cmd_reject(args):
    """Reject escalated task"""
    if not args.task_id:
        print("Usage: multiagent conductor reject <task_id>")
        return 1

    db_path = _find_db()
    db = StateDB(db_path)
    db.connect()

    try:
        task = db.get_task(args.task_id)
        if not task:
            print(f"Task not found: {args.task_id}")
            return 1

        if task.get("status") != "escalated":
            print(f"Task {args.task_id} not escalated (current: {task.get('status')})")
            return 1

        db.update_task_status(args.task_id, "failed")
        for esc in db.get_pending_escalations():
            if esc["task_id"] == args.task_id:
                db.resolve_escalation(esc["id"], "rejected")

        print(f"Task {args.task_id} rejected (marked as failed).")
    finally:
        db.close()
    return 0


# ── Argument parsing ──

def _build_parser():
    parser = argparse.ArgumentParser(
        prog="multiagent conductor",
        description="Conductor — AgentForge scheduling daemon",
    )
    sub = parser.add_subparsers(dest="command", help="Commands")

    # start
    p_start = sub.add_parser("start", help="Start monitoring loop")
    p_start.add_argument("--foreground", "-f", action="store_true",
                         help="Run in foreground (default: daemon)")
    p_start.add_argument("--interval", "-i", type=int, default=5,
                         help="Poll interval in seconds (default: 5)")
    p_start.add_argument("--pid-file", default=None,
                         help="PID file path")
    p_start.add_argument("--workers", "-w", type=int, default=3,
                         help="Max concurrent tasks (default: 3)")
    p_start.add_argument("--pm-auto-discover", action="store_true",
                         help="Auto-discover GitHub Issues as tasks")
    p_start.add_argument("--discord-webhook", default=None,
                         help="Discord webhook URL for notifications")

    # status
    sub.add_parser("status", help="Show conductor and queue status")

    # stop
    p_stop = sub.add_parser("stop", help="Stop monitoring loop")
    p_stop.add_argument("--pid-file", default=None, help="PID file path")

    # restart
    p_restart = sub.add_parser("restart", help="Restart monitoring loop")
    p_restart.add_argument("--foreground", "-f", action="store_true")
    p_restart.add_argument("--interval", "-i", type=int, default=5)
    p_restart.add_argument("--pid-file", default=None)
    p_restart.add_argument("--workers", "-w", type=int, default=3)
    p_restart.add_argument("--pm-auto-discover", action="store_true")
    p_restart.add_argument("--discord-webhook", default=None)

    # alerts
    sub.add_parser("alerts", help="Show pending escalations")

    # retry
    p_retry = sub.add_parser("retry", help="Retry escalated task")
    p_retry.add_argument("task_id", help="Task ID")

    # reject
    p_reject = sub.add_parser("reject", help="Reject escalated task")
    p_reject.add_argument("task_id", help="Task ID")

    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "start": cmd_start,
        "status": cmd_status,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "alerts": cmd_alerts,
        "retry": cmd_retry,
        "reject": cmd_reject,
    }

    if args.command in commands:
        sys.exit(commands[args.command](args) or 0)
    else:
        print(f"Unknown command: {args.command}")
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
