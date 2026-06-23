"""
Workflow CLI — workflow YAML lifecycle management.

Usage:
    multiagent workflow create <template>   Create workflow from template
    multiagent workflow list                List available templates
    multiagent workflow validate <path>      Validate a workflow YAML
    multiagent workflow graph <path>         Show workflow as ASCII graph
"""

import sys
import yaml
from pathlib import Path

from .config.loader import find_workflow_yaml
from .runtime.registry import AgentRegistry, AgentConfig

WF_DIR = Path(__file__).resolve().parent.parent.parent / "architectures" / "dev-test-loop" / "workflow"

# ── Built-in Workflow Templates ──

TEMPLATES = {
    "linear": {
        "id": "linear-pipeline",
        "description": "A → B → C sequential pipeline",
        "steps": [
            {"id": "step_1", "agent": "", "timeout": 300, "depends_on": []},
            {"id": "step_2", "agent": "", "timeout": 300, "depends_on": ["step_1"]},
            {"id": "step_3", "agent": "", "timeout": 300, "depends_on": ["step_2"],
             "on_verdict_approved": {"action": "mark_complete"}},
        ],
    },
    "pm-dev-test": {
        "id": "pm-dev-test-loop",
        "description": "PM analyzes → Dev implements → Test verifies",
        "steps": [
            {"id": "pm_analyze", "agent": "pm", "timeout": 300,
             "output": {"required": ["root_cause", "target_module", "complexity", "task_breakdown", "estimated_files"]},
             "on_success": {"to_state": "assigned"}},
            {"id": "dev_fix", "agent": "dev", "timeout": 600,
             "depends_on": ["pm_analyze"],
             "output": {"required": ["branch_name", "files_changed", "commit_hash"]}},
            {"id": "test_verify", "agent": "test", "timeout": 300,
             "depends_on": ["dev_fix"],
             "output": {"required": ["verdict", "test_summary"]},
             "on_verdict_approved": {"action": "mark_complete"},
             "on_verdict_rejected": {"next": "dev_fix"}},
        ],
    },
    "diamond": {
        "id": "diamond-pipeline",
        "description": "PM → Dev1 + Dev2 (parallel) → Test",
        "steps": [
            {"id": "pm_analyze", "agent": "pm", "timeout": 300,
             "output": {"required": ["root_cause", "task_breakdown"]}},
            {"id": "dev_backend", "agent": "dev", "timeout": 600,
             "depends_on": ["pm_analyze"],
             "output": {"required": ["branch_name", "files_changed"]}},
            {"id": "dev_frontend", "agent": "dev", "timeout": 600,
             "depends_on": ["pm_analyze"],
             "output": {"required": ["branch_name", "files_changed"]}},
            {"id": "test_integration", "agent": "test", "timeout": 300,
             "depends_on": ["dev_backend", "dev_frontend"],
             "output": {"required": ["verdict", "test_summary"]},
             "on_verdict_rejected": {"next": "dev_backend"}},
        ],
    },
    "reviewer": {
        "id": "pm-dev-reviewer-test",
        "description": "PM → Dev → Reviewer → Test (4-step with code review)",
        "steps": [
            {"id": "pm_analyze", "agent": "pm", "timeout": 300,
             "output": {"required": ["root_cause", "task_breakdown", "complexity"]}},
            {"id": "dev_implement", "agent": "dev", "timeout": 600,
             "depends_on": ["pm_analyze"],
             "output": {"required": ["branch_name", "files_changed", "commit_hash"]}},
            {"id": "reviewer_check", "agent": "reviewer", "timeout": 300,
             "depends_on": ["dev_implement"],
             "output": {"required": ["verdict", "review_summary"]},
             "on_verdict_rejected": {"next": "dev_implement"}},
            {"id": "test_verify", "agent": "test", "timeout": 300,
             "depends_on": ["reviewer_check"],
             "output": {"required": ["verdict", "test_summary"]},
             "on_verdict_approved": {"action": "mark_complete"}},
        ],
    },
}


def _make_workflow_yaml(template_name, agents_override=None):
    """Build a workflow YAML dict from a named template."""
    t = TEMPLATES.get(template_name)
    if not t:
        return None

    steps = []
    for s in t["steps"]:
        step = dict(s)  # copy
        agent = agents_override.get(s["id"], s["agent"]) if agents_override else s["agent"]
        step["agent"] = agent
        steps.append(step)

    return {
        "workflow": {
            "id": t["id"],
            "version": "1.0",
            "description": t["description"],
            "steps": steps,
            "error_policy": {
                "max_rejections": 3,
                "escalation_target": "console",
            },
        }
    }


def _validate_workflow(workflow_dict):
    """Validate a workflow dict. Returns (issues, warnings)."""
    issues = []
    warnings = []
    wf = workflow_dict.get("workflow", {})
    steps = wf.get("steps", [])
    if not steps:
        issues.append("No steps defined in workflow")
        return issues, warnings

    step_ids = set()
    agent_names = set()
    for s in steps:
        sid = s.get("id", "")
        if not sid:
            issues.append("Step missing 'id' field")
            continue
        if sid in step_ids:
            issues.append(f"Duplicate step id: {sid}")
        step_ids.add(sid)

        agent = s.get("agent", "")
        if not agent:
            warnings.append(f"Step '{sid}' has no agent assigned (use --agent to set)")
        else:
            agent_names.add(agent)

    # Check agent references
    registered = set(AgentRegistry.list_names())
    for aname in agent_names:
        if aname and aname not in registered:
            warnings.append(f"Agent '{aname}' is not in the registry (may need: multiagent role create {aname})")

    # Check depends_on references
    for s in steps:
        sid = s.get("id", "")
        deps = s.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        for dep in deps:
            if dep not in step_ids:
                issues.append(f"Step '{sid}' depends_on '{dep}' which does not exist")

    # Check verdict routing references
    for s in steps:
        sid = s.get("id", "")
        rejected = s.get("on_verdict_rejected", {})
        if rejected:
            next_step = rejected.get("next", "")
            if next_step and next_step not in step_ids:
                issues.append(f"Step '{sid}' on_verdict_rejected.next='{next_step}' which does not exist")

    # Cycle detection — rejection loops are warnings, true cycles are issues
    cycles = _detect_cycles(steps)
    # Check which cycles involve rejection edges
    rejection_edges = set()
    for s in steps:
        rejected = s.get("on_verdict_rejected", {})
        if rejected and rejected.get("next"):
            rejection_edges.add((s["id"], rejected["next"]))
    for cycle in cycles:
        # If cycle contains a rejection edge, it's a controlled rejection loop
        is_rejection_loop = any(
            (cycle[i], cycle[(i + 1) % len(cycle)]) in rejection_edges
            for i in range(len(cycle))
        )
        msg = f"Rejection loop detected: {' → '.join(cycle)} (controlled by max_rejections)"
        if is_rejection_loop:
            warnings.append(msg)
        else:
            issues.append(f"Hard cycle detected: {' → '.join(cycle)}")

    # Check for orphan nodes (no incoming edges, not the first step, not terminal)
    if len(steps) > 1:
        has_incoming = set()
        for s in steps:
            deps = s.get("depends_on", [])
            if isinstance(deps, str):
                deps = [deps]
            for d in deps:
                has_incoming.add(d)
        for s in steps:
            # Terminal steps (have verdict handlers) are expected to have no dependents
            is_terminal = bool(s.get("on_verdict_rejected") or s.get("on_verdict_approved"))
            if s["id"] not in has_incoming and s.get("depends_on") and not is_terminal:
                warnings.append(f"Step '{s['id']}' has dependencies but no step depends on it (dead end)")

    return issues, warnings


def _detect_cycles(steps):
    """Detect cycles in step dependency graph. Returns list of cycle paths.

    Uses 3-color DFS: WHITE=unvisited, GRAY=in current path, BLACK=fully explored.
    A back edge to a GRAY node indicates a cycle.
    """
    step_ids = {s["id"] for s in steps}

    # Build adjacency: step_id → [dependent steps]
    edges = {sid: [] for sid in step_ids}
    for s in steps:
        deps = s.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        for dep in deps:
            if dep in step_ids:
                edges[dep].append(s["id"])  # dep → s (dep must finish before s)

    # Add conditional (rejection) edges
    for s in steps:
        rejected = s.get("on_verdict_rejected", {})
        if rejected and rejected.get("next"):
            nxt = rejected["next"]
            if nxt in step_ids:
                edges[s["id"]].append(nxt)  # s → nxt (if rejected, go back)

    # 3-color DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in step_ids}
    parent = {}
    cycles = []

    def dfs_visit(u):
        color[u] = GRAY
        for v in edges.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                # Back edge found — reconstruct cycle
                cycle = [v, u]
                cur = u
                while cur in parent and parent[cur] != v:
                    cur = parent[cur]
                    cycle.append(cur)
                cycle.append(v)
                cycles.append(cycle)
            elif color[v] == WHITE:
                parent[v] = u
                dfs_visit(v)
        color[u] = BLACK

    for sid in step_ids:
        if color[sid] == WHITE:
            dfs_visit(sid)

    return cycles


def _workflow_ascii_graph(workflow_dict):
    """Render workflow as ASCII graph."""
    wf = workflow_dict.get("workflow", {})
    steps = wf.get("steps", [])
    lines = [f"Workflow: {wf.get('id', 'unknown')}", f"Description: {wf.get('description', '')}", ""]
    for s in steps:
        sid = s["id"]
        agent = s.get("agent", "?")
        deps = s.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        dep_str = f" (depends: {', '.join(deps)})" if deps else ""
        lines.append(f"  [{agent}] {sid}{dep_str}")
        rejected = s.get("on_verdict_rejected", {})
        if rejected:
            lines.append(f"    ⤴ rejected → {rejected.get('next', '?')}")
        approved = s.get("on_verdict_approved", {})
        if approved:
            lines.append(f"    ⤵ approved → {approved.get('action', '?')}")
    return "\n".join(lines)


# ── CLI Commands ──


def cmd_workflow_create(argv):
    """Create a workflow YAML from a template."""
    import argparse
    parser = argparse.ArgumentParser(prog="multiagent workflow create", description="Create workflow from template")
    parser.add_argument("template", help="Template name (linear, pm-dev-test, diamond, reviewer)")
    parser.add_argument("--output", "-o", default="", help="Output YAML path")
    parser.add_argument("--validate", action="store_true", default=True, help="Validate after creation")
    args = parser.parse_args(argv)

    template_name = args.template
    if template_name not in TEMPLATES:
        print(f"Error: Unknown template '{template_name}'")
        print(f"Available: {', '.join(TEMPLATES.keys())}")
        print("Run 'multiagent workflow list' to see all templates")
        return 1

    wf_dict = _make_workflow_yaml(template_name)

    # Validate
    issues, warnings = _validate_workflow(wf_dict)
    for w in warnings:
        print(f"  ⚠️  {w}")
    for i in issues:
        print(f"  ❌ {i}")

    if issues and args.validate:
        print(f"\nWorkflow has {len(issues)} validation issue(s). Fix before using.")
        print("Tip: missing agents can be created with 'multiagent role create <name>'")
        return 1

    # Write YAML
    output_path = Path(args.output) if args.output else WF_DIR / f"{template_name}.yaml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.dump(wf_dict, default_flow_style=False, allow_unicode=True))
    print(f"\n✅ Workflow written to: {output_path}")
    print(f"   Steps: {len(wf_dict['workflow']['steps'])}")
    print(f"   Run: multiagent run {output_path} --dry-run")
    return 0


def cmd_workflow_list(argv):
    """List available workflow templates."""
    print("\nWorkflow Templates:")
    print(f"{'Name':<16} {'Steps':<8} Description")
    print("-" * 70)
    for name, t in TEMPLATES.items():
        steps = t["steps"]
        agents = ", ".join(s["agent"] or "?" for s in steps)
        print(f"{name:<16} {len(steps):<8} {t['description']}")
        print(f"  {'':>16} Agents: {agents}")
    print()
    return 0


def cmd_workflow_validate(argv):
    """Validate a workflow YAML file."""
    if not argv:
        print("Usage: multiagent workflow validate <path>")
        return 1

    path = Path(argv[0])
    if not path.exists():
        print(f"Error: File not found: {path}")
        return 1

    try:
        wf_dict = yaml.safe_load(path.read_text())
    except Exception as e:
        print(f"Error parsing YAML: {e}")
        return 1

    issues, warnings = _validate_workflow(wf_dict)

    print(f"\nValidating: {path}")
    print(f"  Workflow ID: {wf_dict.get('workflow', {}).get('id', 'unknown')}")
    print(f"  Steps: {len(wf_dict.get('workflow', {}).get('steps', []))}")

    for w in warnings:
        print(f"  ⚠️  {w}")
    for i in issues:
        print(f"  ❌ {i}")

    if issues:
        print(f"\n❌ {len(issues)} issue(s) found")
        return 1
    else:
        print(f"\n✅ Workflow is valid")
        return 0


def cmd_workflow_graph(argv):
    """Show ASCII graph of a workflow."""
    if not argv:
        print("Usage: multiagent workflow graph <path>")
        return 1

    path = Path(argv[0])
    if not path.exists():
        # Try template name
        if argv[0] in TEMPLATES:
            wf_dict = _make_workflow_yaml(argv[0])
            print(_workflow_ascii_graph(wf_dict))
            return 0
        print(f"Error: File not found: {path}")
        return 1

    try:
        wf_dict = yaml.safe_load(path.read_text())
    except Exception as e:
        print(f"Error parsing YAML: {e}")
        return 1

    print(_workflow_ascii_graph(wf_dict))
    return 0


def main():
    if len(sys.argv) < 2:
        print("Usage: multiagent workflow <create|list|validate|graph> [args]")
        return

    if sys.argv[1] == "workflow":
        cmd_idx = 2
    else:
        cmd_idx = 1

    if len(sys.argv) <= cmd_idx:
        print("Usage: multiagent workflow <create|list|validate|graph> [args]")
        return

    cmd = sys.argv[cmd_idx]
    args = sys.argv[cmd_idx + 1:]

    if cmd == "create":
        return cmd_workflow_create(args)
    elif cmd == "list":
        return cmd_workflow_list(args)
    elif cmd == "validate":
        return cmd_workflow_validate(args)
    elif cmd == "graph":
        return cmd_workflow_graph(args)
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: multiagent workflow <create|list|validate|graph> [args]")
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
