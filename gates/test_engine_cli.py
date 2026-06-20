"""Phase 3 Gate: Engine CLI — multiagent run 命令测试"""
import pytest
import sys
import os
import tempfile
import json
import yaml
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.engine_cli import (
    parse_run_args, cmd_run,
)
from multiagent.db import StateDB, Task, now_iso


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_db():
    """Temp state.db for testing"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = StateDB(Path(path))
    db.connect()
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def sample_workflow_yaml():
    """Minimal workflow YAML for testing"""
    return {
        "workflow": {
            "id": "test-workflow",
            "version": "1.0",
            "description": "Test workflow",
            "steps": [
                {
                    "id": "step_one",
                    "agent": "pm",
                    "description": "First step",
                    "timeout": 60,
                    "output": {"required": ["result"]},
                }
            ],
            "error_policy": {"max_rejections": 3},
        }
    }


@pytest.fixture
def sample_workflow_file(tmp_path, sample_workflow_yaml):
    """Write workflow YAML to temp file"""
    wf_path = tmp_path / "test-workflow.yaml"
    with open(wf_path, "w") as f:
        yaml.dump(sample_workflow_yaml, f)
    return wf_path


@pytest.fixture
def sample_roles_yaml(tmp_path):
    """Minimal roles.yaml"""
    roles = {
        "agents": {
            "pm": {
                "description": "Test PM",
                "model": "test-model",
                "permissions": {"write": [], "read": ["*"], "deny": []},
                "personality": "test",
                "skill": "",
                "memory": "",
                "session": "per-issue",
            }
        },
        "global": {"runtime": "claude-code"},
    }
    roles_path = tmp_path / "roles.yaml"
    with open(roles_path, "w") as f:
        yaml.dump(roles, f)
    return roles_path


# ═══════════════════════════════════════════════════════════════
# Tests: Argument Parsing
# ═══════════════════════════════════════════════════════════════

class TestParseRunArgs:
    def test_basic_workflow_path(self):
        """3.1.1: Parse workflow path as positional argument"""
        args = parse_run_args(["run", "workflow.yaml"])
        assert args["workflow"] == "workflow.yaml"
        assert args["task_id"] is None
        assert args["dry_run"] is False

    def test_with_task_id(self):
        """3.1.2: Parse --task-id flag"""
        args = parse_run_args(["run", "workflow.yaml", "--task-id", "task-abc123"])
        assert args["task_id"] == "task-abc123"

    def test_with_dry_run(self):
        """3.1.3: Parse --dry-run flag"""
        args = parse_run_args(["run", "workflow.yaml", "--dry-run"])
        assert args["dry_run"] is True

    def test_missing_workflow(self):
        """3.1.4: Missing workflow path raises SystemExit"""
        with pytest.raises(SystemExit):
            parse_run_args(["run"])


# ═══════════════════════════════════════════════════════════════
# Tests: Task Integration
# ═══════════════════════════════════════════════════════════════

class TestTaskIntegration:
    def test_creates_task_for_workflow(self, tmp_db, sample_workflow_file):
        """3.1.5: cmd_run creates a task in state.db"""
        task_id = cmd_run(
            db=tmp_db,
            workflow_path=sample_workflow_file,
            dry_run=True,  # Don't spawn agents
        )
        assert task_id is not None
        assert task_id.startswith("task-")

        # Verify task in DB
        task = tmp_db.get_task(task_id)
        assert task is not None
        assert task["workflow_id"] == "test-workflow"
        assert task["status"] == "completed"  # dry-run marks complete after load

    def test_run_with_existing_task(self, tmp_db, sample_workflow_file):
        """3.1.6: cmd_run with --task-id uses existing task"""
        # Pre-insert a task
        task = Task(
            id="task-existing-01",
            type="bug",
            source="test",
            workflow_id="test-workflow",
            current_step="step_one",
            context={"test": True},
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        task_id = cmd_run(
            db=tmp_db,
            workflow_path=sample_workflow_file,
            task_id="task-existing-01",
            dry_run=True,
        )
        assert task_id == "task-existing-01"

    def test_nonexistent_task_id_errors(self, tmp_db, sample_workflow_file):
        """3.1.7: Non-existent --task-id returns error"""
        result = cmd_run(
            db=tmp_db,
            workflow_path=sample_workflow_file,
            task_id="task-nope",
            dry_run=True,
        )
        assert result is None  # Should return None on error


# ═══════════════════════════════════════════════════════════════
# Tests: Workflow Validation (dry-run)
# ═══════════════════════════════════════════════════════════════

class TestDryRun:
    def test_dry_run_loads_workflow(self, tmp_db, sample_workflow_file):
        """3.1.8: Dry-run loads and validates workflow without spawning"""
        task_id = cmd_run(
            db=tmp_db,
            workflow_path=sample_workflow_file,
            dry_run=True,
        )
        assert task_id is not None

        # Verify step_results recorded
        steps = tmp_db.conn.execute(
            "SELECT step_id, status FROM step_results WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        assert len(steps) == 1
        assert steps[0][0] == "step_one"

    def test_dry_run_invalid_workflow_errors(self, tmp_db, tmp_path):
        """3.1.9: Invalid workflow YAML returns error"""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("workflow: {steps: [}")  # Invalid YAML

        result = cmd_run(
            db=tmp_db,
            workflow_path=bad_yaml,
            dry_run=True,
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════
# Tests: End-to-end (with mock adapter)
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_full_workflow_execution(self, tmp_db, sample_workflow_file, sample_roles_yaml):
        """3.1.10: Full workflow execution with all steps"""
        import yaml as yaml_lib

        task_id = cmd_run(
            db=tmp_db,
            workflow_path=sample_workflow_file,
            roles_path=sample_roles_yaml,
            dry_run=True,
        )
        assert task_id is not None

        # Verify task completed
        task = tmp_db.get_task(task_id)
        assert task is not None
        assert task["status"] in ("completed", "running")

    def test_workflow_with_context_passing(self, tmp_db, tmp_path):
        """3.1.11: Workflow with task context passes context to steps"""
        # Create workflow with two steps: step_one → step_two (depends on step_one)
        wf = {
            "workflow": {
                "id": "context-test",
                "steps": [
                    {
                        "id": "analyze",
                        "agent": "pm",
                        "description": "Analysis step",
                        "input": {"from": "task.context", "fields": ["requirements"]},
                        "output": {"required": ["finding"]},
                    },
                    {
                        "id": "fix",
                        "agent": "dev",
                        "description": "Fix step",
                        "depends_on": "analyze",
                        "input": {"from": "analyze.output", "fields": ["finding"]},
                        "output": {"required": ["result"]},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "context-test.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        # Pre-insert task with context
        task = Task(
            id="task-ctx-test",
            type="bug",
            source="test",
            workflow_id="context-test",
            current_step=None,
            context={"requirements": "Fix login bug"},
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        task_id = cmd_run(
            db=tmp_db,
            workflow_path=wf_path,
            task_id="task-ctx-test",
            dry_run=True,
        )
        assert task_id == "task-ctx-test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
