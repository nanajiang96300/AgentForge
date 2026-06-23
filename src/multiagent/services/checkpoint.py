"""
Checkpoint system — save and restore task execution state.

Allows resuming failed tasks from the last successful step
instead of restarting from scratch.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from ..db import StateDB, now_iso

_log = logging.getLogger("multiagent.checkpoint")


class CheckpointManager:
    """Save and restore task execution checkpoints."""

    def __init__(self, db: StateDB):
        self.db = db

    def save(self, task_id: str, label: str = "") -> str:
        """Save current task state as a checkpoint. Returns checkpoint_id."""
        task = self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        # Capture step results
        steps = self.db.conn.execute(
            "SELECT step_id, agent, status, output, error FROM step_results "
            "WHERE task_id = ? ORDER BY id",
            (task_id,)
        ).fetchall()

        checkpoint_id = f"ckpt-{task_id}-{now_iso()[:19].replace(':', '')}"
        data = {
            "checkpoint_id": checkpoint_id,
            "task_id": task_id,
            "label": label,
            "created_at": now_iso(),
            "task_status": task["status"],
            "current_step": task.get("current_step"),
            "retry_count": task.get("retry_count", 0),
            "rejection_count": task.get("rejection_count", 0),
            "steps": [{
                "step_id": s[0], "agent": s[1], "status": s[2],
                "output": json.loads(s[3]) if s[3] else {}, "error": s[4]
            } for s in steps],
        }

        # Store in workflow_state table with checkpoint prefix
        self.db.conn.execute(
            "INSERT OR REPLACE INTO workflow_state (workflow_id, status, metadata, updated_at) "
            "VALUES (?, 'checkpoint', ?, ?)",
            (checkpoint_id, json.dumps(data), now_iso())
        )
        self.db.conn.commit()

        _log.info("Checkpoint saved: %s (%d steps)", checkpoint_id, len(steps))
        return checkpoint_id

    def restore(self, checkpoint_id: str) -> dict | None:
        """Load a checkpoint. Returns checkpoint data or None."""
        row = self.db.conn.execute(
            "SELECT metadata FROM workflow_state WHERE workflow_id = ? AND status = 'checkpoint'",
            (checkpoint_id,)
        ).fetchone()

        if not row:
            return None

        data = json.loads(row[0])
        _log.info("Checkpoint restored: %s", checkpoint_id)
        return data

    def list_checkpoints(self, task_id: str = None) -> list[dict]:
        """List checkpoints, optionally filtered by task."""
        if task_id:
            rows = self.db.conn.execute(
                "SELECT workflow_id, metadata, updated_at FROM workflow_state "
                "WHERE status = 'checkpoint' AND workflow_id LIKE ? ORDER BY updated_at DESC",
                (f"ckpt-{task_id}%",)
            ).fetchall()
        else:
            rows = self.db.conn.execute(
                "SELECT workflow_id, metadata, updated_at FROM workflow_state "
                "WHERE status = 'checkpoint' ORDER BY updated_at DESC LIMIT 50"
            ).fetchall()

        return [{
            "checkpoint_id": r[0],
            "created_at": r[2],
            "label": json.loads(r[1]).get("label", ""),
            "task_id": json.loads(r[1]).get("task_id", ""),
            "task_status": json.loads(r[1]).get("task_status", ""),
        } for r in rows]

    def delete(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint."""
        self.db.conn.execute(
            "DELETE FROM workflow_state WHERE workflow_id = ? AND status = 'checkpoint'",
            (checkpoint_id,)
        )
        self.db.conn.commit()
        return True
