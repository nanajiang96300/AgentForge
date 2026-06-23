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


def find_workflow_yaml(start: Path = None) -> Path | None:
    """Find the default pm-dev-test workflow YAML."""
    if start is None:
        start = Path.cwd()

    candidates = [
        start / "architectures" / "dev-test-loop" / "workflow" / "pm-dev-test.yaml",
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
