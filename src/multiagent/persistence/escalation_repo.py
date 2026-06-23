"""
EscalationRepository — escalation event persistence.
Delegates to StateDB for backward compatibility.
"""

from typing import Optional
from ..db import StateDB


class EscalationRepository:
    """Escalation event storage, separated from task/metrics concerns."""

    def __init__(self, db: StateDB):
        self.db = db

    def record(self, task_id: str, step_id: str, reason: str,
               context: Optional[dict] = None) -> int:
        return self.db.record_escalation(task_id, step_id, reason, context)

    def get_pending(self) -> list[dict]:
        return self.db.get_pending_escalations()

    def resolve(self, escalation_id: int, resolution: str) -> bool:
        return self.db.resolve_escalation(escalation_id, resolution)
