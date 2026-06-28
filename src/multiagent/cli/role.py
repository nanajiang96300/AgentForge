"""
CLI for role — agent role lifecycle management.

Usage:
    multiagent role create <name> [options]    Create a new agent role
    multiagent role list                       List all registered roles
    multiagent role show <name>                Show role details
    multiagent role delete <name>              Delete a role
    multiagent role clone <source> --name <n>  Clone an existing role
    multiagent role validate <name>            Validate role completeness
"""

import sys
from pathlib import Path

from ..services.role_service import RoleService, RoleTemplateService
from ..config.loader import find_roles_yaml


# ── CLI Commands ──

def cmd_role_create(argv):
    """Create a new agent role."""
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
    parser.add_argument("--from-template", default=None,
        help="Create from built-in template (architect, security-auditor, code-reviewer, performance-optimizer)")
    args = parser.parse_args(argv)

    name = args.name.strip().lower()
    if not name:
        print("Error: Role name is required")
        return 1

    # If --from-template is used, delegate to RoleTemplateService
    if args.from_template:
        try:
            from ..services.role_service import RoleTemplateService
            svc = RoleTemplateService()
            config = svc.create_from_template(args.from_template, name)
            print(f"\n✅ Role '{name}' created from template '{args.from_template}'")
            print(f"   Description: {config.description}")
            print(f"   Output fields: {config.output_required}")
            return 0
        except ValueError as e:
            print(f"Error: {e}")
            return 1

    try:
        config = RoleService.create_from_template(
            template_name="default",
            name=name,
            model=args.model,
            description=args.description,
            runtime=args.runtime,
            output_required=[f.strip() for f in args.output_required.split(",") if f.strip()],
            write_paths=[p.strip() for p in args.write_paths.split(",") if p.strip()],
            read_paths=[p.strip() for p in args.read_paths.split(",") if p.strip()],
            deny_paths=[p.strip() for p in args.deny_paths.split(",") if p.strip()],
            personality=args.personality,
            timeout=args.timeout,
            session=args.session,
            generate_skill=not args.no_skill,
            generate_prompt=not args.no_prompt,
        )
        print(f"\n✅ Role '{name}' created ({len(config.output_required)} output fields, timeout={args.timeout}s)")
        return 0
    except ValueError as e:
        print(f"Error: {e}")
        return 1


def cmd_role_list(argv):
    """List all registered agent roles."""
    agents = RoleService.list_all()
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
    a = RoleService.get(name)
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

    if RoleService.delete(name, remove_files=remove_files):
        print(f"✅ Role '{name}' deleted")
        return 0
    else:
        print(f"Role not found: {name}")
        return 1


def cmd_role_clone(argv):
    """Clone an existing role under a new name."""
    import argparse
    parser = argparse.ArgumentParser(prog="multiagent role clone", description="Clone an existing role")
    parser.add_argument("source", help="Source role name to clone from")
    parser.add_argument("--name", required=True, help="New role name")
    parser.add_argument("--description", default="", help="Override description")
    parser.add_argument("--model", default="", help="Override model")
    args = parser.parse_args(argv)

    try:
        config = RoleService.clone(
            source_name=args.source,
            new_name=args.name,
            description=args.description,
            model=args.model,
        )
        print(f"\n✅ Role '{config.name}' cloned from '{args.source}'")
        return 0
    except ValueError as e:
        print(f"Error: {e}")
        return 1


def cmd_role_validate(argv):
    """Validate that a role has complete configuration files."""
    if not argv:
        print("Usage: multiagent role validate <name>")
        return 1
    name = argv[0]

    issues = RoleService.validate(name)

    if not RoleService.get(name):
        print(f"❌ Role '{name}' is not registered")
        return 1

    print(f"\nValidating role '{name}':")
    # Re-validate to get individual item results
    # RoleService.validate returns issues; we need ok items too
    a = RoleService.get(name)

    # Print check items
    from pathlib import Path
    from ..config.loader import find_roles_yaml
    from ..services.role_service import SKILLS_DIR, PROMPTS_DIR

    ok_items = []
    if a and a.skill:
        skill_path = Path(a.skill)
        if not skill_path.is_absolute():
            skill_path = SKILLS_DIR.parent.parent / a.skill
        if skill_path.exists():
            content = skill_path.read_text()
            for section in ["核心职责", "工作流", "权限边界", "输出格式"]:
                if section in content and f"SKILL.md missing section: {section}" not in issues:
                    ok_items.append(f"SKILL.md has '{section}'")
            if '"Act, don\'t ask"' in content and f"SKILL.md missing 'Act, don\\'t ask' section" not in issues:
                ok_items.append("SKILL.md has behavioral rules")

    prompt_path = PROMPTS_DIR / f"{name}.md"
    if prompt_path.exists():
        content = prompt_path.read_text()
        if ("## Output Format" in content or "output" in content.lower()) and \
           "prompt.md missing output format" not in issues:
            ok_items.append("prompt.md has output format")
        if ("Few-Shot" in content or "few-shot" in content.lower()) and \
           "prompt.md missing few-shot example" not in issues:
            ok_items.append("prompt.md has few-shot example")

    for item in ok_items:
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


def cmd_role_list_templates(argv):
    """List available role templates (built-in + user-defined)."""
    svc = RoleTemplateService()
    builtins = svc.list_builtins()

    if not builtins:
        print("No templates available.")
        return 0

    print(f"\nAvailable Role Templates ({len(builtins)}):")
    print(f"{'Name':<24} {'Description'}")
    print("-" * 80)
    for tpl_name in builtins:
        try:
            data = svc.load(tpl_name)
            desc = data.get("description", "")[:52]
            if len(data.get("description", "")) > 52:
                desc += "..."
        except ValueError:
            desc = "(could not load)"
        print(f"{tpl_name:<24} {desc}")
    print()
    return 0


def cmd_role_show_template(argv):
    """Show details for a specific template."""
    import argparse
    parser = argparse.ArgumentParser(prog="multiagent role show-template", description="Show template details")
    parser.add_argument("name", help="Template name")
    args = parser.parse_args(argv)

    svc = RoleTemplateService()
    try:
        data = svc.load(args.name)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    print(f"\nTemplate: {args.name}")
    print(f"  Description:    {data.get('description', '(not set)')}")
    print(f"  Model:          {data.get('model', '(not set)')}")
    print(f"  Personality:    {data.get('personality', '(not set)')}")
    print(f"  Timeout:        {data.get('timeout', 600)}s")
    print(f"  Session:        {data.get('session', 'per-issue')}")
    print(f"  Skill:          {data.get('skill', '(not set)')}")
    print(f"  Memory:         {data.get('memory', '(not set)')}")
    print(f"  Output Required: {data.get('output_required', [])}")
    perms = data.get('permissions', {})
    print(f"  Permissions:")
    print(f"    Write: {perms.get('write', [])}")
    print(f"    Read:  {perms.get('read', [])}")
    print(f"    Deny:  {perms.get('deny', [])}")
    print()
    return 0


def main():
    # Handle both: multiagent role <cmd>  and direct import
    if len(sys.argv) >= 2 and sys.argv[1] == "role":
        cmd_idx = 2
    else:
        cmd_idx = 1

    if len(sys.argv) <= cmd_idx:
        print("Usage: multiagent role <create|list|show|delete|clone|validate|list-templates|show-template> [args]")
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
    elif cmd == "list-templates":
        return cmd_role_list_templates(args)
    elif cmd == "show-template":
        return cmd_role_show_template(args)
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: multiagent role <create|list|show|delete|clone|validate|list-templates|show-template> [args]")
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
