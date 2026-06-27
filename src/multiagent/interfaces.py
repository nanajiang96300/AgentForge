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


class StepConditionEvaluator(ABC):
    """Evaluate conditions on step outputs and task context.

    Supports: ==, !=, >, <, >=, <=, in, not in, and, or, not
    Example: 'verdict == "approved" and complexity != "high"'
    """

    @abstractmethod
    def evaluate(self, condition: str, context: dict) -> bool:
        """Evaluate condition expression against context. Returns True/False."""
        ...

    @abstractmethod
    def validate(self, condition: str) -> tuple:
        """Validate condition syntax. Returns (is_valid: bool, error: str|None)."""
        ...


class RoleTemplateLoader(ABC):
    """Load and resolve role templates to AgentConfig.

    Reserved for Phase 8c role template system.
    """

    @abstractmethod
    def list_builtins(self) -> list[str]:
        """List built-in template names."""
        ...

    @abstractmethod
    def list_user_templates(self) -> list[str]:
        """List user-defined template names."""
        ...

    @abstractmethod
    def load(self, name: str) -> "AgentConfig":
        """Load a template by name, resolving inheritance and defaults."""
        ...

    @abstractmethod
    def validate_template(self, name: str) -> list[str]:
        """Validate a template. Returns list of issues (empty = valid)."""
        ...


class WorkflowTopology(ABC):
    """Query workflow graph topology independently of execution.

    Reserved for Phase 8c multi-agent collaboration modes.
    """

    @abstractmethod
    def entry_nodes(self) -> list[str]:
        """Nodes with no incoming edges."""
        ...

    @abstractmethod
    def successors_of(self, node_id: str) -> list[str]:
        """IDs of nodes that directly follow node_id."""
        ...

    @abstractmethod
    def predecessors_of(self, node_id: str) -> list[str]:
        """IDs of nodes that directly precede node_id."""
        ...

    @abstractmethod
    def parallel_groups(self) -> list[list[str]]:
        """Return groups of nodes that can execute in parallel."""
        ...

    @abstractmethod
    def validate(self) -> list[str]:
        """Topological validation. Returns list of issues (empty = valid)."""
        ...
