"""
TaskRepository — task lifecycle persistence.

Delegates to StateDB for backward compatibility.
New code should use this instead of StateDB directly.
"""

from typing import Optional
from ..db import StateDB, Task, now_iso


class TaskRepository:
    """Task CRUD operations, separated from metrics/escalation concerns."""

    def __init__(self, db: StateDB):
        self.db = db

    def insert(self, task: Task) -> bool:
        return self.db.insert_task(task)

    def get(self, task_id: str) -> Optional[dict]:
        return self.db.get_task(task_id)

    def get_pending(self) -> list[dict]:
        return self.db.get_pending_tasks()

    def get_escalated(self) -> list[dict]:
        return self.db.get_escalated_tasks()

    def get_running(self) -> list[Task]:
        return self.db.get_running_tasks()

    def claim(self, workflow_id: str) -> Optional[Task]:
        return self.db.claim_pending_task(workflow_id)

    def update_status(self, task_id: str, status: str, step: str = None):
        self.db.update_task_status(task_id, status, step)

    def increment_retry(self, task_id: str) -> int:
        return self.db.increment_retry(task_id)

    def increment_rejection(self, task_id: str) -> int:
        return self.db.increment_rejection(task_id)

    def set_context(self, task_id: str, context: dict):
        self.db.set_task_context(task_id, context)

    def record_step(self, task_id, step_id, agent, status, output=None,
                    error=None, retry_count=0, started_at=None,
                    completed_at=None, adapter_name="claude-code"):
        self.db.record_step(task_id, step_id, agent, status, output=output,
                           error=error, retry_count=retry_count,
                           started_at=started_at, completed_at=completed_at,
                           adapter_name=adapter_name)

    def heartbeat(self, task_id, step_id, agent_pid):
        self.db.heartbeat(task_id, step_id, agent_pid)

    def get_lost_agents(self, timeout=60) -> list[dict]:
        return self.db.get_lost_agents(timeout)
