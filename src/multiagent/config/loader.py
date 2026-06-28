"""
Unified config and path discovery — single source of truth.

Replaces duplicated find_state_db/find_workflow/find_roles across 5+ files.
"""

from pathlib import Path

_SKIP_DIRS = {'.venv', '.git', '__pycache__', 'node_modules', '.claude'}


def find_state_db(start: Path = None) -> Path:
    """Find state.db, checking common locations first."""
    if start is None:
        start = Path.cwd()

    candidates = [
        start / "state.db",
        start / ".framework" / "workflow" / "state.db",
    ]
    for p in candidates:
        if p.exists():
            return p

    # Fallback: glob from start, skipping large dirs
    try:
        for m in start.glob("**/state.db"):
            if not any(s in m.parts for s in _SKIP_DIRS):
                return m
    except (PermissionError, OSError):
        pass

    return start / "state.db"


def find_workflow_yaml(start: Path = None, prefer_tdd: bool = True) -> Path | None:
    """Find the default workflow YAML. Prefers TDD workflow by default (v1.0)."""
    if start is None:
        start = Path.cwd()

    workflow_dir = start / "architectures" / "dev-test-loop" / "workflow"

    # v1.0: TDD workflow is the recommended default
    if prefer_tdd:
        tdd_wf = workflow_dir / "pm-testfirst-dev-test.yaml"
        if tdd_wf.exists():
            return tdd_wf

    candidates = [
        workflow_dir / "pm-dev-test.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p

    # Glob fallback
    try:
        for m in start.glob("**/pm-dev-test.yaml"):
            if not any(s in m.parts for s in _SKIP_DIRS):
                return m
    except (PermissionError, OSError):
        pass

    return None


def select_workflow(task_type: str = "feature", complexity: str = "medium") -> str:
    """Select the best workflow for a task based on type and complexity.

    v1.0 workflow selection:
    - simple/low complexity → traditional (pm-dev-test, 3 steps, fast)
    - feature/medium+ → TDD (pm-testfirst-dev-test, 4 steps, with acceptance criteria)
    - architecture/design → architect (pm-architect-test, 3 steps)
    - parallel subtasks → swarm (swarm-dev, parallel Dev agents)
    """
    if task_type in ("architecture", "design"):
        return "pm-architect-test"
    if task_type == "parallel":
        return "swarm-dev"
    if complexity in ("low", "simple"):
        return "pm-dev-test"
    # Default: TDD for feature/bug/medium+
    return "pm-testfirst-dev-test"


def find_roles_yaml(start: Path = None) -> Path | None:
    """Find roles.yaml configuration."""
    if start is None:
        start = Path.cwd()

    candidates = [
        start / "architectures" / "dev-test-loop" / "config" / "roles.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p

    # Glob fallback
    try:
        for m in start.glob("**/roles.yaml"):
            if not any(s in m.parts for s in _SKIP_DIRS):
                return m
    except (PermissionError, OSError):
        pass

    return None


def find_architecture_dir(start: Path = None) -> Path | None:
    """Find the active architecture config directory."""
    if start is None:
        start = Path.cwd()

    candidates = [
        start / "architectures" / "dev-test-loop",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None
