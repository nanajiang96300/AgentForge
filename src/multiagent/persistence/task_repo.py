"""
TaskRepository — task lifecycle persistence with inline SQL.

Owns tasks, step_results, and heartbeat tables.
"""

import json
from typing import Optional

from ..db import StateDB, Task, now_iso


class TaskRepository:
    """Task CRUD operations, separated from metrics/escalation concerns."""

    _TASK_COLS = (
        "id", "type", "source", "workflow_id", "current_step", "status",
        "retry_count", "rejection_count", "dedup_key", "context",
        "created_at", "claimed_at", "completed_at",
    )

    def __init__(self, db: StateDB):
        self._db = db

    # ── Queries ──────────────────────────────────────────────────────────

    def get_pending(self) -> list[dict]:
        """Return all pending tasks as dicts."""
        rows = self._db.execute(
            "SELECT id, type, source, workflow_id, current_step, status, "
            "retry_count, rejection_count, dedup_key, context, "
            "created_at, claimed_at, completed_at "
            "FROM tasks WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        return [dict(zip(self._TASK_COLS, r)) for r in rows]

    def get_task(self, task_id: str) -> Optional[dict]:
        """Get a single task by id using PRAGMA table_info for column names."""
        row = self._db.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        cols = [d[1] for d in
                self._db.execute("PRAGMA table_info(tasks)").fetchall()]
        return dict(zip(cols, row))

    def get_escalated(self) -> list[dict]:
        """Return all escalated tasks as dicts."""
        rows = self._db.execute(
            "SELECT id, type, source, workflow_id, current_step, status, "
            "retry_count, rejection_count, dedup_key, context, "
            "created_at, claimed_at, completed_at "
            "FROM tasks WHERE status = 'escalated' ORDER BY created_at"
        ).fetchall()
        return [dict(zip(self._TASK_COLS, r)) for r in rows]

    def get_running(self) -> list[Task]:
        """Return all running tasks as Task objects."""
        rows = self._db.execute(
            "SELECT id, type, source, workflow_id, current_step, status, "
            "retry_count, rejection_count, dedup_key, context, "
            "created_at, claimed_at, completed_at "
            "FROM tasks WHERE status = 'running'"
        ).fetchall()
        result: list[Task] = []
        for r in rows:
            ctx = r[9]
            if isinstance(ctx, str):
                try:
                    ctx = json.loads(ctx)
                except json.JSONDecodeError:
                    pass
            result.append(Task(
                id=r[0], type=r[1], source=r[2], workflow_id=r[3],
                current_step=r[4], status=r[5], retry_count=r[6],
                rejection_count=r[7], dedup_key=r[8], context=ctx,
                created_at=r[10], claimed_at=r[11], completed_at=r[12],
            ))
        return result

    # ── Mutations ────────────────────────────────────────────────────────

    def insert(self, task: Task) -> bool:
        """Insert a new task. Returns True on success, False on duplicate."""
        try:
            self._db.execute_write(
                "INSERT INTO tasks (id, type, source, workflow_id, current_step, "
                "status, retry_count, rejection_count, dedup_key, context, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task.id, task.type, task.source, task.workflow_id,
                    task.current_step, task.status, task.retry_count,
                    task.rejection_count, task.dedup_key,
                    json.dumps(task.context or {}),
                    task.created_at or now_iso(),
                ),
            )
            return True
        except Exception:
            return False

    def update_status(self, task_id: str, status: str, step: Optional[str] = None):
        """Update task status with guard: never downgrade from terminal."""
        cur = self._db.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        )
        row = cur.fetchone()
        current_status = row[0] if row else None
        if current_status in ("completed", "failed", "escalated") and \
           status not in ("completed", "failed", "escalated"):
            return  # Refuse to overwrite terminal status with non-terminal

        fields = ["status = ?"]
        params: list = [status]
        if step is not None:
            fields.append("current_step = ?")
            params.append(step)
        if status == "completed":
            fields.append("completed_at = ?")
            params.append(now_iso())
        params.append(task_id)
        self._db.execute_write(
            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )

    def claim_pending(self, workflow_id: str) -> Optional[Task]:
        """Claim the oldest pending task for a workflow and return it as Task."""
        row = self._db.execute(
            "SELECT id, type, source, workflow_id, current_step, status, "
            "retry_count, rejection_count, dedup_key, context, "
            "created_at, claimed_at, completed_at "
            "FROM tasks WHERE workflow_id = ? AND status = 'pending' "
            "ORDER BY created_at LIMIT 1",
            (workflow_id,),
        ).fetchone()
        if row is None:
            return None
        self._db.execute_write(
            "UPDATE tasks SET status = 'running', claimed_at = ? WHERE id = ?",
            (now_iso(), row[0]),
        )
        ctx = row[9]
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except json.JSONDecodeError:
                pass
        return Task(
            id=row[0], type=row[1], source=row[2], workflow_id=row[3],
            current_step=row[4], status=row[5], retry_count=row[6],
            rejection_count=row[7], dedup_key=row[8], context=ctx,
            created_at=row[10], claimed_at=row[11], completed_at=row[12],
        )

    def increment_retry(self, task_id: str) -> int:
        """Increment retry_count and return the new value."""
        self._db.execute_write(
            "UPDATE tasks SET retry_count = retry_count + 1 "
            "WHERE id = ?",
            (task_id,),
        )
        row = self._db.execute(
            "SELECT retry_count FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return row[0] if row else 0

    def increment_rejection(self, task_id: str) -> int:
        """Increment rejection_count and return the new value."""
        self._db.execute_write(
            "UPDATE tasks SET rejection_count = rejection_count + 1 "
            "WHERE id = ?",
            (task_id,),
        )
        row = self._db.execute(
            "SELECT rejection_count FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return row[0] if row else 0

    def set_context(self, task_id: str, context: dict):
        """Update the context JSON blob for a task."""
        self._db.execute_write(
            "UPDATE tasks SET context = ? WHERE id = ?",
            (json.dumps(context), task_id),
        )

    # ── Search ───────────────────────────────────────────────────────────

    def search(self, keyword: str, status: Optional[str] = None) -> list[dict]:
        """Search tasks by keyword in context, optionally filtered by status."""
        query = (
            "SELECT id, type, source, workflow_id, current_step, status, "
            "retry_count, rejection_count, dedup_key, context, "
            "created_at, claimed_at, completed_at "
            "FROM tasks WHERE context LIKE ?"
        )
        params: list = [f"%{keyword}%"]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = self._db.execute(query, tuple(params)).fetchall()
        results = []
        for r in rows:
            d = dict(zip(self._TASK_COLS, r))
            ctx = d.get("context")
            if isinstance(ctx, str):
                try:
                    parsed = json.loads(ctx)
                except json.JSONDecodeError:
                    parsed = {}
                d["context"] = parsed
                d["context_parsed"] = parsed
            elif isinstance(ctx, dict):
                d["context_parsed"] = ctx
            results.append(d)
        return results

    # ── Step results ─────────────────────────────────────────────────────

    def record_step(
        self,
        task_id,
        step_id,
        agent,
        status,
        output=None,
        error=None,
        retry_count=0,
        started_at=None,
        completed_at=None,
        adapter_name="claude-code",
    ):
        """Insert a step result row."""
        self._db.execute_write(
            "INSERT INTO step_results (task_id, step_id, agent, adapter, status, "
            "output, error, retry_count, started_at, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                task_id, step_id, agent, adapter_name, status,
                json.dumps(output or {}), error, retry_count,
                started_at, completed_at,
            ),
        )

    def get_step_results(self, task_id: str) -> list[dict]:
        """Return all step results for a task ordered by id."""
        rows = self._db.execute(
            "SELECT id, task_id, step_id, agent, adapter, status, output, error, "
            "retry_count, started_at, completed_at "
            "FROM step_results WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        cols = [
            "id", "task_id", "step_id", "agent", "adapter", "status",
            "output", "error", "retry_count", "started_at", "completed_at",
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_last_output(self, task_id: str, step_id: str) -> dict:
        """Return the parsed output dict of the last completed step result."""
        row = self._db.execute(
            "SELECT output FROM step_results "
            "WHERE task_id = ? AND step_id = ? AND status = 'completed' "
            "ORDER BY id DESC LIMIT 1",
            (task_id, step_id),
        ).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return {}

    # ── Heartbeat ────────────────────────────────────────────────────────

    def heartbeat(self, task_id, step_id, agent_pid):
        """Record or refresh a heartbeat for a running agent."""
        self._db.execute_write(
            "INSERT OR REPLACE INTO heartbeat "
            "(task_id, step_id, agent_pid, last_beat) VALUES (?,?,?,?)",
            (task_id, step_id, agent_pid, now_iso()),
        )

    def get_lost_agents(self, heartbeat_timeout=60) -> list[dict]:
        """Return heartbeat entries older than heartbeat_timeout seconds."""
        rows = self._db.execute(
            "SELECT task_id, step_id, agent_pid, last_beat FROM heartbeat "
            "WHERE datetime(last_beat) < datetime('now', ?)",
            (f"-{heartbeat_timeout} seconds",),
        ).fetchall()
        return [
            {
                "task_id": r[0],
                "step_id": r[1],
                "agent_pid": r[2],
                "last_beat": r[3],
            }
            for r in rows
        ]

    # ── Cleanup & retention ──────────────────────────────────────────────

    def cleanup_task_data(self, task_id: str):
        """Remove step_results, agent_metrics, and heartbeat for a task."""
        for table in ("step_results", "agent_metrics", "heartbeat"):
            self._db.execute_write(
                f"DELETE FROM {table} WHERE task_id = ?", (task_id,),
            )

    def prune_step_results(self, days=30):
        """Delete step_results older than `days`."""
        self._db.execute_write(
            "DELETE FROM step_results WHERE completed_at IS NOT NULL "
            "AND datetime(completed_at) < datetime('now', ?)",
            (f"-{days} days",),
        )

    def prune_heartbeat(self, days=7):
        """Delete heartbeat rows older than `days`."""
        self._db.execute_write(
            "DELETE FROM heartbeat "
            "WHERE datetime(last_beat) < datetime('now', ?)",
            (f"-{days} days",),
        )
