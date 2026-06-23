"""
P1 Gate: runtime/registry.py — AgentRegistry CRUD + YAML loading

Covers register, unregister, get, list_all, list_names,
load_from_yaml, to_workflow_step, and built-in agents.
"""

import sys
import tempfile
import yaml
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.runtime.registry import AgentRegistry, AgentConfig


# ── Fixtures ──


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset registry before each test to avoid cross-test pollution."""
    AgentRegistry._agents.clear()


@pytest.fixture
def sample_config():
    return AgentConfig(
        name="custom-agent",
        description="A custom test agent",
        model="test-model",
        permissions={"write": ["src/"], "read": ["tests/"], "deny": ["docs/"]},
        skill="skills/custom/SKILL.md",
        memory=".memory/custom/",
        session="per-issue",
        timeout=900,
        output_required=["result", "summary"],
    )


# ── CRUD ──


class TestRegistryCRUD:
    def test_register_agent(self, sample_config):
        AgentRegistry.register(sample_config)
        assert AgentRegistry.get("custom-agent") is not None

    def test_get_nonexistent_returns_none(self):
        assert AgentRegistry.get("nonexistent") is None

    def test_unregister(self, sample_config):
        AgentRegistry.register(sample_config)
        assert AgentRegistry.unregister("custom-agent") is True
        assert AgentRegistry.get("custom-agent") is None

    def test_unregister_nonexistent_returns_false(self):
        assert AgentRegistry.unregister("nonexistent") is False

    def test_list_all(self, sample_config):
        AgentRegistry.register(sample_config)
        all_agents = AgentRegistry.list_all()
        assert len(all_agents) >= 1
        names = [a.name for a in all_agents]
        assert "custom-agent" in names

    def test_list_names_sorted(self, sample_config):
        AgentRegistry.register(sample_config)
        names = AgentRegistry.list_names()
        assert "custom-agent" in names
        assert names == sorted(names)


# ── YAML Loading ──


class TestYamlLoading:
    def test_load_from_yaml(self):
        yaml_content = {
            "agents": {
                "qa": {
                    "description": "QA tester",
                    "model": "qa-model",
                    "timeout": 300,
                    "output": {"required": ["verdict"]},
                },
                "ops": {
                    "description": "Ops engineer",
                    "model": "ops-model",
                    "timeout": 600,
                },
            }
        }
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        )
        yaml.dump(yaml_content, tmp)
        tmp.close()

        count = AgentRegistry.load_from_yaml(tmp.name)
        Path(tmp.name).unlink()

        assert count == 2
        qa = AgentRegistry.get("qa")
        assert qa.description == "QA tester"
        assert qa.timeout == 300
        assert qa.output_required == ["verdict"]

        ops = AgentRegistry.get("ops")
        assert ops.model == "ops-model"

    def test_load_from_yaml_empty_agents(self):
        yaml_content = {"agents": {}}
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        )
        yaml.dump(yaml_content, tmp)
        tmp.close()

        count = AgentRegistry.load_from_yaml(tmp.name)
        Path(tmp.name).unlink()
        assert count == 0


# ── to_workflow_step ──


class TestToWorkflowStep:
    def test_generates_valid_step_definition(self, sample_config):
        AgentRegistry.register(sample_config)
        step = AgentRegistry.to_workflow_step(
            "custom-agent",
            step_id="custom_step",
            description="Do custom work",
            depends_on=["pm_analyze"],
        )
        assert step["id"] == "custom_step"
        assert step["agent"] == "custom-agent"
        assert step["description"] == "Do custom work"
        assert step["depends_on"] == ["pm_analyze"]
        assert step["timeout"] == 900
        assert step["output"]["required"] == ["result", "summary"]
        assert step["retry"]["max"] == 3

    def test_default_step_id(self, sample_config):
        AgentRegistry.register(sample_config)
        step = AgentRegistry.to_workflow_step("custom-agent")
        assert step["id"] == "custom-agent_step"

    def test_unknown_agent_raises(self):
        with pytest.raises(ValueError, match="Unknown agent"):
            AgentRegistry.to_workflow_step("nonexistent")


# ── Built-in Agents ──


class TestBuiltinAgents:
    def test_builtins_are_registered(self):
        """pm, dev, test should be available from module import side-effects."""
        from multiagent.runtime.registry import AgentRegistry as AR
        AR._agents.clear()
        # Simulate re-import's registration
        from multiagent.runtime import registry as reg
        # Re-run builtin registration
        for c in reg._builtin_configs:
            AR.register(c)

        pm = AR.get("pm")
        dev = AR.get("dev")
        test = AR.get("test")

        assert pm is not None
        assert dev is not None
        assert test is not None

        assert pm.timeout == 300
        assert dev.timeout == 600
        assert test.timeout == 300

        assert "root_cause" in pm.output_required
        assert "branch_name" in dev.output_required
        assert "verdict" in test.output_required


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
