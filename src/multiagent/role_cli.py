"""
Role CLI — Agent role lifecycle management.

Usage:
    multiagent role create <name> [options]    Create a new agent role
    multiagent role list                       List all registered roles
    multiagent role show <name>                Show role details
    multiagent role delete <name>              Delete a role
    multiagent role clone <source> --name <n>  Clone an existing role
    multiagent role validate <name>            Validate role completeness

Role creation generates:
  - SKILL.md skeleton (architectures/.../skills/<name>/SKILL.md)
  - prompt.md skeleton (architectures/.../prompts/<name>.md)
  - Registration in roles.yaml
"""

import sys
import json
import shutil
from pathlib import Path

from .runtime.registry import AgentRegistry, AgentConfig
from .config.loader import find_roles_yaml

SKELETON_DIR = Path(__file__).resolve().parent.parent.parent / "architectures" / "dev-test-loop" / "templates"
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "architectures" / "dev-test-loop" / "skills"
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "architectures" / "dev-test-loop" / "prompts"


def _generate_skill(name, description, model, output_required, runtime):
    """Generate SKILL.md from skeleton template, filling known fields."""
    skeleton = SKELETON_DIR / "SKILL.md.skeleton"
    if not skeleton.exists():
        return None
    content = skeleton.read_text()

    fields_str = ",\n".join(f'  "{f}": "TODO: describe {f}"' for f in output_required)
    runtime_str = runtime or "claude-code"

    content = content.replace("{role_name_upper}", name.upper())
    content = content.replace("{role_description}", description or f"{name} agent role")
    content = content.replace("{trigger_description}", f"Workflow engine dispatches {name} step")
    content = content.replace("{model_name}", model or "deepseek/deepseek-chat")
    content = content.replace("{output_schema}", fields_str)

    # Write to skills/<name>/SKILL.md
    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(content)
    return skill_path


def _generate_prompt(name, description, output_required):
    """Generate prompt.md from skeleton template."""
    skeleton = SKELETON_DIR / "prompt.md.skeleton"
    if not skeleton.exists():
        return None
    content = skeleton.read_text()

    fields_str = ",\n".join(f'  "{f}": "..."' for f in output_required)
    example_str = ",\n".join(f'  "{f}": "example_{f}"' for f in output_required)

    content = content.replace("{role_name_title}", name.title())
    content = content.replace("{task_description}", description or f"Execute {name} step")
    content = content.replace("{output_schema}", fields_str)
    content = content.replace("{example_output}", example_str)
    content = content.replace("{example_input}", f"Example input for {name}")
    content = content.replace("{rule_1}", f"Follow the {name} workflow defined in SKILL.md")
    content = content.replace("{rule_2}", "Return JSON with all required fields")
    content = content.replace("{rule_3}", "Do not modify files outside your permission scope")

    prompt_dir = PROMPTS_DIR
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / f"{name}.md"
    prompt_path.write_text(content)
    return prompt_path


def cmd_role_create(argv):
    """Create a new agent role with SKILL.md + prompt.md skeletons."""
    import argparse
    parser = argparse.ArgumentParser(prog="multiagent role create", description="Create a new agent role")
    parser.add_argument("name", help="Role name (e.g. reviewer)")
    parser.add_argument("--description", default="", help="One-line role description")
    parser.add_argument("--model", default="deepseek/deepseek-chat", help="Model name")
    parser.add_argument("--runtime", default="claude-code", help="Runtime adapter (claude-code|opencode)")
    parser.add_argument("--write-paths", default="src/", help="Comma-separated write paths")
    parser.add_argument("--read-paths", default="src/,tests/,docs/", help="Comma-separated read paths")
    parser.add_argument("--deny-paths", default="", help="Comma-separated deny paths")
    parser.add_argument("--output-required", default="verdict,summary", help="Comma-separated required output fields")
    parser.add_argument("--timeout", type=int, default=600, help="Step timeout in seconds")
    parser.add_argument("--session", default="per-issue", help="Session type (per-issue|per-pr|persistent)")
    parser.add_argument("--personality", default="", help="Behavioral style hint")
    parser.add_argument("--no-skill", action="store_true", help="Skip SKILL.md generation")
    parser.add_argument("--no-prompt", action="store_true", help="Skip prompt.md generation")
    args = parser.parse_args(argv)

    name = args.name.strip().lower()
    if not name:
        print("Error: Role name is required")
        return 1

    # Reload from roles.yaml to check for existing
    roles_path = find_roles_yaml()
    if roles_path and roles_path.exists():
        AgentRegistry.load_from_yaml(roles_path)

    # Check if already registered
    if AgentRegistry.get(name):
        print(f"Error: Role '{name}' already exists in registry")
        return 1

    # Parse paths
    write_paths = [p.strip() for p in args.write_paths.split(",") if p.strip()]
    read_paths = [p.strip() for p in args.read_paths.split(",") if p.strip()]
    deny_paths = [p.strip() for p in args.deny_paths.split(",") if p.strip()]
    output_required = [f.strip() for f in args.output_required.split(",") if f.strip()]

    # Generate files
    skill_path = None
    prompt_path = None

    if not args.no_skill:
        skill_path = _generate_skill(name, args.description, args.model, output_required, args.runtime)
        if skill_path:
            print(f"  SKILL.md → {skill_path}")

    if not args.no_prompt:
        prompt_path = _generate_prompt(name, args.description, output_required)
        if prompt_path:
            print(f"  prompt.md → {prompt_path}")

    # Register in AgentRegistry
    config = AgentConfig(
        name=name,
        description=args.description or f"{name} agent",
        model=args.model,
        personality=args.personality,
        permissions={"write": write_paths, "read": read_paths, "deny": deny_paths},
        skill=str(skill_path.relative_to(SKILLS_DIR.parent.parent)) if skill_path else "",
        memory=f".agents/memory/{name}/",
        session=args.session,
        runtime=args.runtime,
        timeout=args.timeout,
        output_required=output_required,
    )
    AgentRegistry.register(config)

    # Update roles.yaml
    roles_path = find_roles_yaml()
    if roles_path and roles_path.exists():
        import yaml
        roles_data = yaml.safe_load(roles_path.read_text()) or {}
        agents = roles_data.setdefault("agents", {})
        agents[name] = {
            "description": config.description,
            "model": config.model,
            "personality": config.personality,
            "permissions": {
                "write": write_paths,
                "read": read_paths,
                "deny": deny_paths,
            },
            "skill": config.skill,
            "memory": config.memory,
            "session": config.session,
            "runtime": config.runtime,
            "timeout": config.timeout,
            "output": {"required": output_required},
        }
        roles_path.write_text(yaml.dump(roles_data, default_flow_style=False, allow_unicode=True))
        print(f"  roles.yaml updated: +{name}")

    print(f"\n✅ Role '{name}' created ({len(output_required)} output fields, timeout={args.timeout}s)")
    if skill_path:
        skill_rel = str(skill_path.relative_to(SKILLS_DIR.parent.parent))
        print(f"   SKILL.md: {skill_rel} ← fill with AI help, then run:")
    print(f"   multiagent role validate {name}")
    return 0


def cmd_role_list(argv):
    """List all registered agent roles."""
    # Reload from roles.yaml
    roles_path = find_roles_yaml()
    if roles_path and roles_path.exists():
        AgentRegistry.load_from_yaml(roles_path)
    agents = AgentRegistry.list_all()
    if not agents:
        print("No roles registered.")
        return 0

    print(f"\nRegistered Roles ({len(agents)}):")
    print(f"{'Name':<16} {'Model':<26} {'Timeout':<8} {'Output Fields'}")
    print("-" * 80)
    for a in agents:
        fields = ", ".join(a.output_required[:4])
        if len(a.output_required) > 4:
            fields += f" (+{len(a.output_required) - 4})"
        print(f"{a.name:<16} {a.model:<26} {a.timeout:<8} {fields}")
    print()
    return 0


def cmd_role_show(argv):
    """Show detailed configuration for a role."""
    if not argv:
        print("Usage: multiagent role show <name>")
        return 1
    name = argv[0]
    a = AgentRegistry.get(name)
    if not a:
        print(f"Role not found: {name}")
        return 1

    print(f"\nRole: {a.name}")
    print(f"  Description:    {a.description}")
    print(f"  Model:          {a.model}")
    print(f"  Runtime:        {a.runtime or 'default'}")
    print(f"  Personality:    {a.personality or '(not set)'}")
    print(f"  Timeout:        {a.timeout}s")
    print(f"  Session:        {a.session}")
    print(f"  Memory:         {a.memory or '(not set)'}")
    print(f"  Skill:          {a.skill or '(not set)'}")
    print(f"  Output Required: {a.output_required}")
    print(f"  Permissions:")
    print(f"    Write: {a.permissions.get('write', [])}")
    print(f"    Read:  {a.permissions.get('read', [])}")
    print(f"    Deny:  {a.permissions.get('deny', [])}")
    print()
    return 0


def cmd_role_delete(argv):
    """Delete a role from registry and optionally its files."""
    if not argv:
        print("Usage: multiagent role delete <name> [--files]")
        return 1
    opts = [a for a in argv if a.startswith("--")]
    args_list = [a for a in argv if not a.startswith("--")]
    if not args_list:
        print("Usage: multiagent role delete <name> [--files]")
        return 1

    name = args_list[0]
    remove_files = "--files" in opts

    # Reload from roles.yaml
    roles_path = find_roles_yaml()
    if roles_path and roles_path.exists():
        AgentRegistry.load_from_yaml(roles_path)

    if not AgentRegistry.get(name):
        print(f"Role not found: {name}")
        return 1

    AgentRegistry.unregister(name)

    # Remove from roles.yaml (reuse roles_path from above)
    if roles_path and roles_path.exists():
        import yaml
        roles_data = yaml.safe_load(roles_path.read_text()) or {}
        if "agents" in roles_data and name in roles_data["agents"]:
            del roles_data["agents"][name]
            roles_path.write_text(yaml.dump(roles_data, default_flow_style=False, allow_unicode=True))

    if remove_files:
        skill_dir = SKILLS_DIR / name
        prompt_file = PROMPTS_DIR / f"{name}.md"
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            print(f"  Removed: {skill_dir}")
        if prompt_file.exists():
            prompt_file.unlink()
            print(f"  Removed: {prompt_file}")

    print(f"✅ Role '{name}' deleted")
    return 0


def cmd_role_clone(argv):
    """Clone an existing role under a new name."""
    import argparse
    parser = argparse.ArgumentParser(prog="multiagent role clone", description="Clone an existing role")
    parser.add_argument("source", help="Source role name to clone from")
    parser.add_argument("--name", required=True, help="New role name")
    parser.add_argument("--description", default="", help="Override description")
    parser.add_argument("--model", default="", help="Override model")
    args = parser.parse_args(argv)

    # Reload from roles.yaml
    roles_path = find_roles_yaml()
    if roles_path and roles_path.exists():
        AgentRegistry.load_from_yaml(roles_path)

    source = AgentRegistry.get(args.source)
    if not source:
        print(f"Error: Source role '{args.source}' not found")
        return 1

    new_name = args.name.strip().lower()
    if AgentRegistry.get(new_name):
        print(f"Error: Role '{new_name}' already exists")
        return 1

    # Create new config from source
    config = AgentConfig(
        name=new_name,
        description=args.description or f"Cloned from {args.source}: {source.description}",
        model=args.model or source.model,
        personality=source.personality,
        permissions=dict(source.permissions),
        skill="",  # Will be set after generating
        memory=f".agents/memory/{new_name}/",
        session=source.session,
        runtime=source.runtime,
        timeout=source.timeout,
        output_required=list(source.output_required),
    )

    # Generate skill and prompt from source content if available
    skill_path = None
    prompt_path = None
    if source.skill:
        arch_dir = SKILLS_DIR.parent.parent  # architectures/
        src_skill = arch_dir / source.skill
        if src_skill.exists():
            skill_dir = SKILLS_DIR / new_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path = skill_dir / "SKILL.md"
            content = src_skill.read_text()
            content = content.replace(source.name.upper(), new_name.upper())
            content = content.replace(source.name, new_name)
            skill_path.write_text(content)
            config.skill = str(skill_path.relative_to(SKILLS_DIR.parent.parent))
            print(f"  SKILL.md → {skill_path} (cloned from {args.source})")

    # Clone prompt
    src_prompt = PROMPTS_DIR / f"{args.source}.md"
    if src_prompt.exists():
        prompt_dir = PROMPTS_DIR
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompt_dir / f"{new_name}.md"
        content = src_prompt.read_text()
        content = content.replace(source.name.title(), new_name.title())
        content = content.replace(source.name, new_name)
        prompt_path.write_text(content)
        print(f"  prompt.md → {prompt_path} (cloned from {args.source})")

    AgentRegistry.register(config)

    # Update roles.yaml
    roles_path = find_roles_yaml()
    if roles_path and roles_path.exists():
        import yaml
        roles_data = yaml.safe_load(roles_path.read_text()) or {}
        agents = roles_data.setdefault("agents", {})
        agents[new_name] = {
            "description": config.description,
            "model": config.model,
            "personality": config.personality,
            "permissions": {
                "write": config.permissions.get("write", []),
                "read": config.permissions.get("read", []),
                "deny": config.permissions.get("deny", []),
            },
            "skill": config.skill,
            "memory": config.memory,
            "session": config.session,
            "runtime": config.runtime,
            "timeout": config.timeout,
            "output": {"required": config.output_required},
        }
        roles_path.write_text(yaml.dump(roles_data, default_flow_style=False, allow_unicode=True))
        print(f"  roles.yaml updated: +{new_name}")

    print(f"\n✅ Role '{new_name}' cloned from '{args.source}'")
    return 0


def cmd_role_validate(argv):
    """Validate that a role has complete configuration files."""
    if not argv:
        print("Usage: multiagent role validate <name>")
        return 1
    name = argv[0]

    # Reload from roles.yaml to pick up recent additions
    roles_path = find_roles_yaml()
    if roles_path and roles_path.exists():
        AgentRegistry.load_from_yaml(roles_path)

    a = AgentRegistry.get(name)
    if not a:
        print(f"❌ Role '{name}' is not registered")
        return 1

    issues = []
    ok = []

    # Check SKILL.md
    if a.skill:
        skill_path = Path(a.skill)
        if not skill_path.is_absolute():
            # skill is relative to architectures/
            arch_dir = SKILLS_DIR.parent.parent  # architectures/
            skill_path = arch_dir / a.skill
        if skill_path.exists():
            content = skill_path.read_text()
            # Required sections
            for section in ["核心职责", "工作流", "权限边界", "输出格式"]:
                if section in content:
                    ok.append(f"SKILL.md has '{section}'")
                else:
                    issues.append(f"SKILL.md missing section: {section}")
            if '"Act, don\'t ask"' not in content and '"Act' not in content:
                issues.append("SKILL.md missing 'Act, don\\'t ask' section")
            else:
                ok.append("SKILL.md has behavioral rules")
        else:
            issues.append(f"SKILL.md not found at: {skill_path}")
    else:
        issues.append("No skill path configured")

    # Check prompt.md
    prompt_path = PROMPTS_DIR / f"{name}.md"
    if prompt_path.exists():
        content = prompt_path.read_text()
        if "## Output Format" in content or "output" in content.lower():
            ok.append("prompt.md has output format")
        else:
            issues.append("prompt.md missing output format")
        if "Few-Shot" in content or "few-shot" in content.lower():
            ok.append("prompt.md has few-shot example")
        else:
            issues.append("prompt.md missing few-shot example")
    else:
        issues.append(f"prompt.md not found at: {prompt_path}")

    # Check registry completeness
    if not a.output_required:
        issues.append("No output_required fields configured")
    if not a.permissions.get("write") and not a.permissions.get("deny"):
        issues.append("No permission scopes configured")

    # Print results
    print(f"\nValidating role '{name}':")
    for item in ok:
        print(f"  ✅ {item}")
    for item in issues:
        print(f"  ❌ {item}")

    if issues:
        print(f"\n{len(issues)} issue(s) found. Fill the skeletons with AI help:")
        print(f"  1. Edit {SKILLS_DIR / name / 'SKILL.md'}")
        print(f"  2. Edit {PROMPTS_DIR / f'{name}.md'}")
        print(f"  3. Copy content to Claude/GPT: 'Fill this skill file for a {name} agent'")
        print(f"  4. Run: multiagent role validate {name}")
        return 1

    print(f"\n✅ Role '{name}' is complete and ready to use")
    return 0


def main():
    # Handle both: multiagent role <cmd>  and direct import
    if len(sys.argv) >= 2 and sys.argv[1] == "role":
        cmd_idx = 2
    else:
        cmd_idx = 1

    if len(sys.argv) <= cmd_idx:
        print("Usage: multiagent role <create|list|show|delete|clone|validate> [args]")
        return

    cmd = sys.argv[cmd_idx]
    args = sys.argv[cmd_idx + 1:]

    if cmd == "create":
        return cmd_role_create(args)
    elif cmd == "list":
        return cmd_role_list(args)
    elif cmd == "show":
        return cmd_role_show(args)
    elif cmd == "delete":
        return cmd_role_delete(args)
    elif cmd == "clone":
        return cmd_role_clone(args)
    elif cmd == "validate":
        return cmd_role_validate(args)
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: multiagent role <create|list|show|delete|clone|validate> [args]")
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
