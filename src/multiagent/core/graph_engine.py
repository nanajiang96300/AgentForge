"""
Graph Workflow Engine — LangGraph-style DAG with conditional routing.

Nodes = Agent steps. Edges = transitions (optionally conditional).
Supports graph serialization to/from YAML and JSON.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

from ..interfaces import WorkflowTopology, StepConditionEvaluator

_log = logging.getLogger("multiagent.graph")


@dataclass
class GraphNode:
    """A node in the workflow graph (one agent step)."""
    id: str
    agent: str
    description: str = ""
    timeout: int = 600
    retry_max: int = 3
    output_required: list = field(default_factory=list)


@dataclass
class GraphEdge:
    """A directed edge between nodes. Optional condition for routing."""
    source: str       # Source node ID
    target: str       # Target node ID
    condition: str = ""   # e.g. "verdict == 'approved'" or "" for unconditional
    label: str = ""       # Human-readable label for visualization


class WorkflowGraph(WorkflowTopology):
    """A directed graph defining agent workflow topology.

    Converts to/from workflow YAML format compatible with WorkflowOrchestrator.
    """

    def __init__(self, graph_id: str = "custom", description: str = "",
                 evaluator: StepConditionEvaluator = None):
        self.graph_id = graph_id
        self.description = description
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._evaluator = evaluator

    # ── Graph Building ──

    def add_node(self, node: GraphNode):
        self.nodes[node.id] = node

    def add_edge(self, source: str, target: str, condition: str = "", label: str = ""):
        if source not in self.nodes or target not in self.nodes:
            raise ValueError(f"Edge references unknown node: {source} -> {target}")
        self.edges.append(GraphEdge(source=source, target=target,
                                     condition=condition, label=label))

    def remove_node(self, node_id: str):
        if node_id in self.nodes:
            del self.nodes[node_id]
        self.edges = [e for e in self.edges
                      if e.source != node_id and e.target != node_id]

    # ── Condition Evaluation ──

    def set_evaluator(self, evaluator: StepConditionEvaluator):
        """Set the condition evaluator for conditional edges."""
        self._evaluator = evaluator

    def evaluate_edge(self, edge: GraphEdge, context: dict) -> bool:
        """Evaluate a conditional edge against context."""
        if not edge.condition or not self._evaluator:
            return True
        return self._evaluator.evaluate(edge.condition, context)

    def get_active_successors(self, node_id: str, context: dict) -> list[str]:
        """Get successor nodes whose conditional edges pass evaluation."""
        return [e.target for e in self.get_successors(node_id)
                if self.evaluate_edge(e, context)]

    # ── Graph Analysis ──

    def get_entry_nodes(self) -> list[str]:
        """Nodes with no incoming edges."""
        targets = {e.target for e in self.edges}
        return [nid for nid in self.nodes if nid not in targets]

    def get_exit_nodes(self) -> list[str]:
        """Nodes with no outgoing edges."""
        sources = {e.source for e in self.edges}
        return [nid for nid in self.nodes if nid not in sources]

    def get_successors(self, node_id: str) -> list[GraphEdge]:
        """Get outgoing edges from a node."""
        return [e for e in self.edges if e.source == node_id]

    def get_predecessors(self, node_id: str) -> list[GraphEdge]:
        """Get incoming edges to a node."""
        return [e for e in self.edges if e.target == node_id]

    # ── WorkflowTopology Interface ──

    def entry_nodes(self) -> list[str]:
        """Nodes with no incoming edges (WorkflowTopology interface)."""
        targets = {e.target for e in self.edges}
        return [nid for nid in self.nodes if nid not in targets]

    def successors_of(self, node_id: str) -> list[str]:
        """IDs of nodes that directly follow node_id."""
        return [e.target for e in self.edges if e.source == node_id]

    def predecessors_of(self, node_id: str) -> list[str]:
        """IDs of nodes that directly precede node_id."""
        return [e.source for e in self.edges if e.target == node_id]

    def parallel_groups(self) -> list[list[str]]:
        """Return groups of nodes that can execute in parallel."""
        deps = {nid: set() for nid in self.nodes}
        for e in self.edges:
            deps[e.target].add(e.source)
            deps[e.target].update(deps.get(e.source, set()))
        groups = []
        remaining = set(self.nodes.keys())
        while remaining:
            group = []
            for nid in list(remaining):
                if all(nid not in deps.get(other, set()) and
                       other not in deps.get(nid, set())
                       for other in group):
                    group.append(nid)
            for nid in group:
                remaining.discard(nid)
            if group:
                groups.append(group)
        return groups

    def validate(self) -> list[str]:
        """Topological validation. Returns list of issues (empty = valid)."""
        issues = []
        if len(self.nodes) > 1:
            entry = self.entry_nodes()
            if not entry:
                issues.append("No entry nodes found")
            exits = self.get_exit_nodes()
            if not exits:
                issues.append("No exit nodes found")
        # Detect orphan nodes (not connected to any edge)
        connected = {e.source for e in self.edges} | {e.target for e in self.edges}
        orphans = [nid for nid in self.nodes if nid not in connected]
        for nid in orphans:
            issues.append(f"Orphan node '{nid}' not connected to any edge")
        for e in self.edges:
            if e.source not in self.nodes:
                issues.append(f"Edge source '{e.source}' not a known node")
            if e.target not in self.nodes:
                issues.append(f"Edge target '{e.target}' not a known node")
            if e.source == e.target:
                issues.append(f"Self-loop on node '{e.source}'")
            if self._evaluator and e.condition:
                ok, err = self._evaluator.validate(e.condition)
                if not ok:
                    issues.append(f"Invalid condition on {e.source}->{e.target}: {err}")
        return issues

    def topological_order(self) -> list[str]:
        """Return nodes in topological order (Kahn's algorithm)."""
        in_degree = {nid: 0 for nid in self.nodes}
        for e in self.edges:
            in_degree[e.target] = in_degree.get(e.target, 0) + 1

        queue = [nid for nid, d in in_degree.items() if d == 0]
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for e in self.get_successors(node):
                in_degree[e.target] -= 1
                if in_degree[e.target] == 0:
                    queue.append(e.target)

        if len(result) != len(self.nodes):
            _log.warning("Graph has cycles! Topological sort incomplete.")
        return result

    # ── Serialization ──

    def to_workflow_yaml(self) -> str:
        """Export graph as workflow YAML compatible with WorkflowOrchestrator."""
        lines = [
            "workflow:",
            f"  id: \"{self.graph_id}\"",
            "  version: \"1.0\"",
            f"  description: \"{self.description}\"",
            "  steps:",
        ]

        # Topological order for dependency resolution
        order = self.topological_order()
        for nid in order:
            node = self.nodes[nid]
            lines.append(f"    - id: \"{node.id}\"")
            lines.append(f"      agent: \"{node.agent}\"")
            if node.description:
                lines.append(f"      description: \"{node.description}\"")
            lines.append(f"      timeout: {node.timeout}")

            # Dependencies from incoming edges WITHOUT conditions
            deps = [e.source for e in self.get_predecessors(nid) if not e.condition]
            if deps:
                dep_str = json.dumps(deps[0] if len(deps) == 1 else deps)
                lines.append(f"      depends_on: {dep_str}")

            # Conditional edges → on_verdict handlers
            for e in self.get_successors(nid):
                if e.condition:
                    cond = e.condition.replace("'", "").replace('"', '')
                    if "approved" in cond:
                        lines.append("      on_verdict_approved:")
                        lines.append(f"        action: \"mark_complete\"")
                    elif "rejected" in cond:
                        lines.append("      on_verdict_rejected:")
                        lines.append(f"        next: \"{e.target}\"")

            # Output requirements
            if node.output_required:
                req = json.dumps(node.output_required)
                lines.append(f"      output:")
                lines.append(f"        required: {req}")

            # Retry
            if node.retry_max != 3:
                lines.append(f"      retry:")
                lines.append(f"        max: {node.retry_max}")

        lines.append(f"  error_policy:")
        lines.append(f"    max_rejections: 3")
        lines.append(f"    escalation_target: \"console\"")

        return '\n'.join(lines) + '\n'

    def to_json(self) -> dict:
        """Serialize graph to JSON-compatible dict."""
        return {
            "graph_id": self.graph_id,
            "description": self.description,
            "nodes": {
                nid: {
                    "id": n.id, "agent": n.agent,
                    "description": n.description, "timeout": n.timeout,
                    "output_required": n.output_required,
                }
                for nid, n in self.nodes.items()
            },
            "edges": [{
                "source": e.source, "target": e.target,
                "condition": e.condition, "label": e.label,
            } for e in self.edges],
        }

    @classmethod
    def from_json(cls, data: dict) -> "WorkflowGraph":
        """Deserialize graph from JSON dict."""
        g = cls(
            graph_id=data.get("graph_id", "custom"),
            description=data.get("description", ""),
        )
        for nid, nd in data.get("nodes", {}).items():
            g.add_node(GraphNode(
                id=nd["id"], agent=nd["agent"],
                description=nd.get("description", ""),
                timeout=nd.get("timeout", 600),
                output_required=nd.get("output_required", []),
            ))
        for ed in data.get("edges", []):
            g.add_edge(
                source=ed["source"], target=ed["target"],
                condition=ed.get("condition", ""),
                label=ed.get("label", ""),
            )
        return g

    @classmethod
    def from_registry(cls, agent_names: list[str],
                      graph_id: str = "custom") -> "WorkflowGraph":
        """Create a linear graph from a list of registered agent names."""
        from ..runtime.registry import AgentRegistry
        g = cls(graph_id=graph_id)
        prev = None
        for name in agent_names:
            agent = AgentRegistry.get(name)
            if not agent:
                raise ValueError(f"Unknown agent: {name}")
            nid = f"{name}_step"
            g.add_node(GraphNode(
                id=nid, agent=name,
                description=agent.description,
                timeout=agent.timeout,
                output_required=agent.output_required,
            ))
            if prev:
                g.add_edge(prev, nid)
            prev = nid
        return g
