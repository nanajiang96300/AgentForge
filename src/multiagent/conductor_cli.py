"""
DEPRECATED: Conductor CLI — import from cli.conductor instead.

This file is a thin re-export wrapper for backward compatibility.
"""

from .cli.conductor import (  # noqa: F401
    cmd_start, cmd_status, cmd_stop, cmd_restart,
    cmd_alerts, cmd_retry, cmd_reject,
    main, _build_parser,
)
