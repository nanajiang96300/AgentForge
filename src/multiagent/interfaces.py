"""
Core interfaces (ABCs) for AgentForge framework.
All high-level modules depend on these abstractions, not on concrete implementations.
"""

from abc import ABC, abstractmethod
from typing import Optional, Callable


class TaskQueue(ABC):
    """Task lifecycle management (pending → running → completed)."""
    @abstractmethod
    def get_pending(self) -> list[dict]: ...
    @abstractmethod
    def get_task(self, task_id: str) -> Optional[dict]: ...
    @abstractmethod
    def update_status(self, task_id: str, status: str, step: str = None) -> None: ...
    @abstractmethod
    def get_escalated(self) -> list[dict]: ...


class MetricsStore(ABC):
    """Token/cost/duration metrics persistence."""
    @abstractmethod
    def record(self, task_id: str, step_id: str, agent: str, adapter: str,
               model: str, input_tokens: int, output_tokens: int,
               cost_usd: float, duration_ms: int, status: str) -> None: ...
    @abstractmethod
    def summary(self, agent: str = None) -> dict: ...


class EscalationStore(ABC):
    """Escalation event persistence."""
    @abstractmethod
    def record(self, task_id: str, step_id: str, reason: str, context: dict = None) -> int: ...
    @abstractmethod
    def get_pending(self) -> list[dict]: ...
    @abstractmethod
    def resolve(self, escalation_id: int, resolution: str) -> bool: ...


class AgentRuntime(ABC):
    """Agent subprocess management."""
    @abstractmethod
    def execute(self, task, step: dict, timeout: int = 600): ...


class Notifier(ABC):
    """Notification channel (Discord, Slack, etc)."""
    @abstractmethod
    def notify(self, event: str, task_id: str, project: str, task_dict: dict) -> None: ...


class StepHook(ABC):
    """Hook into workflow step lifecycle."""
    @abstractmethod
    def before_step(self, task_id: str, step_id: str) -> None: ...
    @abstractmethod
    def after_step(self, task_id: str, step_id: str, result) -> None: ...
    @abstractmethod
    def on_rejection(self, task_id: str, step_id: str, count: int) -> None: ...
    @abstractmethod
    def on_escalation(self, task_id: str, step_id: str, reason: str) -> None: ...


class ProgressCalculator(ABC):
    """Calculate task completion progress."""
    @abstractmethod
    def calculate(self, task_id: str, db_conn) -> dict: ...


# Type aliases for duck-typing compatibility
NotifierFunc = Callable[[str, str, str, dict], None]
