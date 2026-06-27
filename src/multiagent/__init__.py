"""MultiAgent — 多智能体协同开发框架"""
__version__ = "0.1.0"

# CLI re-exports for external consumers
from .cli.run import parse_run_args, cmd_run  # noqa: F401
from .cli.conductor import (  # noqa: F401
    cmd_start, cmd_status, cmd_stop, cmd_restart,
    cmd_alerts, cmd_retry, cmd_reject,
)
from .cli.role import (  # noqa: F401
    cmd_role_create, cmd_role_list, cmd_role_show,
    cmd_role_delete, cmd_role_clone, cmd_role_validate,
)
from .cli.workflow import (  # noqa: F401
    cmd_workflow_create, cmd_workflow_list,
    cmd_workflow_validate, cmd_workflow_graph,
)
from .cli.pm import cmd_init, cmd_submit, cmd_list, cmd_status  # noqa: F401
from .cli.metrics import parse_metrics_args, cmd_metrics  # noqa: F401
