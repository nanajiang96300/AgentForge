"""
Agent Registry — extensible agent type management.

Register custom agent roles at runtime. Supports YAML config loading.
Pattern mirrors adapters/__init__.py registry.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger("multiagent.registry")


@dataclass
class AgentConfig:
    """Agent role configuration."""
    name: str
    description: str = ""
    model: str = ""
    personality: str = ""  # Behavioral style hint
    permissions: dict = field(default_factory=lambda: {"write": [], "read": [], "deny": []})
    skill: str = ""       # Path to SKILL.md
    memory: str = ""      # Memory directory
    session: str = "per-issue"
    runtime: str = ""     # Override default runtime
    timeout: int = 600
    output_required: list = field(default_factory=list)


class AgentRegistry:
    """Global agent type registry — register, list, create agent configs."""

    _agents: dict[str, AgentConfig] = {}

    @classmethod
    def register(cls, config: AgentConfig):
        """Register a new agent type."""
        cls._agents[config.name] = config
        _log.info("Agent registered: %s", config.name)

    @classmethod
    def unregister(cls, name: str) -> bool:
        """Remove an agent type."""
        if name in cls._agents:
            del cls._agents[name]
            return True
        return False

    @classmethod
    def get(cls, name: str) -> Optional[AgentConfig]:
        """Get agent config by name."""
        return cls._agents.get(name)

    @classmethod
    def list_all(cls) -> list[AgentConfig]:
        """List all registered agents."""
        return list(cls._agents.values())

    @classmethod
    def list_names(cls) -> list[str]:
        """List agent names."""
        return sorted(cls._agents.keys())

    @classmethod
    def load_from_yaml(cls, yaml_path) -> int:
        """Load agent configs from a roles.yaml file. Returns count loaded."""
        import yaml
        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        count = 0
        agents = data.get("agents", {})
        for name, cfg in agents.items():
            if not isinstance(cfg, dict):
                continue
            config = AgentConfig(
                name=name,
                description=cfg.get("description", ""),
                model=cfg.get("model", ""),
                personality=cfg.get("personality", ""),
                permissions=cfg.get("permissions", {"write": [], "read": [], "deny": []}),
                skill=cfg.get("skill", ""),
                memory=cfg.get("memory", ""),
                session=cfg.get("session", "per-issue"),
                runtime=cfg.get("runtime", ""),
                timeout=cfg.get("timeout", 600),
                output_required=cfg.get("output", {}).get("required", []),
            )
            cls.register(config)
            count += 1

        return count

    @classmethod
    def to_workflow_step(cls, name: str, step_id: str = None,
                         description: str = "", depends_on: list = None,
                         timeout: int = None) -> dict:
        """Generate a workflow step definition from a registered agent."""
        agent = cls.get(name)
        if not agent:
            raise ValueError(f"Unknown agent: {name}")

        return {
            "id": step_id or f"{name}_step",
            "agent": name,
            "description": description or agent.description,
            "timeout": timeout or agent.timeout,
            "depends_on": depends_on or [],
            "output": {"required": agent.output_required},
            "retry": {"max": 3},
        }


# Register built-in agents on import
_builtin_configs = [
    AgentConfig(
        name="pm",
        description="Project Manager — analyze requirements, break down tasks",
        model="deepseek/deepseek-chat",
        permissions={"write": ["docs/"], "read": ["src/", "tests/"], "deny": ["src/", "tests/"]},
        skill="architectures/dev-test-loop/skills/pm/SKILL.md",
        memory=".agents/memory/pm/",
        session="per-issue",
        timeout=300,
        output_required=["root_cause", "target_module", "complexity", "task_breakdown", "estimated_files"],
    ),
    AgentConfig(
        name="architect",
        description="架构设计师 — 设计系统架构、生成 C4 图 + ADR、技术选型",
        model="deepseek-v4-pro",
        permissions={"write": ["docs/architecture/"], "read": ["src/", "docs/", "architectures/"], "deny": ["src/", "tests/"]},
        skill="architectures/dev-test-loop/skills/architect/SKILL.md",
        memory=".agents/memory/architect/",
        session="per-issue",
        timeout=600,
        output_required=["architecture_doc", "adrs", "component_diagram", "tech_stack", "tradeoffs"],
    ),
    AgentConfig(
        name="dev",
        description="Developer — implement features and fix bugs",
        model="deepseek/deepseek-chat",
        permissions={"write": ["src/"], "read": ["src/", "tests/"], "deny": ["tests/"]},
        skill="architectures/dev-test-loop/skills/dev/SKILL.md",
        memory=".agents/memory/dev/",
        session="per-issue",
        timeout=600,
        output_required=["branch_name", "files_changed"],
    ),
    AgentConfig(
        name="test",
        description="Test Engineer — run tests and verify implementations",
        model="deepseek/deepseek-chat",
        permissions={"write": ["tests/"], "read": ["src/", "tests/"], "deny": ["src/"]},
        skill="architectures/dev-test-loop/skills/test/SKILL.md",
        memory=".agents/memory/test/",
        session="per-pr",
        timeout=300,
        output_required=["verdict", "test_summary"],
    ),
]
for _c in _builtin_configs:
    AgentRegistry.register(_c)
