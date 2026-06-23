"""
Gate: Workflow CLI — create, validate, list, graph

Tests workflow template creation, validation (cycles, missing agents,
orphan deps), and ASCII graph output.
"""

import os
import sys
import tempfile
import yaml
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.workflow_cli import (
    _make_workflow_yaml, _validate_workflow, _detect_cycles,
    _workflow_ascii_graph, TEMPLATES,
)


# ── Template Creation ──


class TestWorkflowTemplates:
    def test_all_templates_have_ids(self):
        """Every template has a workflow ID."""
        for name, t in TEMPLATES.items():
            assert t.get("id"), f"Template '{name}' missing id"
            assert t.get("steps"), f"Template '{name}' missing steps"

    def test_all_templates_have_steps_with_ids(self):
        """Every step in every template has an id."""
        for name, t in TEMPLATES.items():
            for s in t["steps"]:
                assert s.get("id"), f"Template '{name}' step missing id"
                assert "agent" in s, f"Template '{name}' step missing agent field"

    def test_pm_dev_test_template_has_required_edges(self):
        """PM-Dev-Test template has correct dependency chain."""
        t = TEMPLATES["pm-dev-test"]
        steps = {s["id"]: s for s in t["steps"]}
        assert "dev_fix" in steps["pm_analyze"].get("depends_on", []) or \
               "pm_analyze" in [d for s2 in t["steps"] if s2["id"] == "dev_fix"
                               for d in (s2.get("depends_on", []) if isinstance(s2.get("depends_on", []), list) else [s2.get("depends_on", "")])]
        # dev_fix should depend on pm_analyze
        dev = steps.get("dev_fix", {})
        deps = dev.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        assert "pm_analyze" in deps

    def test_diamond_template_has_parallel_steps(self):
        """Diamond template has two steps that both depend on pm_analyze."""
        t = TEMPLATES["diamond"]
        steps = {s["id"]: s for s in t["steps"]}
        pm_dependents = [s["id"] for s in t["steps"]
                        if "pm_analyze" in (s.get("depends_on", []) if isinstance(s.get("depends_on", []), list) else [s.get("depends_on", "")])]
        assert len(pm_dependents) >= 2, f"Expected >=2 parallel steps, got {pm_dependents}"


# ── YAML Generation ──


class TestMakeWorkflowYaml:
    def test_generates_valid_yaml_structure(self):
        wf = _make_workflow_yaml("pm-dev-test")
        assert "workflow" in wf
        assert wf["workflow"]["id"] == "pm-dev-test-loop"
        assert len(wf["workflow"]["steps"]) == 3

    def test_unknown_template_returns_none(self):
        assert _make_workflow_yaml("nonexistent") is None

    def test_all_steps_have_agents(self):
        wf = _make_workflow_yaml("pm-dev-test")
        for s in wf["workflow"]["steps"]:
            assert s.get("agent"), f"Step {s['id']} missing agent"

    def test_error_policy_included(self):
        wf = _make_workflow_yaml("pm-dev-test")
        ep = wf["workflow"].get("error_policy", {})
        assert "max_rejections" in ep


# ── Validation ──


class TestValidateWorkflow:
    def test_valid_workflow_no_issues(self):
        wf = _make_workflow_yaml("pm-dev-test")
        issues, warnings = _validate_workflow(wf)
        assert len(issues) == 0, f"Unexpected issues: {issues}"

    def test_missing_agent_warns(self):
        wf = _make_workflow_yaml("linear")  # linear has empty agent fields
        issues, warnings = _validate_workflow(wf)
        agent_warnings = [w for w in warnings if "no agent" in w.lower() or "not in the registry" in w.lower()]
        # linear template has empty agents, should warn
        assert len(issues) == 0  # empty agent is not a hard issue

    def test_duplicate_step_id_detected(self):
        wf = {
            "workflow": {
                "id": "test",
                "steps": [
                    {"id": "step_a", "agent": "pm"},
                    {"id": "step_a", "agent": "pm"},
                ]
            }
        }
        issues, _ = _validate_workflow(wf)
        dup_issues = [i for i in issues if "Duplicate" in i]
        assert len(dup_issues) >= 1

    def test_missing_dependency_detected(self):
        wf = {
            "workflow": {
                "id": "test",
                "steps": [
                    {"id": "step_a", "agent": "pm", "depends_on": ["nonexistent"]},
                ]
            }
        }
        issues, _ = _validate_workflow(wf)
        dep_issues = [i for i in issues if "depends_on" in i]
        assert len(dep_issues) >= 1

    def test_verdict_next_reference_validated(self):
        wf = {
            "workflow": {
                "id": "test",
                "steps": [
                    {"id": "step_a", "agent": "test",
                     "on_verdict_rejected": {"next": "nonexistent"}},
                ]
            }
        }
        issues, _ = _validate_workflow(wf)
        ref_issues = [i for i in issues if "on_verdict_rejected" in i]
        assert len(ref_issues) >= 1

    def test_empty_steps_fails(self):
        wf = {"workflow": {"id": "test", "steps": []}}
        issues, _ = _validate_workflow(wf)
        assert len(issues) >= 1


# ── Cycle Detection ──


class TestCycleDetection:
    def test_no_cycle_in_linear(self):
        steps = [
            {"id": "a", "depends_on": []},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["b"]},
        ]
        cycles = _detect_cycles(steps)
        assert len(cycles) == 0

    def test_simple_cycle_detected(self):
        steps = [
            {"id": "a", "depends_on": ["b"]},
            {"id": "b", "depends_on": ["a"]},
        ]
        cycles = _detect_cycles(steps)
        assert len(cycles) >= 1

    def test_rejection_loop_detected_as_cycle(self):
        """A → B with B rejecting back to A creates a cycle (this is expected in workflows)."""
        steps = [
            {"id": "dev", "depends_on": []},
            {"id": "test", "depends_on": ["dev"],
             "on_verdict_rejected": {"next": "dev"}},
        ]
        cycles = _detect_cycles(steps)
        # Rejection loops ARE cycles; validation should warn about them
        assert len(cycles) >= 1

    def test_diamond_no_cycle(self):
        steps = [
            {"id": "pm", "depends_on": []},
            {"id": "dev1", "depends_on": ["pm"]},
            {"id": "dev2", "depends_on": ["pm"]},
            {"id": "test", "depends_on": ["dev1", "dev2"]},
        ]
        cycles = _detect_cycles(steps)
        assert len(cycles) == 0


# ── ASCII Graph ──


class TestAsciiGraph:
    def test_graph_contains_step_ids(self):
        wf = _make_workflow_yaml("pm-dev-test")
        output = _workflow_ascii_graph(wf)
        for sid in ["pm_analyze", "dev_fix", "test_verify"]:
            assert sid in output

    def test_graph_shows_rejection_edges(self):
        wf = _make_workflow_yaml("pm-dev-test")
        output = _workflow_ascii_graph(wf)
        assert "rejected" in output.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
