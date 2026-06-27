"""
WorkflowService — core workflow execution business logic.

Encapsulates the engine run loop previously in engine_cli.cmd_run().
Depends on Repository interfaces, not StateDB directly.
Uses logging, not print().
"""

import uuid
import logging
from pathlib import Path
from typing import Optional

from ..db import StateDB, Task, now_iso
from ..engine import AgentSpawner, load_yaml
from ..orchestrator import WorkflowOrchestrator
from ..core.conditions import ConditionEvaluator
from ..persistence.task_repo import TaskRepository
from ..config.loader import find_roles_yaml, find_state_db

_log = logging.getLogger("multiagent.services.workflow")


class WorkflowService:
    """Business logic for workflow execution, decoupled from CLI parsing."""

    def __init__(self):
        self._close_db = False

    # ── Public API ──────────────────────────────────────────────────────

    def execute(
        self,
        db_path: Optional[Path] = None,
        workflow_path: Optional[Path] = None,
        task_id: Optional[str] = None,
        roles_path: Optional[Path] = None,
    ) -> Optional[str]:
        """Load workflow, create/existing task, run through orchestrator.

        Args:
            db_path: Path to state.db (auto-discovered if omitted).
            workflow_path: Path to workflow YAML file.
            task_id: Existing task ID to resume (creates new if omitted).
            roles_path: Path to roles.yaml (auto-discovered if omitted).

        Returns:
            task_id on success, None on failure.
        """
        if workflow_path is None:
            _log.error("workflow_path is required")
            return None

        wf_path = Path(workflow_path) if isinstance(workflow_path, str) else workflow_path
        if not wf_path.exists():
            _log.error("Workflow file not found: %s", wf_path)
            return None

        # Load workflow to get ID
        try:
            wf_def = load_yaml(wf_path)
        except Exception as e:
            _log.error("Failed to load workflow YAML: %s", e)
            return None

        workflow_id = wf_def.get("workflow", {}).get("id", wf_path.stem)

        # Initialize DB
        db = self._resolve_db(db_path)
        if db is None:
            return None

        repo = TaskRepository(db)

        try:
            # Get or create task
            task = self._resolve_task(repo, task_id, workflow_id)
            if task is None:
                return None

            _log.info("Task: %s (workflow=%s, steps=%d)",
                      task.id, workflow_id,
                      len(wf_def.get("workflow", {}).get("steps", [])))

            # Load roles
            roles = self._load_roles(roles_path)

            # Create ConditionEvaluator and wire into orchestrator
            evaluator = ConditionEvaluator()

            spawner = AgentSpawner(db, roles)
            orchestrator = WorkflowOrchestrator(db, spawner, wf_path, evaluator=evaluator)

            # Wire notification hooks
            self._wire_hooks(orchestrator, db=db)

            # Mark task as running
            repo.update_status(task.id, "running")
            task.status = "running"

            # Run the workflow
            try:
                result = orchestrator.run(task)
            except Exception as e:
                _log.error("Workflow Error: %s", e)
                repo.update_status(task.id, "failed")
                return None

            # Log results
            for step_id, state in result["steps"].items():
                output_preview = ""
                if step_id in result.get("results", {}):
                    res = result["results"][step_id]
                    if isinstance(res, dict):
                        keys = list(res.keys())[:3]
                        output_preview = f" -> {', '.join(keys)}"
                _log.info("  %s: %s%s", step_id, state, output_preview)

            # Update task status based on final step states
            all_completed = all(
                s in ("completed", "skipped") for s in result["steps"].values()
            )
            if all_completed:
                repo.update_status(task.id, "completed")
            elif any(s == "failed" for s in result["steps"].values()):
                repo.update_status(task.id, "failed")

            return task.id

        finally:
            if self._close_db:
                db.close()

    def execute_dry_run(
        self,
        db_path: Optional[Path] = None,
        workflow_path: Optional[Path] = None,
        roles_path: Optional[Path] = None,
    ) -> list[str]:
        """Validate workflow without spawning agents.

        Returns a list of issues (empty = valid).
        """
        issues: list[str] = []

        if workflow_path is None:
            return ["workflow_path is required"]

        wf_path = Path(workflow_path) if isinstance(workflow_path, str) else workflow_path
        if not wf_path.exists():
            return [f"Workflow file not found: {wf_path}"]

        # Load workflow
        try:
            wf_def = load_yaml(wf_path)
        except Exception as e:
            return [f"Failed to load workflow YAML: {e}"]

        wf = wf_def.get("workflow", {})
        workflow_id = wf.get("id", wf_path.stem)
        steps = wf.get("steps", [])

        if not steps:
            issues.append("Workflow has no steps defined")

        # Validate each step
        step_ids = set()
        for i, step in enumerate(steps):
            sid = step.get("id", f"step_{i}")
            if sid in step_ids:
                issues.append(f"Duplicate step id: {sid}")
            step_ids.add(sid)

            if not step.get("agent"):
                issues.append(f"Step '{sid}' has no agent defined")

            # Check depends_on references
            deps = step.get("depends_on", [])
            if isinstance(deps, str):
                deps = [deps]
            for dep in deps:
                if dep not in step_ids and dep != sid:
                    issues.append(f"Step '{sid}' depends on unknown step '{dep}'")

        # Load roles for spawner validation
        roles = self._load_roles(roles_path)

        # Validate with orchestrator
        db = self._resolve_db(db_path)
        if db is not None:
            try:
                evaluator = ConditionEvaluator()
                spawner = AgentSpawner(db, roles)
                orchestrator = WorkflowOrchestrator(db, spawner, wf_path, evaluator=evaluator)
                try:
                    orchestrator.load()
                except Exception as e:
                    issues.append(f"Orchestrator load failed: {e}")
            finally:
                if self._close_db:
                    db.close()

        if not issues:
            _log.info("Dry-run passed for workflow '%s' (%d steps)", workflow_id, len(steps))

        return issues

    # ── Internal helpers ───────────────────────────────────────────────

    def _resolve_db(self, db_path: Optional[Path] = None) -> Optional[StateDB]:
        """Open state.db, auto-discovering if not specified."""
        if db_path is None:
            db_path = find_state_db()
        elif isinstance(db_path, str):
            db_path = Path(db_path)

        try:
            db = StateDB(db_path)
            db.connect()
            self._close_db = True
            return db
        except Exception as e:
            _log.error("Failed to open state DB at %s: %s", db_path, e)
            return None

    def _resolve_task(
        self,
        repo: TaskRepository,
        task_id: Optional[str],
        workflow_id: str,
    ) -> Optional[Task]:
        """Get existing task or create a new one."""
        if task_id:
            task_data = repo.get_task(task_id)
            if task_data is None:
                _log.error("Task not found: %s", task_id)
                return None
            task = Task(
                id=task_data["id"],
                type=task_data.get("type", "unknown"),
                source=task_data.get("source"),
                workflow_id=task_data.get("workflow_id", workflow_id),
                current_step=task_data.get("current_step"),
                status=task_data.get("status", "pending"),
                retry_count=task_data.get("retry_count", 0),
                rejection_count=task_data.get("rejection_count", 0),
                dedup_key=task_data.get("dedup_key"),
                context=task_data.get("context"),
                created_at=task_data.get("created_at"),
                claimed_at=task_data.get("claimed_at"),
                completed_at=task_data.get("completed_at"),
            )
            _log.info("Using existing task: %s", task.id)
            return task

        # Create new task
        new_id = f"task-{uuid.uuid4().hex[:8]}"
        task = Task(
            id=new_id,
            type="manual",
            source="cli",
            workflow_id=workflow_id,
            current_step=None,
            status="pending",
            context={},
            created_at=now_iso(),
        )
        if not repo.insert(task):
            _log.error("Failed to create task (duplicate key?)")
            return None
        _log.info("Created new task: %s", new_id)
        return task

    def _load_roles(self, roles_path: Optional[Path] = None) -> dict:
        """Load roles configuration from YAML."""
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        if roles_path:
            rp = Path(roles_path) if isinstance(roles_path, str) else roles_path
            if rp.exists():
                roles = load_yaml(rp)
        else:
            found = find_roles_yaml()
            if found:
                roles = load_yaml(found)
        return roles

    @staticmethod
    def _wire_hooks(orchestrator, db=None):
        """Register notification hooks."""
        try:
            from ..notify import create_notifier, NotifierStepHook
            import os

            lang = os.environ.get("AGENTFORGE_LANG", "")
            if not lang:
                try:
                    from ..notify import _load_claudeclaw_config
                    lang = _load_claudeclaw_config().get("language", "")
                except Exception:
                    pass
            if lang:
                from ..notify import set_language
                set_language(lang)

            notifiers = create_notifier()
            if notifiers:
                hook = NotifierStepHook(notifiers, db=db)
                orchestrator.register_hook(hook)
                _log.info("Hooks wired: %d notifier(s)", len(notifiers))
        except Exception:
            pass
