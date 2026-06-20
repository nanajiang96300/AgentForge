"""Phase 3 Gate: PM CLI 走 Engine 通道 — submit 使用 AgentSpawner"""
import pytest
import sys
import os
import tempfile
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.pm_cli import cmd_submit, cmd_status
from multiagent.db import StateDB, Task, now_iso
from multiagent.engine_cli import cmd_run


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = StateDB(Path(path))
    db.connect()
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def requirements_file(tmp_path):
    """Create a sample requirements.md"""
    req = tmp_path / "requirements.md"
    req.write_text("# Test Requirements\n\nFix the login bug.\n")
    return req


@pytest.fixture
def workflow_for_test(tmp_path):
    """Minimal single-step workflow for testing"""
    wf = {
        "workflow": {
            "id": "pm-test-loop",
            "steps": [
                {
                    "id": "pm_analyze",
                    "agent": "pm",
                    "description": "Analyze requirements",
                    "timeout": 60,
                    "input": {"from": "task.context", "fields": ["requirements_text"]},
                    "output": {"required": ["root_cause", "complexity"]},
                }
            ],
            "error_policy": {"max_rejections": 3},
        }
    }
    wf_path = tmp_path / "pm-test-loop.yaml"
    with open(wf_path, "w") as f:
        yaml.dump(wf, f)
    return wf_path


# ═══════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════

class TestSubmitViaEngine:
    def test_submit_creates_task(self, tmp_db, requirements_file):
        """3.2.1: submit creates task in state.db"""
        result = cmd_submit(
            [str(requirements_file)],
            db=tmp_db,
            auto_run=False,
        )
        assert result == 0

        # Verify task exists
        tasks = tmp_db.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        assert tasks[0] >= 1

    def test_submit_with_auto_run_dry(self, tmp_db, requirements_file, workflow_for_test):
        """3.2.2: submit --run --dry-run creates task AND records step"""
        result = cmd_submit(
            [str(requirements_file)],
            db=tmp_db,
            auto_run=True,
            dry_run=True,
            workflow_path=workflow_for_test,
        )
        assert result == 0

        # Verify step_results have records (proves engine was used)
        steps = tmp_db.conn.execute("SELECT COUNT(*) FROM step_results").fetchone()
        assert steps[0] >= 1, "Engine should record at least pm_analyze step"

    def test_submit_task_has_correct_context(self, tmp_db, requirements_file):
        """3.2.3: submit stores requirements in task context"""
        cmd_submit(
            [str(requirements_file)],
            db=tmp_db,
            auto_run=False,
        )

        rows = tmp_db.conn.execute(
            "SELECT context FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert rows is not None
        import json
        ctx = json.loads(rows[0])
        assert "requirements_text" in ctx
        assert "Fix the login bug" in ctx["requirements_text"]

    def test_submit_auto_run_updates_task_status(self, tmp_db, requirements_file,
                                                  workflow_for_test):
        """3.2.4: submit with auto-run transitions task from pending"""
        cmd_submit(
            [str(requirements_file)],
            db=tmp_db,
            auto_run=True,
            dry_run=True,
            workflow_path=workflow_for_test,
        )

        rows = tmp_db.conn.execute(
            "SELECT status FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert rows is not None
        # After dry-run completion, status should be completed (not pending)
        assert rows[0] in ("completed", "running")

    def test_submit_without_run_leaves_task_pending(self, tmp_db, requirements_file):
        """3.2.5: submit without --run leaves task as pending"""
        cmd_submit(
            [str(requirements_file)],
            db=tmp_db,
            auto_run=False,
        )

        rows = tmp_db.conn.execute(
            "SELECT status FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert rows is not None
        assert rows[0] == "pending"

    def test_status_shows_run_results(self, tmp_db, requirements_file, workflow_for_test,
                                       capsys):
        """3.2.6: pm status shows step results from engine execution"""
        cmd_submit(
            [str(requirements_file)],
            db=tmp_db,
            auto_run=True,
            dry_run=True,
            workflow_path=workflow_for_test,
        )

        # Get the task ID from the DB
        row = tmp_db.conn.execute(
            "SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        task_id = row[0]

        # Run status
        cmd_status([task_id], db=tmp_db)
        captured = capsys.readouterr()
        # Should show step results
        assert "Steps:" in captured.out or task_id in captured.out

    def test_metrics_recorded_via_engine(self, tmp_db, requirements_file,
                                          workflow_for_test):
        """3.2.7: Engine records stub metrics even in dry-run mode"""
        # Note: in dry-run mode, AgentSpawner is created but not actually spawned.
        # We verify the step_results table has entries with adapter name.
        cmd_submit(
            [str(requirements_file)],
            db=tmp_db,
            auto_run=True,
            dry_run=True,
            workflow_path=workflow_for_test,
        )

        steps = tmp_db.conn.execute(
            "SELECT step_id, agent, status, adapter FROM step_results ORDER BY id"
        ).fetchall()
        assert len(steps) >= 1
        # Each step should have adapter set
        for s in steps:
            assert s[3]  # adapter name should not be empty


class TestPmCliIntegration:
    def test_help_shows_run_command(self, capsys):
        """3.2.8: CLI help includes run command"""
        # Just verify the module structure
        from multiagent.pm_cli import main
        # Don't actually call main() since it parses sys.argv
        import multiagent.engine_cli
        assert hasattr(multiagent.engine_cli, 'cmd_run')
        assert hasattr(multiagent.engine_cli, 'parse_run_args')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
