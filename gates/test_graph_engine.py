"""
P1 Gate: graph_engine.py — WorkflowGraph DAG operations

Covers add_node, add_edge, topological_order, to_json, from_json,
to_workflow_yaml, entry/exit nodes, successors/predecessors.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.core.graph_engine import (
    WorkflowGraph, GraphNode, GraphEdge,
)


# ── Fixtures ──


@pytest.fixture
def linear_graph():
    """PM -> Dev -> Test (3 steps in a line)."""
    g = WorkflowGraph("test-linear", "Linear workflow")
    g.add_node(GraphNode(id="pm_analyze", agent="pm", output_required=["root_cause"]))
    g.add_node(GraphNode(id="dev_fix", agent="dev", output_required=["branch_name"]))
    g.add_node(GraphNode(id="test_verify", agent="test", output_required=["verdict"]))
    g.add_edge("pm_analyze", "dev_fix")
    g.add_edge("dev_fix", "test_verify")
    return g


@pytest.fixture
def diamond_graph():
    """PM -> Dev + Test in parallel -> Reviewer."""
    g = WorkflowGraph("test-diamond", "Diamond workflow")
    g.add_node(GraphNode(id="pm", agent="pm"))
    g.add_node(GraphNode(id="dev", agent="dev"))
    g.add_node(GraphNode(id="test", agent="test"))
    g.add_node(GraphNode(id="reviewer", agent="reviewer"))
    g.add_edge("pm", "dev")
    g.add_edge("pm", "test")
    g.add_edge("dev", "reviewer")
    g.add_edge("test", "reviewer")
    return g


# ── Node/Edge Building ──


class TestGraphBuilding:
    def test_add_node(self, linear_graph):
        assert len(linear_graph.nodes) == 3
        assert "pm_analyze" in linear_graph.nodes

    def test_add_edge(self, linear_graph):
        assert len(linear_graph.edges) == 2
        assert linear_graph.edges[0].source == "pm_analyze"

    def test_remove_node_removes_edges(self):
        g = WorkflowGraph("test")
        g.add_node(GraphNode(id="a", agent="x"))
        g.add_node(GraphNode(id="b", agent="y"))
        g.add_edge("a", "b")
        g.remove_node("a")
        assert "a" not in g.nodes
        assert len(g.edges) == 0

    def test_edge_with_invalid_node_raises(self):
        g = WorkflowGraph("test")
        g.add_node(GraphNode(id="a", agent="x"))
        with pytest.raises(ValueError):
            g.add_edge("a", "nonexistent")

    def test_conditional_edge(self, linear_graph):
        linear_graph.add_edge(
            "test_verify", "dev_fix",
            condition="verdict == 'rejected'", label="Reject"
        )
        edge = linear_graph.edges[-1]
        assert edge.condition == "verdict == 'rejected'"


# ── Graph Analysis ──


class TestGraphAnalysis:
    def test_entry_nodes_linear(self, linear_graph):
        entries = linear_graph.get_entry_nodes()
        assert entries == ["pm_analyze"]

    def test_exit_nodes_linear(self, linear_graph):
        exits = linear_graph.get_exit_nodes()
        assert exits == ["test_verify"]

    def test_entry_nodes_diamond(self, diamond_graph):
        entries = diamond_graph.get_entry_nodes()
        assert entries == ["pm"]

    def test_exit_nodes_diamond(self, diamond_graph):
        exits = diamond_graph.get_exit_nodes()
        assert exits == ["reviewer"]

    def test_successors(self, linear_graph):
        succ = linear_graph.get_successors("pm_analyze")
        assert len(succ) == 1
        assert succ[0].target == "dev_fix"

    def test_predecessors(self, linear_graph):
        pred = linear_graph.get_predecessors("test_verify")
        assert len(pred) == 1
        assert pred[0].source == "dev_fix"

    def test_no_successors_for_exit_node(self, linear_graph):
        assert len(linear_graph.get_successors("test_verify")) == 0

    def test_no_predecessors_for_entry_node(self, linear_graph):
        assert len(linear_graph.get_predecessors("pm_analyze")) == 0


# ── Topological Sort ──


class TestTopologicalOrder:
    def test_linear_order(self, linear_graph):
        order = linear_graph.topological_order()
        assert order == ["pm_analyze", "dev_fix", "test_verify"]

    def test_diamond_order(self, diamond_graph):
        order = diamond_graph.topological_order()
        # pm must be first, reviewer must be last
        assert order[0] == "pm"
        assert order[-1] == "reviewer"
        # dev and test must both come after pm and before reviewer
        assert set(order[1:3]) == {"dev", "test"}

    def test_cycle_detection(self):
        g = WorkflowGraph("cyclic")
        g.add_node(GraphNode(id="a", agent="x"))
        g.add_node(GraphNode(id="b", agent="y"))
        g.add_edge("a", "b")
        g.add_edge("b", "a")  # Cycle
        order = g.topological_order()
        # Should not infinite loop — returns partial
        assert len(order) == 0  # Kahn's algorithm can't start with cycle


# ── Serialization ──


class TestSerialization:
    def test_to_json_roundtrip(self):
        g = WorkflowGraph("test-rt", "Roundtrip test")
        g.add_node(GraphNode(id="pm_analyze", agent="pm", timeout=300,
                   output_required=["root_cause"]))
        g.add_node(GraphNode(id="dev_fix", agent="dev", timeout=600,
                   output_required=["branch_name"]))
        g.add_edge("pm_analyze", "dev_fix")
        g.add_edge("dev_fix", "dev_fix", condition="verdict == 'rejected'")

        data = g.to_json()
        g2 = WorkflowGraph.from_json(data)

        assert g2.graph_id == "test-rt"
        assert len(g2.nodes) == 2
        assert len(g2.edges) == 2
        assert g2.nodes["pm_analyze"].timeout == 300
        assert g2.nodes["pm_analyze"].output_required == ["root_cause"]

    def test_to_json_output_has_expected_keys(self, linear_graph):
        data = linear_graph.to_json()
        assert "graph_id" in data
        assert "nodes" in data
        assert "edges" in data
        assert "description" in data


# ── YAML Export ──


class TestYamlExport:
    def test_to_workflow_yaml_has_required_sections(self, linear_graph):
        yaml_str = linear_graph.to_workflow_yaml()
        assert "workflow:" in yaml_str
        assert "steps:" in yaml_str
        assert "pm_analyze" in yaml_str
        assert "dev_fix" in yaml_str
        assert "test_verify" in yaml_str
        assert "error_policy:" in yaml_str
        assert "max_rejections:" in yaml_str

    def test_yaml_includes_depends_on(self, linear_graph):
        yaml_str = linear_graph.to_workflow_yaml()
        assert "depends_on" in yaml_str

    def test_yaml_handles_conditional_edges(self):
        g = WorkflowGraph("test-cond")
        g.add_node(GraphNode(id="pm", agent="pm"))
        g.add_node(GraphNode(id="dev", agent="dev"))
        g.add_node(GraphNode(id="test", agent="test"))
        g.add_edge("pm", "dev")
        g.add_edge("dev", "test")
        # Conditional edge from test to a follow-up step (acyclic)
        g.add_node(GraphNode(id="reviewer", agent="reviewer"))
        g.add_edge("test", "reviewer", condition="verdict == 'approved'")
        yaml_str = g.to_workflow_yaml()
        assert "on_verdict_approved" in yaml_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
