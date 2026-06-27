"""
MetricsRepository — token/cost/duration persistence with inline SQL.

Owns the agent_metrics table.
"""

from typing import Optional

from ..db import StateDB, now_iso


class MetricsRepository:
    """Token/cost metrics storage, separated from task/escalation concerns."""

    def __init__(self, db: StateDB):
        self._db = db

    def record(
        self,
        task_id,
        step_id,
        agent,
        adapter,
        model,
        input_tokens,
        output_tokens,
        cost_usd,
        duration_ms,
        status,
    ):
        """Record an agent_metrics row."""
        self._db.execute_write(
            "INSERT INTO agent_metrics "
            "(task_id, step_id, agent, adapter, model, "
            "input_tokens, output_tokens, cost_usd, duration_ms, status, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id, step_id, agent, adapter, model,
                input_tokens, output_tokens, cost_usd, duration_ms,
                status, now_iso(),
            ),
        )

    def summary(self, agent: Optional[str] = None) -> dict:
        """Return aggregate metrics, optionally filtered by agent name."""
        if agent:
            row = self._db.execute(
                "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), "
                "SUM(cost_usd), AVG(duration_ms) "
                "FROM agent_metrics WHERE agent = ?",
                (agent,),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), "
                "SUM(cost_usd), AVG(duration_ms) FROM agent_metrics"
            ).fetchone()
        return {
            "total_calls": row[0] or 0,
            "total_input_tokens": row[1] or 0,
            "total_output_tokens": row[2] or 0,
            "total_cost_usd": round(row[3] or 0.0, 6),
            "avg_duration_ms": int(row[4] or 0),
        }

    def for_task(self, task_id: str) -> list[dict]:
        """Get all metrics for a task."""
        rows = self._db.execute(
            "SELECT agent, input_tokens, output_tokens, cost_usd, duration_ms "
            "FROM agent_metrics WHERE task_id = ? ORDER BY recorded_at",
            (task_id,),
        ).fetchall()
        return [
            dict(
                zip(
                    ["agent", "input_tokens", "output_tokens",
                     "cost_usd", "duration_ms"],
                    r,
                )
            )
            for r in rows
        ]

    def prune(self, days=90):
        """Delete agent_metrics older than `days`."""
        self._db.execute_write(
            "DELETE FROM agent_metrics WHERE recorded_at IS NOT NULL "
            "AND datetime(recorded_at) < datetime('now', ?)",
            (f"-{days} days",),
        )
