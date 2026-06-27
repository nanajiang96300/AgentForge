from .pid_manager import PidManager
from .checkpoint import CheckpointManager
from .workflow_service import WorkflowService
from .role_service import RoleService
from .dashboard_service import DashboardService
from .recovery_service import RecoveryService
from .discovery_service import DiscoveryService


def _resolve_db(db_path=None):
    """Open a StateDB connection. CLI helper — keeps StateDB import out of cli/."""
    from ..config.loader import find_state_db
    from ..db import StateDB
    if db_path is None:
        db_path = find_state_db()
    if isinstance(db_path, str):
        from pathlib import Path
        db_path = Path(db_path)
    db = StateDB(db_path)
    db.connect()
    return db
