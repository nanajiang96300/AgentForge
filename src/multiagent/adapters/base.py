"""Agent 适配器抽象基类"""
from abc import ABC, abstractmethod
from pathlib import Path

class AgentAdapter(ABC):
    def __init__(self, project_root: Path):
        self.project_root = project_root

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def build_command(self, agent_config: dict, task_prompt: str, step: dict) -> list[str]: ...

    @abstractmethod
    def parse_output(self, stdout: str, stderr: str): ...

    def get_tool_restriction_flags(self, permissions: dict):
        return self._paths_to_tool_patterns(
            permissions.get("deny", []), permissions.get("write", []))

    @abstractmethod
    def _paths_to_tool_patterns(self, deny_paths: list[str], write_paths: list[str]): ...
