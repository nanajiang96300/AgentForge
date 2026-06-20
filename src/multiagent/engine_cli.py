"""
Engine CLI — Phase 3 工作流执行命令

用法:
    multiagent run <workflow.yaml>               运行工作流
    multiagent run <workflow.yaml> --task-id <id> 运行已有任务
    multiagent run <workflow.yaml> --dry-run       仅验证不执行
"""

import sys
import argparse
import uuid
from pathlib import Path

from .db import StateDB, Task, now_iso
from .engine import AgentSpawner, load_yaml


def find_state_db():
    """查找 state.db"""
    for p in [Path.cwd()] + list(Path.cwd().parents):
        for pat in ["**/state.db", ".framework/workflow/state.db"]:
            m = list(p.glob(pat))
            if m:
                return m[0]
    return Path.cwd() / "state.db"


def find_roles_yaml():
    """查找 roles.yaml"""
    for p in [Path.cwd()] + list(Path.cwd().parents):
        for pat in ["**/roles.yaml", "architectures/*/config/roles.yaml"]:
            m = list(p.glob(pat))
            if m:
                return m[0]
    return None


def parse_run_args(argv=None):
    """解析 multiagent run 命令参数，返回 dict"""
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
    执行工作流。返回 task_id 或 None（失败时）。

    参数可以直接传入（编程调用）或从 sys.argv 解析。
    """
    # Resolve paths
    wf_path = Path(workflow_path) if workflow_path else None
    if wf_path is None:
        return None

    if not wf_path.exists():
        print(f"Error: Workflow file not found: {wf_path}")
        return None

    # Load workflow to get ID
    try:
        wf_def = load_yaml(wf_path)
    except Exception as e:
        print(f"Error: Failed to load workflow YAML: {e}")
        return None

    workflow_id = wf_def.get("workflow", {}).get("id", wf_path.stem)

    # Initialize DB
    if db is None:
        db = StateDB(find_state_db())
        db.connect()
        _close_db = True
    else:
        _close_db = False

    try:
        # Get or create task
        if task_id:
            task_data = db.get_task(task_id)
            if task_data is None:
                print(f"Error: Task not found: {task_id}")
                return None
            # Convert dict to Task
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
            print(f"Task: {task.id} (existing)")
        else:
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
            if not db.insert_task(task):
                print(f"Error: Failed to create task (duplicate key?)")
                return None
            print(f"Task: {task.id} (new)")

        print(f"Workflow: {workflow_id}")
        print(f"Steps: {len(wf_def.get('workflow', {}).get('steps', []))}")

        if dry_run:
            print("\n[Dry-run mode — validating workflow only]")
            # Load workflow, resolve dependencies, print summary
            from .orchestrator import WorkflowOrchestrator

            # Load roles for spawner (may be dummy for dry-run)
            roles = {"agents": {}, "global": {"runtime": "claude-code"}}
            if roles_path:
                roles = load_yaml(Path(roles_path))
            else:
                found = find_roles_yaml()
                if found:
                    roles = load_yaml(found)

            spawner = AgentSpawner(db, roles)
            orchestrator = WorkflowOrchestrator(db, spawner, wf_path)
            orchestrator.load()

            # Print step tree
            for step_id, step in orchestrator.steps.items():
                deps = f" (depends: {', '.join(step.depends_on)})" if step.depends_on else ""
                print(f"  • {step_id} [{step.agent}]{deps}")
                if step.output.get("required"):
                    print(f"      required output: {', '.join(step.output['required'])}")

            # Mark task as completed in dry-run (validation passed)
            db.update_task_status(task_id, "completed")
            # Record step results as dry-run
            for step_id in orchestrator.steps:
                db.record_step(
                    task_id, step_id, "dry-run", "completed",
                    output={"dry_run": True},
                    started_at=now_iso(),
                    completed_at=now_iso(),
                )

            print(f"\n✅ Workflow validation passed (dry-run)")
            return task_id

        # Real execution mode
        print("\n[Executing workflow...]")

        # Load roles
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        if roles_path:
            roles = load_yaml(Path(roles_path))
        else:
            found = find_roles_yaml()
            if found:
                roles = load_yaml(found)

        spawner = AgentSpawner(db, roles)
        orchestrator = WorkflowOrchestrator(db, spawner, wf_path)

        # Mark task as running
        db.update_task_status(task_id, "running")
        task.status = "running"

        # Run the workflow
        result = orchestrator.run(task)

        # Print results
        print(f"\n{'='*50}")
        print(f"Workflow Complete: {task_id}")
        print(f"{'='*50}")
        for step_id, state in result["steps"].items():
            icon = "✅" if state == "completed" else ("❌" if state in ("failed", "rejected") else "⏳")
            output_preview = ""
            if step_id in result.get("results", {}):
                res = result["results"][step_id]
                if isinstance(res, dict):
                    # Show a brief preview
                    keys = list(res.keys())[:3]
                    output_preview = f" → {', '.join(keys)}"
            print(f"  {icon} {step_id}: {state}{output_preview}")

        # Update task status based on final step states
        all_completed = all(
            s in ("completed", "skipped") for s in result["steps"].values()
        )
        if all_completed:
            db.update_task_status(task_id, "completed")
        elif any(s == "failed" for s in result["steps"].values()):
            db.update_task_status(task_id, "failed")

        return task_id

    finally:
        if _close_db:
            db.close()


def main():
    """multiagent run CLI 入口"""
    args = parse_run_args()
    return cmd_run(
        workflow_path=args["workflow"],
        task_id=args["task_id"],
        dry_run=args["dry_run"],
        roles_path=args.get("roles"),
    )


if __name__ == "__main__":
    main()
