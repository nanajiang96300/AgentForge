"""Agent 运行时适配器"""
from .base import AgentAdapter
from .claude_code import ClaudeCodeAdapter
from .opencode import OpenCodeAdapter

_registry = {"claude-code": ClaudeCodeAdapter, "opencode": OpenCodeAdapter}

def register(name, cls): _registry[name] = cls
def create(name, project_root=None):
    from pathlib import Path
    cls = _registry.get(name)
    if cls is None: raise ValueError(f"Unknown adapter: {name}")
    return cls(project_root or Path.cwd())
def list_adapters(): return list(_registry.keys())
