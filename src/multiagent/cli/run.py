"""
CLI for run — workflow execution commands.

Usage:
    multiagent run <workflow.yaml>                Run a workflow
    multiagent run <workflow.yaml> --task-id <id> Resume existing task
    multiagent run <workflow.yaml> --dry-run       Validate only
"""

import sys
import argparse
import uuid
from pathlib import Path

from ..services.workflow_service import WorkflowService
from ..services import _resolve_db
from ..persistence.task_repo import TaskRepository
from ..engine import load_yaml
from ..config.loader import find_roles_yaml
from ..db import Task, now_iso


def _wire_hooks(orchestrator, db=None):
    """Register notification hooks. Deprecated — handled by WorkflowService."""
    pass


def _close_db_on_exit(db_obj, close_flag):
    """Close DB if we opened it."""
    if close_flag and db_obj is not None:
        try:
            db_obj.close()
        except Exception:
            pass


def parse_run_args(argv=None):
    """Parse multiagent run command arguments, returns dict."""
    if argv is None:
        argv = sys.argv[1:]

    # Strip 'run' subcommand if present (from 'multiagent run <args>')
    if argv and argv[0] == "run":
        argv = argv[1:]

    parser = argparse.ArgumentParser(
        prog="multiagent run",
        description="Run a workflow through the MultiAgent Engine",
    )
    parser.add_argument(
        "workflow",
        help="Path to workflow YAML file",
    )
    parser.add_argument(
        "--task-id",
        default=None,
        help="Existing task ID to run (creates new task if omitted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate workflow without spawning agents",
    )
    parser.add_argument(
        "--roles",
        default=None,
        help="Path to roles.yaml (auto-detected if omitted)",
    )

    # parse_known_args so we don't conflict with parent parser
    args, _ = parser.parse_known_args(argv)
    return {
        "workflow": args.workflow,
        "task_id": args.task_id,
        "dry_run": args.dry_run,
        "roles": args.roles,
    }




def cmd_run(db=None, workflow_path=None, task_id=None, dry_run=False, roles_path=None):
    """
    Execute workflow. Returns task_id or None (on failure).

    Signature matches old engine_cli.cmd_run for backward compatibility.
    The `db` parameter is accepted for backward compat; if provided, it is
    used directly. Otherwise a connection is obtained via _resolve_db.

    Parameters can be passed directly (programmatic call) or from sys.argv.
    """
    # Resolve paths
    wf_path = Path(workflow_path) if workflow_path else None
    if wf_path is None:
        return None

    if not wf_path.exists():
        print(f"Error: Workflow file not found: {wf_path}")
        return None

    # Load workflow to get ID for display
    try:
        wf_def = load_yaml(wf_path)
    except Exception as e:
        print(f"Error: Failed to load workflow YAML: {e}")
        return None

    workflow_id = wf_def.get("workflow", {}).get("id", wf_path.stem)
    steps = wf_def.get("workflow", {}).get("steps", [])
    steps_count = len(steps)

    # Resolve DB connection
    _close_db = False
    if db is None:
        db_obj = _resolve_db()
        _close_db = True
    else:
        db_obj = db

    repo = TaskRepository(db_obj)

    # ── Dry-run mode ──
    if dry_run:
        try:
            # If task_id provided, validate it exists
            if task_id:
                task_data = repo.get_task(task_id)
                if task_data is None:
                    print(f"Error: Task not found: {task_id}")
                    return None
                print(f"Task: {task_id} (existing)")
            else:
                # Create a new task for dry-run tracking
                task_id = f"task-{uuid.uuid4().hex[:8]}"
                task = Task(
                    id=task_id,
                    type="manual",
                    source="cli",
                    workflow_id=workflow_id,
                    current_step=None,
                    status="pending",
                    context={},
                    created_at=now_iso(),
                )
                if not repo.insert(task):
                    print("Error: Failed to create task")
                    return None
                print(f"Task: {task_id} (new)")

            print(f"Workflow: {workflow_id}")
            print(f"Steps: {steps_count}")

            # Validate via WorkflowService
            svc = WorkflowService()
            issues = svc.execute_dry_run(
                workflow_path=workflow_path,
                roles_path=roles_path,
            )

            print("\n[Dry-run mode — validating workflow only]")
            for step in steps:
                deps = step.get("depends_on", [])
                dep_str = f" (depends: {', '.join(deps)})" if deps else ""
                print(f"  • {step['id']} [{step.get('agent', '?')}]{dep_str}")
                if step.get("output", {}).get("required"):
                    print(f"      required output: {', '.join(step['output']['required'])}")

            if issues:
                for issue in issues:
                    print(f"  Error: {issue}")
                return None

            # Mark task as completed and record dry-run step results
            repo.update_status(task_id, "completed")
            ts = now_iso()
            for step in steps:
                repo.record_step(
                    task_id, step.get("id", "?"), step.get("agent", "?"),
                    "dry-run", output={"dry_run": True},
                    started_at=ts,
                    completed_at=ts,
                )

            print("\n✅ Workflow validation passed (dry-run)")
            return task_id
        finally:
            _close_db_on_exit(db_obj, _close_db)

    # ── Real execution mode ──
    try:
        # Determine db_path for WorkflowService (use provided db's file or auto-detect)
        db_path_to_use = db_obj.db_path if not _close_db else None

        if task_id:
            print(f"Task: {task_id} (existing)")
        else:
            print(f"Task: (new — auto-assigned by WorkflowService)")
        print(f"Workflow: {workflow_id}")
        print(f"Steps: {steps_count}")

        print("\n[Executing workflow...]")

        svc = WorkflowService()
        result = svc.execute(
            db_path=db_path_to_use,
            workflow_path=workflow_path,
            task_id=task_id,
            roles_path=roles_path,
        )

        if result is None:
            print("\n❌ Workflow execution failed.")
            return None

        print(f"\n{'='*50}")
        print(f"Workflow Complete: {result}")
        print(f"{'='*50}")

        return result
    finally:
        _close_db_on_exit(db_obj, _close_db)


def main():
    """multiagent run CLI entry point."""
    args = parse_run_args()
    return cmd_run(
        workflow_path=args["workflow"],
        task_id=args["task_id"],
        dry_run=args["dry_run"],
        roles_path=args.get("roles"),
    )


if __name__ == "__main__":
    main()
