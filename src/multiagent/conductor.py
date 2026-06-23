"""
Conductor — Phase 5 生产化调度器。监控 state.db，自动触发工作流，Discord 通知。

Phase 5 改进:
  - PID 文件管理 (start/stop/restart 完整生命周期)
  - Python logging + RotatingFileHandler (daemon 日志)
  - 优雅停止 (SIGTERM + workflow_state 双通道)
  - Discord Webhook 通知 (零 Token 消耗)
  - 多项目并发监控
"""

import json
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional, Callable

from .db import StateDB, Task, now_iso
from .core.progress import calculate_task_progress, progress_bar

_DEFAULT_POLL_INTERVAL = 5
_log = logging.getLogger("multiagent.conductor")


def _get_default_logger(level=logging.INFO):
    """获取默认 logger（无 handler 时创建）"""
    if not _log.handlers:
        _log.setLevel(level)
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        _log.addHandler(h)
    return _log


@dataclass
class InFlightTask:
    """正在执行的任务进度"""
    task_id: str
    project_name: str
    started_at: str
    thread_id: int
    future: object = None  # concurrent.futures.Future


@dataclass
class ConductorState:
    """Conductor 运行时状态"""
    running: bool = False
    tasks_processed: int = 0
    tasks_failed: int = 0
    escalations_detected: int = 0
    current_task_id: Optional[str] = None
    started_at: Optional[str] = None
    last_poll_at: Optional[str] = None
    pid_file: Optional[Path] = None
    max_workers: int = 3  # Max concurrent tasks


@dataclass
class ProjectConfig:
    """单个项目的配置"""
    name: str
    db_path: Path
    workflow_path: Path
    roles_path: Optional[Path] = None


class Conductor:
    """Phase 5 调度器：Daemon 生命周期 + 多项目 + Discord 通知"""

    def __init__(
        self,
        db_path: Path = None,
        workflow_path: Path = None,
        roles_path: Optional[Path] = None,
        poll_interval: int = _DEFAULT_POLL_INTERVAL,
        projects: Optional[list[ProjectConfig]] = None,
        logger: Optional[logging.Logger] = None,
        notifiers: Optional[list[Callable]] = None,
        pid_file: Optional[Path] = None,
        max_workers: int = 3,
    ):
        # Single-project mode (backward compat)
        self._single_db = db_path
        self._single_wf = workflow_path
        self._single_roles = roles_path

        # Multi-project mode
        self.projects = projects or []
        if db_path and workflow_path and not self.projects:
            name = db_path.parent.name or "default"
            self.projects = [
                ProjectConfig(
                    name=name,
                    db_path=Path(db_path),
                    workflow_path=Path(workflow_path),
                    roles_path=Path(roles_path) if roles_path else None,
                )
            ]

        self.poll_interval = poll_interval
        self.state = ConductorState(pid_file=pid_file, max_workers=max_workers)
        self._lock = threading.Lock()
        self._in_flight: dict[str, InFlightTask] = {}
        self._executor: Optional[ThreadPoolExecutor] = None
        self.pm_auto_discover = False  # Set by CLI
        self.pm_discover_labels = ["bug", "feature", "enhancement"]
        self.log = logger or _get_default_logger()
        self.notifiers = notifiers or []

    # ── Public API ──

    def start(self, blocking: bool = True):
        """启动监控循环"""
        if self.state.running:
            self.log.warning("Conductor already running")
            return

        self._write_pid_file()
        self.state.running = True
        self.state.started_at = now_iso()
        self.log.info("Conductor started (PID=%d, poll=%ds)", os.getpid(), self.poll_interval)

        if blocking:
            self._monitor_loop()
        else:
            t = threading.Thread(target=self._monitor_loop, daemon=True)
            t.start()

    def stop(self):
        """停止监控循环，并清理所有 in-flight agent 进程"""
        self.log.info("Conductor stop requested")
        self.state.running = False
        self._kill_in_flight_agents()

    def status(self) -> dict:
        """返回 Conductor 运行时状态"""
        projects_status = []
        for proj in self.projects:
            db = self._connect_db(proj.db_path)
            try:
                pending = db.get_pending_tasks()
                escalated = db.get_escalated_tasks()
                pend_esc = db.get_pending_escalations()
                rows = db.conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status = 'running'"
                ).fetchone()
                projects_status.append({
                    "name": proj.name,
                    "pending": len(pending),
                    "running": rows[0] if rows else 0,
                    "escalated": len(escalated),
                    "alerts": len(pend_esc),
                })
            finally:
                db.close()

        pid_file_exists = (
            self.state.pid_file and self.state.pid_file.exists()
        )

        # Aggregate queue counts
        total_pending = sum(p["pending"] for p in projects_status)
        total_running = sum(p["running"] for p in projects_status)
        total_escalated = sum(p["escalated"] for p in projects_status)
        total_alerts = sum(p["alerts"] for p in projects_status)

        # In-flight task progress
        in_flight_progress = []
        with self._lock:
            for task_id, inf in list(self._in_flight.items()):
                progress = {
                    "task_id": task_id,
                    "project": inf.project_name,
                    "started_at": inf.started_at,
                }
                # Get current step from DB
                try:
                    for project in self.projects:
                        if project.name == inf.project_name:
                            db_proj = self._connect_db(project.db_path)
                            try:
                                t = db_proj.get_task(task_id)
                                if t:
                                    progress["status"] = t.get("status", "?")
                                    progress["current_step"] = t.get("current_step", "?")
                                    progress["retry_count"] = t.get("retry_count", 0)
                                    progress["rejection_count"] = t.get("rejection_count", 0)
                                # Get latest step result
                                row = db_proj.conn.execute(
                                    "SELECT step_id, agent, status FROM step_results "
                                    "WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                                    (task_id,)
                                ).fetchone()
                                if row:
                                    progress["latest_step"] = row[0]
                                    progress["latest_agent"] = row[1]
                                    progress["latest_status"] = row[2]
                                # Fine-grained progress (generic, no hardcoded steps)
                                try:
                                    p = calculate_task_progress(db_proj, task_id)
                                    progress.update(p)
                                except Exception:
                                    progress["pct"] = 0
                                    progress["bar"] = progress_bar(0)
                                # Get token usage
                                metrics = db_proj.conn.execute(
                                    "SELECT SUM(input_tokens), SUM(output_tokens), SUM(cost_usd), "
                                    "SUM(duration_ms) FROM agent_metrics WHERE task_id = ?",
                                    (task_id,)
                                ).fetchone()
                                if metrics and metrics[0]:
                                    progress["input_tokens"] = metrics[0]
                                    progress["output_tokens"] = metrics[1]
                                    progress["cost_usd"] = round(metrics[2] or 0, 6)
                                    progress["duration_ms"] = metrics[3]
                            finally:
                                db_proj.close()
                            break
                except Exception:
                    pass
                in_flight_progress.append(progress)

        return {
            "conductor": {
                "running": self.state.running,
                "pid": os.getpid(),
                "pid_file": str(self.state.pid_file) if self.state.pid_file else None,
                "pid_file_exists": pid_file_exists,
                "started_at": self.state.started_at,
                "last_poll_at": self.state.last_poll_at,
                "poll_interval_s": self.poll_interval,
                "tasks_processed": self.state.tasks_processed,
                "tasks_failed": self.state.tasks_failed,
                "escalations_detected": self.state.escalations_detected,
                "current_task_id": self.state.current_task_id,
                "projects": len(self.projects),
                "max_workers": self.state.max_workers,
                "in_flight_count": len(self._in_flight),
            },
            "queue": {
                "pending": total_pending,
                "running": total_running,
                "escalated": total_escalated,
            },
            "alerts": {
                "pending_escalations": total_alerts,
            },
            "in_flight": in_flight_progress,
            "projects": projects_status,
        }

    def process_one(self, project: Optional[ProjectConfig] = None) -> Optional[str]:
        """处理一个 pending 任务（同步，向后兼容）。返回 task_id 或 None。"""
        if project is None:
            if not self.projects:
                return None
            project = self.projects[0]
        return self._execute_task(project, project.db_path)

    def process_all(self) -> list[str]:
        """并行处理所有项目的所有 pending 任务。返回已启动的 task_id 列表。"""
        started = []

        # Collect all pending tasks across all projects
        all_tasks: list[tuple[ProjectConfig, dict]] = []
        for project in self.projects:
            db = self._connect_db(project.db_path)
            try:
                for task_dict in db.get_pending_tasks():
                    all_tasks.append((project, task_dict))
            finally:
                db.close()

        if not all_tasks:
            return started

        # Ensure executor is ready
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.state.max_workers,
                thread_name_prefix="agentforge",
            )

        # Submit all pending tasks
        for project, task_dict in all_tasks:
            task_id = task_dict["id"]
            if task_id in self._in_flight:
                continue  # Already running

            future = self._executor.submit(
                self._execute_task, project, project.db_path, task_dict
            )
            in_flight = InFlightTask(
                task_id=task_id,
                project_name=project.name,
                started_at=now_iso(),
                thread_id=id(future),
                future=future,
            )
            with self._lock:
                self._in_flight[task_id] = in_flight
            started.append(task_id)
            self.log.info("Dispatched: %s (project=%s, in_flight=%d)",
                          task_id, project.name, len(self._in_flight))

        return started

    def _execute_task(self, project: ProjectConfig, db_path: Path,
                      task_dict: Optional[dict] = None) -> Optional[str]:
        """在线程中执行单个任务。task_dict=None 时自动取第一个 pending。"""
        db = self._connect_db(db_path)
        try:
            if task_dict is None:
                pending = db.get_pending_tasks()
                if not pending:
                    return None
                task_dict = pending[0]

            task_id = task_dict["id"]

            with self._lock:
                self.state.current_task_id = task_id

            self.log.info("Executing: %s (project=%s)", task_id, project.name)
            self._notify("started", task_id, project.name, task_dict)

            try:
                from .engine_cli import cmd_run

                result_id = cmd_run(
                    db=db,
                    workflow_path=str(project.workflow_path),
                    task_id=task_id,
                    roles_path=str(project.roles_path) if project.roles_path else None,
                )

                with self._lock:
                    self.state.tasks_processed += 1
                    if result_id is None:
                        self.state.tasks_failed += 1

                task_after = db.get_task(task_id)
                if task_after:
                    status = task_after.get("status")
                    if status == "escalated":
                        self._record_escalation_for_task(db, task_after)
                        self._notify("escalated", task_id, project.name, task_after)
                        with self._lock:
                            self.state.escalations_detected += 1
                    elif status == "completed":
                        self._notify("completed", task_id, project.name, task_after)
                    elif status == "failed":
                        self._notify("failed", task_id, project.name, task_after)

                return result_id

            except Exception as e:
                with self._lock:
                    self.state.tasks_failed += 1
                self.log.error("Workflow error for %s: %s", task_id, e)
                db.record_escalation(
                    task_id=task_id, step_id="conductor",
                    reason=f"Workflow execution error: {e}",
                )
                self._notify("escalated", task_id, project.name,
                            {"error": str(e), "status": "failed"})
                with self._lock:
                    self.state.escalations_detected += 1
                return None
            finally:
                with self._lock:
                    self.state.current_task_id = None
                    if task_id in self._in_flight:
                        del self._in_flight[task_id]
        finally:
            db.close()

    def retry_escalated(self, task_id: str,
                        project: Optional[ProjectConfig] = None) -> Optional[str]:
        """重新执行 escalated 任务"""
        if project is None:
            project = self.projects[0] if self.projects else None
            if not project:
                return None

        db = self._connect_db(project.db_path)
        try:
            task = db.get_task(task_id)
            if not task or task.get("status") != "escalated":
                return None

            db.update_task_status(task_id, "running")
            for esc in db.get_pending_escalations():
                if esc["task_id"] == task_id:
                    db.resolve_escalation(esc["id"], "retry")

            from .engine_cli import cmd_run
            return cmd_run(
                db=db,
                workflow_path=str(project.workflow_path),
                task_id=task_id,
                roles_path=str(project.roles_path) if project.roles_path else None,
            )
        finally:
            db.close()

    # ── Internal ──

    def _connect_db(self, db_path: Path):
        db = StateDB(db_path)
        db.connect()
        return db

    def _monitor_loop(self):
        """主监控循环 — 每轮并行处理所有 pending 任务"""
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGHUP, self._handle_signal)
        except (ValueError, OSError):
            pass

        # Recover orphaned tasks from prior unclean shutdown
        self._recover_orphaned_tasks()

        while self.state.running:
            with self._lock:
                self.state.last_poll_at = now_iso()

            try:
                for project in self.projects:
                    if not self.state.running:
                        break
                    self._check_escalations(project)
                    self._check_stop_signal(project)

                # PM auto-discovery (GitHub Issues → tasks)
                if self.pm_auto_discover and self.state.running:
                    self._discover_and_submit()

                # Dispatch ALL pending tasks in parallel
                if self.state.running:
                    started = self.process_all()
                    if started:
                        self.log.info("Dispatched %d tasks (in_flight=%d, max_workers=%d)",
                                      len(started), len(self._in_flight),
                                      self.state.max_workers)
            except Exception:
                self.log.exception("Unexpected error in monitor loop")

            if self.state.running:
                time.sleep(self.poll_interval)

        # Shutdown executor
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

        # Kill agents BEFORE removing PID file
        self._kill_in_flight_agents()
        self._cleanup_pid_file()
        self.log.info("Conductor stopped (processed=%d, failed=%d, escalations=%d)",
                      self.state.tasks_processed, self.state.tasks_failed,
                      self.state.escalations_detected)

    def _check_escalations(self, project: Optional[ProjectConfig] = None):
        """检查 escalated 任务"""
        if project is None:
            if not self.projects:
                return
            project = self.projects[0]
        db = self._connect_db(project.db_path)
        try:
            for task_dict in db.get_escalated_tasks():
                self._record_escalation_for_task(db, task_dict)
        finally:
            db.close()

    def _check_stop_signal(self, project: Optional[ProjectConfig] = None):
        """检查 workflow_state 表中的 stop 信号"""
        if project is None:
            if not self.projects:
                return
            project = self.projects[0]
        db = self._connect_db(project.db_path)
        try:
            row = db.conn.execute(
                "SELECT status FROM workflow_state WHERE workflow_id = ?",
                ("conductor",)
            ).fetchone()
            if row and row[0] == "stopped":
                self.log.info("Received stop signal via workflow_state for %s", project.name)
                self.state.running = False
                # Clear the signal
                db.conn.execute(
                    "DELETE FROM workflow_state WHERE workflow_id = ?",
                    ("conductor",)
                )
                db.conn.commit()
        finally:
            db.close()

    def _record_escalation_for_task(self, db: StateDB, task_dict: dict):
        """为 escalated 任务记录升级事件"""
        task_id = task_dict["id"]
        for esc in db.get_pending_escalations():
            if esc["task_id"] == task_id:
                return

        step_id = task_dict.get("current_step", "unknown")
        rejection_count = task_dict.get("rejection_count", 0)
        reason = (
            f"Task escalated at step '{step_id}' after {rejection_count} rejections. "
            f"Human intervention required."
        )
        db.record_escalation(
            task_id=task_id, step_id=step_id, reason=reason,
            context={
                "task_type": task_dict.get("type"),
                "source": task_dict.get("source"),
                "retry_count": task_dict.get("retry_count", 0),
                "rejection_count": rejection_count,
            },
        )

    def _kill_in_flight_agents(self):
        """Kill all agent subprocesses referenced in the heartbeat table for in-flight tasks."""
        killed = 0
        with self._lock:
            in_flight_ids = list(self._in_flight.keys())
        if not in_flight_ids:
            return
        for task_id in in_flight_ids:
            try:
                for project in self.projects:
                    db = self._connect_db(project.db_path)
                    try:
                        rows = db.conn.execute(
                            "SELECT agent_pid FROM heartbeat WHERE task_id = ?",
                            (task_id,)
                        ).fetchall()
                        for (agent_pid,) in rows:
                            if agent_pid:
                                try:
                                    os.killpg(os.getpgid(agent_pid), signal.SIGTERM)
                                    killed += 1
                                except ProcessLookupError:
                                    killed += 1
                                except Exception as e:
                                    self.log.warning(
                                        "Failed to kill agent PID %d for %s: %s",
                                        agent_pid, task_id, e)
                    finally:
                        db.close()
            except Exception as e:
                self.log.warning("Failed to kill agents for %s: %s", task_id, e)
        if killed:
            self.log.info("Killed %d agent processes during stop", killed)

    def _recover_orphaned_tasks(self):
        """On startup, find tasks stuck in 'running' state and mark them failed.

        These tasks were running when the Conductor was killed. Their agent
        processes may still be alive — we kill them and clean up.
        """
        recovered = 0
        for project in self.projects:
            db = self._connect_db(project.db_path)
            try:
                running_tasks = db.conn.execute(
                    "SELECT id FROM tasks WHERE status = 'running'"
                ).fetchall()
                for (task_id,) in running_tasks:
                    # Check heartbeat — if agent still alive, kill it
                    heartbeat_rows = db.conn.execute(
                        "SELECT agent_pid FROM heartbeat "
                        "WHERE task_id = ? ORDER BY last_beat DESC LIMIT 1",
                        (task_id,)
                    ).fetchall()
                    for (agent_pid,) in heartbeat_rows:
                        if agent_pid:
                            try:
                                os.killpg(os.getpgid(agent_pid), signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            except Exception as e:
                                self.log.debug(
                                    "Failed to kill orphan agent PID %d: %s", agent_pid, e)
                    # Clean up stale data and mark task as failed
                    db.cleanup_task_data(task_id)
                    db.update_task_status(task_id, "failed")
                    self.log.info("Recovered orphaned task: %s", task_id)
                    recovered += 1
            finally:
                db.close()
        if recovered:
            self.log.info("Conductor recovered %d orphaned tasks", recovered)
        return recovered

    def _handle_signal(self, signum, frame):
        sig_name = signal.Signals(signum).name
        self.log.info("Received %s, shutting down...", sig_name)
        with self._lock:
            self.state.running = False
        if signum == signal.SIGHUP:
            self.log.info("SIGHUP received — configuration reload is deferred to next restart")
            # Future: re-read config files here

    # ── PID File ──

    def _pid_file_path(self) -> Path:
        """PID 文件路径（与第一个项目的 db 同目录）"""
        if self.state.pid_file:
            return self.state.pid_file
        if self.projects:
            return self.projects[0].db_path.parent / ".conductor.pid"
        return Path.cwd() / ".conductor.pid"

    def _write_pid_file(self):
        """写入 PID 文件，如果已有运行中的进程则拒绝"""
        pid_path = self._pid_file_path()
        self.state.pid_file = pid_path

        if pid_path.exists():
            try:
                old_pid = int(pid_path.read_text().strip())
                try:
                    os.kill(old_pid, 0)  # Check if alive
                    raise RuntimeError(
                        f"Conductor already running (PID={old_pid}). "
                        f"Stop it first or remove {pid_path}"
                    )
                except ProcessLookupError:
                    self.log.warning("Stale PID file found (PID=%d), overwriting", old_pid)
            except ValueError:
                self.log.warning("Corrupt PID file, overwriting")

        pid_path.write_text(str(os.getpid()))
        self.log.info("PID file: %s (PID=%d)", pid_path, os.getpid())

    def _cleanup_pid_file(self):
        """清理 PID 文件"""
        if self.state.pid_file and self.state.pid_file.exists():
            try:
                self.state.pid_file.unlink()
                self.log.info("PID file removed: %s", self.state.pid_file)
            except OSError:
                pass

    # ── PM Auto-Discovery ──

    def _discover_and_submit(self):
        """从 GitHub Issues 自动发现并提交需求"""
        try:
            from .pm_discover import discover_github_issues, submit_issue_as_task, mark_issue_submitted

            for project in self.projects:
                db = self._connect_db(project.db_path)
                try:
                    issues = discover_github_issues(
                        repo_path=Path.cwd(),
                        labels=self.pm_discover_labels,
                    )
                    for issue in issues:
                        task_id = submit_issue_as_task(issue, db)
                        if task_id:
                            mark_issue_submitted(Path.cwd(), issue["number"])
                            self.log.info("PM auto-discovered: GH #%d → %s",
                                         issue["number"], task_id)
                finally:
                    db.close()
        except Exception:
            self.log.debug("PM discovery skipped (gh CLI may not be available)")

    # ── Notifications ──

    def _notify(self, event: str, task_id: str, project_name: str, task_dict: dict):
        """发送通知到所有注册的 notifier"""
        for notifier in self.notifiers:
            try:
                notifier(event, task_id, project_name, task_dict)
            except Exception:
                self.log.exception("Notifier error for event=%s", event)
