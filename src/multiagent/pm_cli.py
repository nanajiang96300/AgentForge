"""
DEPRECATED: PM CLI — import from cli.pm instead.

This file is a thin re-export wrapper for backward compatibility.
"""

from .cli.pm import (  # noqa: F401
    cmd_init, cmd_submit, cmd_list, cmd_status,
    main,
)
