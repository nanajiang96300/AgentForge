"""OpenCode 适配器（骨架）"""
from .base import AgentAdapter
class OpenCodeAdapter(AgentAdapter):
    def name(self): return "opencode"
    def build_command(self, *a): raise NotImplementedError
    def parse_output(self, *a): raise NotImplementedError
    def _paths_to_tool_patterns(self, *a): raise NotImplementedError
