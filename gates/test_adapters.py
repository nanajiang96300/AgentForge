"""Gate: Adapter CLI building + config validation"""
import sys
from pathlib import Path
from multiagent.adapters import create, ClaudeCodeAdapter, list_adapters
from multiagent.engine import load_yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROLES = PROJECT_ROOT / "architectures" / "dev-test-loop" / "config" / "roles.yaml"

DEV_CONFIG = {
    "skill": "architectures/dev-test-loop/skills/dev/SKILL.md",
    "permissions": {"deny": ["tests/", "docs/"]},
}
FAKE_STEP = {"id": "test", "agent": "dev"}

def test_adapters_registered():
    adapters = list_adapters()
    assert "claude-code" in adapters
    assert "opencode" in adapters
    return True

def test_roles_loads():
    config = load_yaml(ROLES)
    assert "agents" in config
    assert "global" in config
    return True

def test_claude_build_command():
    cc = ClaudeCodeAdapter(PROJECT_ROOT)
    cmd = cc.build_command(DEV_CONFIG, "Test task", FAKE_STEP)
    assert "claude" in cmd[0]
    assert "-p" in cmd
    assert "--output-format" in cmd
    assert "json" in cmd
    assert "--disallowedTools" in cmd
    assert "--bare" in cmd
    return True

def test_disallowed_patterns():
    cc = ClaudeCodeAdapter(PROJECT_ROOT)
    cmd = cc.build_command(DEV_CONFIG, "task", FAKE_STEP)
    idx = cmd.index("--disallowedTools")
    disallowed = cmd[idx + 1]
    assert "Write(tests/*)" in disallowed
    assert "Write(docs/*)" in disallowed
    return True

def test_no_invented_flags():
    cc = ClaudeCodeAdapter(PROJECT_ROOT)
    cmd = cc.build_command(DEV_CONFIG, "task", FAKE_STEP)
    for flag in ["--agent", "--skill", "--memory", "--task-id"]:
        assert flag not in cmd, f"Invented flag {flag} found!"
    return True

if __name__ == "__main__":
    tests = [
        ("Adapters registered", test_adapters_registered),
        ("Roles loads", test_roles_loads),
        ("Build command", test_claude_build_command),
        ("Disallowed patterns", test_disallowed_patterns),
        ("No invented flags", test_no_invented_flags),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {name}: {e}")
    print(f"  {passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
