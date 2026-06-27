"""
EscalationRepository — escalation event persistence with inline SQL.

Owns the escalations table.
"""

import json
from typing import Optional

from ..db import StateDB, now_iso


class EscalationRepository:
    """Escalation event storage, separated from task/metrics concerns."""

    def __init__(self, db: StateDB):
        self._db = db

    def record(
        self,
        task_id: str,
        step_id: str,
        reason: str,
        context: Optional[dict] = None,
    ) -> int:
        """Record an escalation event and return its id."""
        cur = self._db.execute_write(
            "INSERT INTO escalations "
            "(task_id, step_id, reason, context, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (task_id, step_id, reason, json.dumps(context or {}), now_iso()),
        )
        return cur.lastrowid

    def get_pending(self) -> list[dict]:
        """Return all unresolved (pending) escalations."""
        rows = self._db.execute(
            "SELECT id, task_id, step_id, reason, context, status, created_at "
            "FROM escalations WHERE status = 'pending' ORDER BY created_at",
        ).fetchall()
        cols = [
            "id", "task_id", "step_id", "reason", "context",
            "status", "created_at",
        ]
        return [dict(zip(cols, r)) for r in rows]

    def resolve(self, escalation_id: int, resolution: str) -> bool:
        """Mark an escalation as resolved with a resolution note."""
        self._db.execute_write(
            "UPDATE escalations "
            "SET status = 'resolved', resolution = ?, resolved_at = ? "
            "WHERE id = ?",
            (resolution, now_iso(), escalation_id),
        )
        return True
