"""
RoleService — agent role CRUD business logic.

Extracted from role_cli.py. Depends on AgentRegistry for storage.
Uses logging, not print().
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from ..runtime.registry import AgentRegistry, AgentConfig
from ..config.loader import find_roles_yaml

_log = logging.getLogger("multiagent.services.role")

SKELETON_DIR = Path(__file__).resolve().parent.parent.parent / "architectures" / "dev-test-loop" / "templates"
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "architectures" / "dev-test-loop" / "skills"
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "architectures" / "dev-test-loop" / "prompts"


class RoleService:
    """Agent role lifecycle operations — create, list, get, delete, clone, validate."""

    @staticmethod
    def create_from_template(
        template_name: str,
        name: str,
        model: str = "deepseek/deepseek-chat",
        description: str = "",
        output_required: Optional[list[str]] = None,
        runtime: str = "claude-code",
        write_paths: Optional[list[str]] = None,
        read_paths: Optional[list[str]] = None,
        deny_paths: Optional[list[str]] = None,
        personality: str = "",
        timeout: int = 600,
        session: str = "per-issue",
        generate_skill: bool = True,
        generate_prompt: bool = True,
    ) -> AgentConfig:
        """Create a new agent role with optional SKILL.md + prompt.md skeletons.

        Args:
            template_name: Base template name (currently unused, reserved for future).
            name: Role name (lowercased internally).
            model: Model identifier.
            description: One-line role description.
            output_required: List of required output field names.
            runtime: Runtime adapter name.
            write_paths: List of writable paths.
            read_paths: List of readable paths.
            deny_paths: List of denied paths.
            personality: Behavioral style hint.
            timeout: Step timeout in seconds.
            session: Session type.
            generate_skill: Whether to write SKILL.md.
            generate_prompt: Whether to write prompt.md.

        Returns:
            The registered AgentConfig.

        Raises:
            ValueError: If role name already exists.
        """
        name = name.strip().lower()
        if not name:
            raise ValueError("Role name is required")

        # Reload from roles.yaml to check for existing
        RoleService._reload_registry()

        if AgentRegistry.get(name):
            raise ValueError(f"Role '{name}' already exists in registry")

        output_required = output_required or ["verdict", "summary"]
        write_paths = write_paths or ["src/"]
        read_paths = read_paths or ["src/", "tests/", "docs/"]
        deny_paths = deny_paths or []

        # Generate files
        skill_path = None
        prompt_path = None

        if generate_skill:
            skill_path = _generate_skill(name, description, model, output_required, runtime)
            if skill_path:
                _log.info("SKILL.md generated: %s", skill_path)

        if generate_prompt:
            prompt_path = _generate_prompt(name, description, output_required)
            if prompt_path:
                _log.info("prompt.md generated: %s", prompt_path)

        # Register in AgentRegistry
        config = AgentConfig(
            name=name,
            description=description or f"{name} agent",
            model=model,
            personality=personality,
            permissions={"write": write_paths, "read": read_paths, "deny": deny_paths},
            skill=str(skill_path.relative_to(SKILLS_DIR.parent.parent)) if skill_path else "",
            memory=f".agents/memory/{name}/",
            session=session,
            runtime=runtime,
            timeout=timeout,
            output_required=output_required,
        )
        AgentRegistry.register(config)

        # Persist to roles.yaml
        _persist_to_yaml(name, config)

        _log.info("Role '%s' created (%d output fields, timeout=%ds)",
                  name, len(output_required), timeout)
        return config

    @staticmethod
    def list_all() -> list[AgentConfig]:
        """List all registered agent roles."""
        RoleService._reload_registry()
        return AgentRegistry.list_all()

    @staticmethod
    def get(name: str) -> Optional[AgentConfig]:
        """Get a role by name."""
        RoleService._reload_registry()
        return AgentRegistry.get(name)

    @staticmethod
    def delete(name: str, remove_files: bool = False) -> bool:
        """Delete a role from registry and optionally its files.

        Returns True if the role was found and deleted, False otherwise.
        """
        RoleService._reload_registry()

        if not AgentRegistry.get(name):
            return False

        AgentRegistry.unregister(name)

        # Remove from roles.yaml
        roles_path = find_roles_yaml()
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
            if prompt_file.exists():
                prompt_file.unlink()

        _log.info("Role '%s' deleted", name)
        return True

    @staticmethod
    def clone(source_name: str, new_name: str, description: str = "", model: str = "") -> AgentConfig:
        """Clone an existing role under a new name.

        Args:
            source_name: Name of the existing role to clone.
            new_name: Name for the new cloned role.
            description: Override description (defaults to clone from source).
            model: Override model (defaults to source model).

        Returns:
            The new cloned AgentConfig.

        Raises:
            ValueError: If source role not found or new name already exists.
        """
        RoleService._reload_registry()

        source = AgentRegistry.get(source_name)
        if not source:
            raise ValueError(f"Source role '{source_name}' not found")

        new_name_clean = new_name.strip().lower()
        if AgentRegistry.get(new_name_clean):
            raise ValueError(f"Role '{new_name_clean}' already exists")

        # Create new config from source
        config = AgentConfig(
            name=new_name_clean,
            description=description or f"Cloned from {source_name}: {source.description}",
            model=model or source.model,
            personality=source.personality,
            permissions=dict(source.permissions),
            skill="",
            memory=f".agents/memory/{new_name_clean}/",
            session=source.session,
            runtime=source.runtime,
            timeout=source.timeout,
            output_required=list(source.output_required),
        )

        # Generate skill and prompt from source content if available
        skill_path = None
        if source.skill:
            arch_dir = SKILLS_DIR.parent.parent
            src_skill = arch_dir / source.skill
            if src_skill.exists():
                skill_dir = SKILLS_DIR / new_name_clean
                skill_dir.mkdir(parents=True, exist_ok=True)
                skill_path = skill_dir / "SKILL.md"
                content = src_skill.read_text()
                content = content.replace(source.name.upper(), new_name_clean.upper())
                content = content.replace(source.name, new_name_clean)
                skill_path.write_text(content)
                config.skill = str(skill_path.relative_to(SKILLS_DIR.parent.parent))
                _log.info("SKILL.md cloned: %s", skill_path)

        # Clone prompt
        src_prompt = PROMPTS_DIR / f"{source_name}.md"
        if src_prompt.exists():
            prompt_dir = PROMPTS_DIR
            prompt_dir.mkdir(parents=True, exist_ok=True)
            prompt_path = prompt_dir / f"{new_name_clean}.md"
            content = src_prompt.read_text()
            content = content.replace(source.name.title(), new_name_clean.title())
            content = content.replace(source.name, new_name_clean)
            prompt_path.write_text(content)
            _log.info("prompt.md cloned: %s", prompt_path)

        AgentRegistry.register(config)

        # Persist to roles.yaml
        _persist_to_yaml(new_name_clean, config)

        _log.info("Role '%s' cloned from '%s'", new_name_clean, source_name)
        return config

    @staticmethod
    def validate(name: str) -> list[str]:
        """Validate that a role has complete configuration files.

        Returns a list of issues (empty = valid).
        """
        RoleService._reload_registry()

        a = AgentRegistry.get(name)
        if not a:
            return [f"Role '{name}' is not registered"]

        issues: list[str] = []

        # Check SKILL.md
        if a.skill:
            skill_path = Path(a.skill)
            if not skill_path.is_absolute():
                arch_dir = SKILLS_DIR.parent.parent
                skill_path = arch_dir / a.skill
            if skill_path.exists():
                content = skill_path.read_text()
                for section in ["核心职责", "工作流", "权限边界", "输出格式"]:
                    if section not in content:
                        issues.append(f"SKILL.md missing section: {section}")
                if '"Act, don\'t ask"' not in content:
                    issues.append("SKILL.md missing 'Act, don\\'t ask' section")
            else:
                issues.append(f"SKILL.md not found at: {skill_path}")
        else:
            issues.append("No skill path configured")

        # Check prompt.md
        prompt_path = PROMPTS_DIR / f"{name}.md"
        if prompt_path.exists():
            content = prompt_path.read_text()
            if "## Output Format" not in content and "output" not in content.lower():
                issues.append("prompt.md missing output format")
            if "Few-Shot" not in content and "few-shot" not in content.lower():
                issues.append("prompt.md missing few-shot example")
        else:
            issues.append(f"prompt.md not found at: {prompt_path}")

        # Check registry completeness
        if not a.output_required:
            issues.append("No output_required fields configured")
        if not a.permissions.get("write") and not a.permissions.get("deny"):
            issues.append("No permission scopes configured")

        return issues

    # ── Internal ───────────────────────────────────────────────────────

    @staticmethod
    def _reload_registry():
        """Reload agent registry from roles.yaml."""
        roles_path = find_roles_yaml()
        if roles_path and roles_path.exists():
            AgentRegistry.load_from_yaml(roles_path)


def _generate_skill(name: str, description: str, model: str,
                    output_required: list[str], runtime: str) -> Optional[Path]:
    """Generate SKILL.md from skeleton template."""
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

    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(content)
    return skill_path


def _generate_prompt(name: str, description: str, output_required: list[str]) -> Optional[Path]:
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


def _persist_to_yaml(name: str, config: AgentConfig):
    """Write a role entry into roles.yaml."""
    roles_path = find_roles_yaml()
    if not roles_path or not roles_path.exists():
        return

    import yaml
    roles_data = yaml.safe_load(roles_path.read_text()) or {}
    agents = roles_data.setdefault("agents", {})
    agents[name] = {
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
