"""
MetricsRepository — token/cost/duration persistence.
Delegates to StateDB for backward compatibility.
"""

from typing import Optional
from ..db import StateDB, AgentMetrics


class MetricsRepository:
    """Token/cost metrics storage, separated from task/escalation concerns."""

    def __init__(self, db: StateDB):
        self.db = db

    def record(self, metrics: AgentMetrics):
        self.db.record_metrics(metrics)

    def summary(self, agent: Optional[str] = None) -> dict:
        return self.db.get_metrics_summary(agent)

    def for_task(self, task_id: str) -> list:
        """Get all metrics for a task."""
        rows = self.db.conn.execute(
            "SELECT agent, input_tokens, output_tokens, cost_usd, duration_ms "
            "FROM agent_metrics WHERE task_id = ? ORDER BY recorded_at",
            (task_id,)
        ).fetchall()
        return [dict(zip(["agent", "input_tokens", "output_tokens", "cost_usd", "duration_ms"], r))
                for r in rows]
