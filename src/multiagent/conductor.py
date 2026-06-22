"""
Conductor — Phase 4 调度循环。监控 state.db，自动触发工作流，处理升级。

职责:
  - 轮询 state.db 发现 pending 任务 → 自动执行 workflow
  - 监控 escalated 任务 → 记录到 escalations 表
  - 支持继续 escalated 任务（human retry）
  - 状态查询

用法:
    from multiagent.conductor import Conductor
    c = Conductor(db_path, workflow_path, roles_path)
    c.start()   # 启动监控循环（阻塞）
    c.status()  # 查询当前状态
"""

import json
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .db import StateDB, Task, now_iso

_DEFAULT_POLL_INTERVAL = 5  # seconds


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


class Conductor:
    """Phase 4 调度器：自动监控 → 触发 → 升级通知"""

    def __init__(
        self,
        db_path: Path,
        workflow_path: Path,
        roles_path: Optional[Path] = None,
        poll_interval: int = _DEFAULT_POLL_INTERVAL,
    ):
        self.db_path = Path(db_path)
        self.workflow_path = Path(workflow_path)
        self.roles_path = Path(roles_path) if roles_path else None
        self.poll_interval = poll_interval
        self.state = ConductorState()
        self._lock = threading.Lock()
        self._tasks_in_flight: dict[str, threading.Thread] = {}

    # ── Public API ──

    def start(self, blocking: bool = True):
        """启动监控循环。blocking=True 时阻塞当前线程。"""
        if self.state.running:
            return

        self.state.running = True
        self.state.started_at = now_iso()

        if blocking:
            self._monitor_loop()
        else:
            t = threading.Thread(target=self._monitor_loop, daemon=True)
            t.start()

    def stop(self):
        """停止监控循环"""
        self.state.running = False

    def status(self) -> dict:
        """返回 Conductor 运行时状态和摘要"""
        db = self._connect_db()
        try:
            pending = db.get_pending_tasks()
            escalated = db.get_escalated_tasks()
            pend_esc = db.get_pending_escalations()

            # Count running tasks
            rows = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'running'"
            ).fetchone()
            running_count = rows[0] if rows else 0

            return {
                "conductor": {
                    "running": self.state.running,
                    "started_at": self.state.started_at,
                    "last_poll_at": self.state.last_poll_at,
                    "poll_interval_s": self.poll_interval,
                    "tasks_processed": self.state.tasks_processed,
                    "tasks_failed": self.state.tasks_failed,
                    "escalations_detected": self.state.escalations_detected,
                    "current_task_id": self.state.current_task_id,
                    "tasks_in_flight": len(self._tasks_in_flight),
                },
                "queue": {
                    "pending": len(pending),
                    "running": running_count,
                    "escalated": len(escalated),
                },
                "alerts": {
                    "pending_escalations": len(pend_esc),
                },
                "pending_task_ids": [t["id"] for t in pending],
                "escalated_task_ids": [t["id"] for t in escalated],
            }
        finally:
            db.close()

    def process_one(self) -> Optional[str]:
        """处理一个 pending 任务（非阻塞，供外部调用）。返回 task_id 或 None。"""
        db = self._connect_db()
        try:
            pending = db.get_pending_tasks()
            if not pending:
                return None

            task_dict = pending[0]
            task_id = task_dict["id"]

            with self._lock:
                self.state.current_task_id = task_id

            try:
                from .engine_cli import cmd_run

                result_id = cmd_run(
                    db=db,
                    workflow_path=str(self.workflow_path),
                    task_id=task_id,
                    roles_path=str(self.roles_path) if self.roles_path else None,
                )

                with self._lock:
                    self.state.tasks_processed += 1
                    if result_id is None:
                        self.state.tasks_failed += 1

                # Check if task was escalated
                task_after = db.get_task(task_id)
                if task_after and task_after.get("status") == "escalated":
                    self._record_escalation_for_task(db, task_after)
                    with self._lock:
                        self.state.escalations_detected += 1

                return result_id

            except Exception as e:
                with self._lock:
                    self.state.tasks_failed += 1
                db.record_escalation(
                    task_id=task_id,
                    step_id="conductor",
                    reason=f"Workflow execution error: {e}",
                )
                with self._lock:
                    self.state.escalations_detected += 1
                return None
            finally:
                with self._lock:
                    self.state.current_task_id = None
        finally:
            db.close()

    def retry_escalated(self, task_id: str) -> Optional[str]:
        """重新执行 escalated 任务（human 确认后调用）"""
        db = self._connect_db()
        try:
            task = db.get_task(task_id)
            if not task:
                return None
            if task.get("status") != "escalated":
                return None

            # Reset status to running so orchestrator picks it up
            db.update_task_status(task_id, "running")

            # Resolve pending escalations for this task
            for esc in db.get_pending_escalations():
                if esc["task_id"] == task_id:
                    db.resolve_escalation(esc["id"], "retry")

            from .engine_cli import cmd_run

            return cmd_run(
                db=db,
                workflow_path=str(self.workflow_path),
                task_id=task_id,
                roles_path=str(self.roles_path) if self.roles_path else None,
            )
        finally:
            db.close()

    # ── Internal ──

    def _connect_db(self):
        db = StateDB(self.db_path)
        db.connect()
        return db

    def _monitor_loop(self):
        """主监控循环"""
        # Setup signal handling for graceful shutdown
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, OSError):
            pass  # Not in main thread

        while self.state.running:
            with self._lock:
                self.state.last_poll_at = now_iso()

            try:
                # Check for escalations on running tasks
                self._check_escalations()

                # Process one pending task per poll cycle
                self.process_one()

            except Exception:
                pass  # Don't crash the loop on transient errors

            time.sleep(self.poll_interval)

    def _check_escalations(self):
        """检查 escalated 任务并记录到 escalations 表"""
        db = self._connect_db()
        try:
            escalated = db.get_escalated_tasks()
            for task_dict in escalated:
                self._record_escalation_for_task(db, task_dict)
        finally:
            db.close()

    def _record_escalation_for_task(self, db: StateDB, task_dict: dict):
        """为 escalated 任务记录升级事件（如尚未记录）"""
        task_id = task_dict["id"]
        # Check if there's already a pending escalation for this task
        existing = db.get_pending_escalations()
        for esc in existing:
            if esc["task_id"] == task_id:
                return  # Already recorded

        step_id = task_dict.get("current_step", "unknown")
        rejection_count = task_dict.get("rejection_count", 0)
        reason = (
            f"Task escalated at step '{step_id}' after {rejection_count} rejections. "
            f"Human intervention required."
        )
        db.record_escalation(
            task_id=task_id,
            step_id=step_id,
            reason=reason,
            context={
                "task_type": task_dict.get("type"),
                "source": task_dict.get("source"),
                "retry_count": task_dict.get("retry_count", 0),
                "rejection_count": rejection_count,
            },
        )

    def _handle_signal(self, signum, frame):
        """信号处理：优雅停止"""
        self.state.running = False
