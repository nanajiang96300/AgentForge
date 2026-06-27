"""
DashboardService — data aggregation for Web dashboard and CLI status.

Depends on Repository interfaces, not StateDB directly.
Uses logging, not print().
"""

import json
import logging
from pathlib import Path
from typing import Optional

from ..persistence.task_repo import TaskRepository
from ..persistence.metrics_repo import MetricsRepository
from ..persistence.escalation_repo import EscalationRepository
from ..core.progress import calculate_task_progress, progress_bar
from ..core.graph_engine import WorkflowGraph
from ..engine import load_yaml
from ..config.loader import find_workflow_yaml

_log = logging.getLogger("multiagent.services.dashboard")


class DashboardService:
    """Data aggregation for both Web dashboard and CLI conductor status."""

    def __init__(
        self,
        task_repo: TaskRepository,
        metrics_repo: MetricsRepository,
        escalation_repo: EscalationRepository,
    ):
        self._task_repo = task_repo
        self._metrics_repo = metrics_repo
        self._escalation_repo = escalation_repo

    # ── Public API ──────────────────────────────────────────────────────

    def queue_summary(self) -> dict:
        """Return aggregate queue counts across all statuses.

        Returns:
            {pending_count, running_count, escalated_count, alerts_count,
             completed_count, failed_count}
        """
        pending = self._task_repo.get_pending()
        escalated = self._task_repo.get_escalated()
        running = self._task_repo.get_running()
        alerts = self._escalation_repo.get_pending()

        return {
            "pending_count": len(pending),
            "running_count": len(running),
            "escalated_count": len(escalated),
            "alerts_count": len(alerts),
            "completed_count": 0,  # Populated by caller if needed
            "failed_count": 0,
        }

    def task_progress(self, task_id: str) -> dict:
        """Calculate fine-grained progress for a single task.

        Returns:
            {pct, stage, subtasks_done, subtasks_total, bar, status,
             current_step, input_tokens, output_tokens, cost_usd, duration_ms}
        """
        result: dict = {
            "pct": 0,
            "stage": "pending",
            "bar": progress_bar(0),
            "subtasks_done": 0,
            "subtasks_total": 0,
            "status": "pending",
            "current_step": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "duration_ms": 0,
        }

        # Task-level data
        task_data = self._task_repo.get_task(task_id)
        if task_data:
            result["status"] = task_data.get("status", "pending")
            result["current_step"] = task_data.get("current_step")

        # Progress from step_results (needs raw db connection)
        # NOTE: calculate_task_progress still accesses db.conn directly.
        # In a full refactor this would move into the TaskRepository.
        # For now we delegate to the existing function.
        try:
            p = calculate_task_progress(self._task_repo._db, task_id)
            result["pct"] = p.get("pct", 0)
            result["stage"] = p.get("stage", "pending")
            result["subtasks_done"] = p.get("subtasks_done", 0)
            result["subtasks_total"] = p.get("subtasks_total", 0)
            result["bar"] = p.get("bar", progress_bar(0))
            result["completed_steps"] = p.get("completed_steps", 0)
            result["total_steps"] = p.get("total_steps", 0)
        except Exception:
            _log.debug("Progress calculation failed for %s", task_id, exc_info=True)

        # Metrics aggregation
        try:
            metrics = self._metrics_repo.for_task(task_id)
            result["input_tokens"] = sum(m.get("input_tokens", 0) for m in metrics)
            result["output_tokens"] = sum(m.get("output_tokens", 0) for m in metrics)
            result["cost_usd"] = sum(m.get("cost_usd", 0) for m in metrics)
            result["duration_ms"] = sum(m.get("duration_ms", 0) for m in metrics)
        except Exception:
            _log.debug("Metrics aggregation failed for %s", task_id, exc_info=True)

        return result

    def timeseries(self, days: int = 7) -> dict:
        """Return token and pass-rate trends over the given number of days.

        Returns:
            {token_trend: [{date, tokens, cost, calls}],
             pass_rate: [{date, total, passed, rate}]}
        """
        # Token/cost trend from agent_metrics
        # NOTE: Direct SQL is used here since the MetricsRepository doesn't
        # expose timeseries queries. This is a bridge until that's added.
        token_trend: list[dict] = []
        pass_rate: list[dict] = []

        try:
            db = self._metrics_repo._db
            token_rows = db.execute(
                """SELECT date(recorded_at) as day,
                          SUM(input_tokens + output_tokens) as tokens,
                          SUM(cost_usd) as cost, COUNT(*) as calls
                   FROM agent_metrics
                   WHERE recorded_at IS NOT NULL
                     AND date(recorded_at) >= date('now', ?)
                   GROUP BY date(recorded_at) ORDER BY day ASC""",
                (f"-{days} days",),
            ).fetchall()
            token_trend = [
                {
                    "date": r[0], "tokens": r[1] or 0,
                    "cost": round(r[2] or 0, 4), "calls": r[3] or 0,
                }
                for r in token_rows
            ]

            # Pass-rate from tasks (completed_at)
            task_rows = db.execute(
                """SELECT date(completed_at) as day,
                          COUNT(*) as total,
                          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as passed
                   FROM tasks
                   WHERE completed_at IS NOT NULL
                     AND date(completed_at) >= date('now', ?)
                   GROUP BY date(completed_at) ORDER BY day ASC""",
                (f"-{days} days",),
            ).fetchall()
            pass_rate = [
                {
                    "date": r[0], "total": r[1] or 0, "passed": r[2] or 0,
                    "rate": round((r[2] or 0) * 100 / max(r[1] or 1, 1), 1),
                }
                for r in task_rows
            ]
        except Exception as e:
            _log.warning("Timeseries query failed: %s", e)

        return {
            "token_trend": token_trend,
            "pass_rate": pass_rate,
        }

    def workflow_dag(self, workflow_path: Optional[Path] = None) -> dict:
        """Return nodes and edges for the Designer visualization.

        Args:
            workflow_path: Path to workflow YAML. Auto-discovered if omitted.

        Returns:
            {nodes: [{id, agent, status}], edges: [{source, target, label}],
             workflow_id: str}
        """
        if workflow_path is None:
            wf_path = find_workflow_yaml()
        else:
            wf_path = Path(workflow_path) if isinstance(workflow_path, str) else workflow_path

        if not wf_path or not wf_path.exists():
            return {"nodes": [], "edges": [], "workflow_id": "", "error": "No workflow found"}

        try:
            wf_def = load_yaml(wf_path)
        except Exception as e:
            return {"nodes": [], "edges": [], "workflow_id": "", "error": str(e)}

        steps = wf_def.get("workflow", {}).get("steps", [])
        wf_id = wf_def.get("workflow", {}).get("id", wf_path.stem)

        nodes = []
        edges = []
        for step in steps:
            sid = step["id"]
            agent = step.get("agent", "?")
            nodes.append({"id": sid, "agent": agent, "status": "pending"})
            deps = step.get("depends_on", [])
            if isinstance(deps, str):
                deps = [deps]
            for dep in deps:
                edges.append({"source": dep, "target": sid, "label": ""})

        # Enrich nodes with step status from the currently running task
        try:
            running_tasks = self._task_repo.get_running()
            if running_tasks:
                task_id = running_tasks[0].id
                steps_data = self._task_repo.get_step_results(task_id)
                seen = set()
                for sr in steps_data:
                    if sr["step_id"] not in seen:
                        seen.add(sr["step_id"])
                        for node in nodes:
                            if node["id"] == sr["step_id"]:
                                node["status"] = sr["status"]
        except Exception as e:
            _log.debug("Failed to enrich DAG with running task status: %s", e)

        return {
            "nodes": nodes,
            "edges": edges,
            "workflow_id": wf_id,
        }
