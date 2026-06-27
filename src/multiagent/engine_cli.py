"""
DEPRECATED: Engine CLI — import from cli.run instead.

This file is a thin re-export wrapper for backward compatibility.
"""

from .cli.run import parse_run_args, cmd_run, main, _wire_hooks  # noqa: F401
