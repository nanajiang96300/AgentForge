"""
DEPRECATED: Role CLI — import from cli.role instead.

This file is a thin re-export wrapper for backward compatibility.
"""

from .cli.role import (  # noqa: F401
    cmd_role_create, cmd_role_list, cmd_role_show,
    cmd_role_delete, cmd_role_clone, cmd_role_validate,
    main,
)
