"""
DEPRECATED: Workflow CLI — import from cli.workflow instead.

This file is a thin re-export wrapper for backward compatibility.
"""

from .cli.workflow import (  # noqa: F401
    cmd_workflow_create, cmd_workflow_list,
    cmd_workflow_validate, cmd_workflow_graph,
    main, TEMPLATES,
    _make_workflow_yaml, _validate_workflow, _workflow_ascii_graph,
    _detect_cycles,
)
