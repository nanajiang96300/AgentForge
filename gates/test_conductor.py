"""Phase 4 Gate: Conductor + 全链路自动化"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.db import StateDB, Task, now_iso
from multiagent.engine import AgentSpawner, StepResult, StepStatus
from multiagent.orchestrator import WorkflowOrchestrator
from multiagent.conductor import Conductor


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
def workflow_yaml(tmp_path):
    """Minimal 3-step PM→Dev→Test workflow for conductor tests"""
    wf = {
        "workflow": {
            "id": "conductor-test",
            "version": "1.0",
            "description": "Test workflow for conductor",
            "steps": [
                {
                    "id": "pm_analyze",
                    "agent": "pm",
                    "description": "Analyze requirements",
                    "timeout": 30,
                    "output": {"required": ["root_cause", "task_breakdown"]},
                    "on_success": {"to_state": "assigned"},
                    "on_failure": {"escalate_on_exhaust": True},
                    "retry": {"max": 1},
                },
                {
                    "id": "dev_fix",
                    "agent": "dev",
                    "description": "Implement changes",
                    "depends_on": "pm_analyze",
                    "timeout": 30,
                    "output": {"required": ["files_changed"]},
                    "input": {
                        "from": "pm_analyze.output",
                        "fields": ["root_cause", "task_breakdown"],
                    },
                    "on_success": {"action": "transition_to_in_review"},
                    "retry": {"max": 1},
                },
                {
                    "id": "test_verify",
                    "agent": "test",
                    "description": "Run tests and verify",
                    "depends_on": "dev_fix",
                    "timeout": 30,
                    "output": {"required": ["verdict"]},
                    "input": {
                        "from": "dev_fix.output",
                        "fields": ["files_changed"],
                    },
                    "on_verdict_approved": {"action": "mark_complete"},
                    "on_verdict_rejected": {
                        "next": "dev_fix",
                        "increment_rejection": True,
                    },
                },
            ],
            "error_policy": {
                "max_rejections": 3,
                "escalation_target": "console",
            },
        }
    }
    wf_path = tmp_path / "conductor-test.yaml"
    with open(wf_path, "w") as f:
        yaml.dump(wf, f)
    return wf_path


def _make_task(task_id, workflow_id, status="pending", current_step=None, **kwargs):
    """Helper to create a Task with all required fields."""
    return Task(
        id=task_id,
        type=kwargs.get("type", "bug"),
        source=kwargs.get("source", "test"),
        workflow_id=workflow_id,
        current_step=current_step,
        status=status,
        retry_count=kwargs.get("retry_count", 0),
        rejection_count=kwargs.get("rejection_count", 0),
        dedup_key=kwargs.get("dedup_key"),
        context=kwargs.get("context"),
        created_at=kwargs.get("created_at", now_iso()),
        claimed_at=kwargs.get("claimed_at"),
        completed_at=kwargs.get("completed_at"),
    )


# ═══════════════════════════════════════════════════════════════
# Mock adapter helpers
# ═══════════════════════════════════════════════════════════════

def _make_sleep_adapter(outputs=None, parse_fn=None):
    """Create a sleep-based mock adapter with configurable parse_output."""
    class SleepMockAdapter:
        def __init__(self):
            self.project_root = Path.cwd()
            self.spawn_log = []

        def name(self):
            return "sleep-mock"

        def build_command(self, agent_config, task_prompt, step):
            sleep_time = step.get("sleep", 0.05)
            return ["sleep", str(sleep_time)]

        def parse_output(self, stdout, stderr):
            if parse_fn:
                return parse_fn()
            return StepResult(
                step_id="", agent="", status=StepStatus.COMPLETED,
                output=outputs or {
                    "result": "ok",
                    "root_cause": "Bug in module X",
                    "task_breakdown": ["Fix module X"],
                    "files_changed": ["src/module.py"],
                    "verdict": "approved",
                },
            )

        def get_tool_restriction_flags(self, permissions):
            return [], []

        def _paths_to_tool_patterns(self, deny_paths, write_paths):
            return [], []

    return SleepMockAdapter()


# ═══════════════════════════════════════════════════════════════
# Tests: Conductor Core (4.1)
# ═══════════════════════════════════════════════════════════════

class TestConductorCore:
    """4.1: Conductor auto-trigger and core functionality"""

    def test_conductor_initialization(self, tmp_db, workflow_yaml):
        """Conductor initializes with valid db and workflow paths"""
        c = Conductor(
            db_path=tmp_db.db_path,
            workflow_path=workflow_yaml,
            poll_interval=1,
        )
        assert c.state.running is False
        assert c.poll_interval == 1

    def test_conductor_status_stopped(self, tmp_db, workflow_yaml):
        """Conductor status shows stopped when not running"""
        c = Conductor(
            db_path=tmp_db.db_path,
            workflow_path=workflow_yaml,
        )
        status = c.status()
        assert status["conductor"]["running"] is False
        assert "pending" in status["queue"]
        assert "escalated" in status["queue"]

    def test_conductor_process_one_no_tasks(self, tmp_db, workflow_yaml):
        """process_one returns None when no pending tasks"""
        c = Conductor(
            db_path=tmp_db.db_path,
            workflow_path=workflow_yaml,
        )
        result = c.process_one()
        assert result is None

    @patch("multiagent.engine_cli.cmd_run")
    def test_conductor_process_one_with_task(self, mock_cmd_run, tmp_db, workflow_yaml):
        """process_one calls cmd_run for a pending task"""
        mock_cmd_run.return_value = "task-test-001"

        task = _make_task("task-test-001", "conductor-test",
                          current_step="pm_analyze",
                          context={"requirements_text": "Test requirement"})
        tmp_db.insert_task(task)

        c = Conductor(
            db_path=tmp_db.db_path,
            workflow_path=workflow_yaml,
        )

        result = c.process_one()
        assert result == "task-test-001"
        assert mock_cmd_run.called
        assert c.state.tasks_processed == 1

    @patch("multiagent.engine_cli.cmd_run")
    def test_conductor_process_one_cmd_run_failure(self, mock_cmd_run, tmp_db, workflow_yaml):
        """process_one handles cmd_run returning None"""
        mock_cmd_run.return_value = None

        task = _make_task("task-fail", "conductor-test",
                          current_step="pm_analyze",
                          context={"requirements_text": "Test"})
        tmp_db.insert_task(task)

        c = Conductor(
            db_path=tmp_db.db_path,
            workflow_path=workflow_yaml,
        )
        result = c.process_one()
        assert result is None
        assert c.state.tasks_failed == 1

    def test_conductor_status_shows_queue_counts(self, tmp_db, workflow_yaml):
        """Conductor status includes pending/running/escalated counts"""
        for i, status in enumerate(["pending", "pending", "escalated"]):
            task = _make_task(
                f"task-{status}-{i}", "conductor-test",
                status="pending",  # Insert as pending first
                current_step=None,
            )
            tmp_db.insert_task(task)
            if status != "pending":
                tmp_db.update_task_status(f"task-{status}-{i}", status)

        c = Conductor(
            db_path=tmp_db.db_path,
            workflow_path=workflow_yaml,
        )
        status = c.status()
        assert status["queue"]["pending"] >= 1
        assert status["queue"]["escalated"] >= 1


# ═══════════════════════════════════════════════════════════════
# Tests: Full Auto PM→Dev→Test Pipeline (4.2)
# ═══════════════════════════════════════════════════════════════

class TestFullAutoPipeline:
    """4.2: PM→Dev→Test 全自动三步接力"""

    def test_three_step_chain_completes(self, tmp_db, tmp_path):
        """Full PM→Dev→Test chain completes with all steps successful"""
        wf = {
            "workflow": {
                "id": "full-auto-test",
                "steps": [
                    {
                        "id": "pm_analyze", "agent": "pm",
                        "description": "Analyze", "sleep": 0.05, "timeout": 10,
                        "output": {"required": ["root_cause", "task_breakdown"]},
                        "on_success": {"to_state": "assigned"},
                    },
                    {
                        "id": "dev_fix", "agent": "dev",
                        "description": "Implement", "depends_on": "pm_analyze",
                        "sleep": 0.05, "timeout": 10,
                        "output": {"required": ["files_changed"]},
                        "input": {"from": "pm_analyze.output", "fields": ["root_cause"]},
                    },
                    {
                        "id": "test_verify", "agent": "test",
                        "description": "Verify", "depends_on": "dev_fix",
                        "sleep": 0.05, "timeout": 10,
                        "output": {"required": ["verdict"]},
                        "input": {"from": "dev_fix.output", "fields": ["files_changed"]},
                        "on_verdict_approved": {"action": "mark_complete"},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "full-auto.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        adapter = _make_sleep_adapter()
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=adapter)
        orch = WorkflowOrchestrator(tmp_db, spawner, wf_path)

        task = _make_task("task-full-auto", "full-auto-test",
                          current_step=None,
                          type="feature",
                          context={"requirements_text": "Test requirement"})
        tmp_db.insert_task(task)

        result = orch.run(task)
        assert result["steps"]["pm_analyze"] == "completed"
        assert result["steps"]["dev_fix"] == "completed"
        assert result["steps"]["test_verify"] == "completed"

    def test_pm_output_flows_to_dev(self, tmp_db, tmp_path):
        """PM output fields are passed as Dev input context"""
        wf = {
            "workflow": {
                "id": "dataflow-test",
                "steps": [
                    {
                        "id": "pm_analyze", "agent": "pm",
                        "description": "Analyze", "sleep": 0.05, "timeout": 10,
                        "output": {"required": ["root_cause", "task_breakdown"]},
                    },
                    {
                        "id": "dev_fix", "agent": "dev",
                        "description": "Implement", "depends_on": "pm_analyze",
                        "sleep": 0.05, "timeout": 10,
                        "output": {"required": ["files_changed"]},
                        "input": {"from": "pm_analyze.output",
                                  "fields": ["root_cause", "task_breakdown"]},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "dataflow.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        adapter = _make_sleep_adapter(outputs={
            "root_cause": "Memory leak in parser",
            "task_breakdown": ["Fix parser", "Add tests"],
            "files_changed": ["src/parser.py"],
        })
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=adapter)
        orch = WorkflowOrchestrator(tmp_db, spawner, wf_path)

        task = _make_task("task-dataflow", "dataflow-test",
                          current_step=None, type="feature")
        tmp_db.insert_task(task)

        result = orch.run(task)
        assert result["steps"]["pm_analyze"] == "completed"
        assert result["steps"]["dev_fix"] == "completed"

        # Verify PM output was captured and flowed to Dev via input resolution
        assert "pm_analyze" in result["results"]
        pm_output = result["results"]["pm_analyze"]
        assert pm_output.get("root_cause") == "Memory leak in parser"
        assert pm_output.get("task_breakdown") == ["Fix parser", "Add tests"]


# ═══════════════════════════════════════════════════════════════
# Tests: Rejection Loop (4.3)
# ═══════════════════════════════════════════════════════════════

class TestRejectionLoop:
    """4.3: Rejection loop — Test rejects → Dev retries → Test re-verifies"""

    def test_single_rejection_then_approval(self, tmp_db, tmp_path):
        """Test rejects once, Dev fixes, Test approves on second pass"""
        wf = {
            "workflow": {
                "id": "reject-loop-test",
                "steps": [
                    {
                        "id": "pm_analyze", "agent": "pm",
                        "description": "Analyze", "sleep": 0.02, "timeout": 10,
                        "output": {"required": ["root_cause"]},
                    },
                    {
                        "id": "dev_fix", "agent": "dev",
                        "description": "Fix", "depends_on": "pm_analyze",
                        "sleep": 0.02, "timeout": 10,
                        "output": {"required": ["files_changed"]},
                        "input": {"from": "pm_analyze.output", "fields": ["root_cause"]},
                    },
                    {
                        "id": "test_verify", "agent": "test",
                        "description": "Verify", "depends_on": "dev_fix",
                        "sleep": 0.02, "timeout": 10,
                        "output": {"required": ["verdict"]},
                        "input": {"from": "dev_fix.output", "fields": ["files_changed"]},
                        "on_verdict_rejected": {"next": "dev_fix"},
                        "on_verdict_approved": {"action": "mark_complete"},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "reject-loop.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        # Adapter: first 2 calls (pm + dev) return approved, 3rd (test) rejects,
        # 4th (dev retry) returns, 5th (test) approves
        call_count = [0]

        def parse_fn():
            call_count[0] += 1
            verdict = "rejected" if call_count[0] == 3 else "approved"
            return StepResult(
                step_id="", agent="", status=StepStatus.COMPLETED,
                output={
                    "root_cause": "Bug found",
                    "files_changed": ["src/fix.py"],
                    "verdict": verdict,
                },
            )

        adapter = _make_sleep_adapter(parse_fn=parse_fn)
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=adapter)
        orch = WorkflowOrchestrator(tmp_db, spawner, wf_path)

        task = _make_task("task-reject-loop", "reject-loop-test", current_step=None)
        tmp_db.insert_task(task)

        result = orch.run(task)
        # pm_analyze should always complete
        assert result["steps"]["pm_analyze"] == "completed"
        # After rejection loop, test_verify should eventually complete
        assert result["steps"]["test_verify"] == "completed"

    def test_max_rejections_triggers_escalation(self, tmp_db, tmp_path):
        """After max_rejections, task is escalated"""
        wf = {
            "workflow": {
                "id": "escalate-test",
                "steps": [
                    {
                        "id": "pm_analyze", "agent": "pm",
                        "description": "Analyze", "sleep": 0.02, "timeout": 10,
                        "output": {"required": ["root_cause"]},
                    },
                    {
                        "id": "dev_fix", "agent": "dev",
                        "description": "Fix", "depends_on": "pm_analyze",
                        "sleep": 0.02, "timeout": 10,
                        "output": {"required": ["files_changed"]},
                        "input": {"from": "pm_analyze.output", "fields": ["root_cause"]},
                    },
                    {
                        "id": "test_verify", "agent": "test",
                        "description": "Verify (always rejects)", "depends_on": "dev_fix",
                        "sleep": 0.02, "timeout": 10,
                        "output": {"required": ["verdict"]},
                        "input": {"from": "dev_fix.output", "fields": ["files_changed"]},
                        "on_verdict_rejected": {"next": "dev_fix"},
                    },
                ],
                "error_policy": {"max_rejections": 2},
            }
        }
        wf_path = tmp_path / "escalate-test.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        # Always reject adapter
        def always_reject():
            return StepResult(
                step_id="", agent="", status=StepStatus.COMPLETED,
                output={
                    "root_cause": "Bug",
                    "files_changed": ["src/fix.py"],
                    "verdict": "rejected",
                },
            )

        adapter = _make_sleep_adapter(parse_fn=always_reject)
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=adapter)
        orch = WorkflowOrchestrator(tmp_db, spawner, wf_path)

        task = _make_task("task-escalate", "escalate-test", current_step=None)
        tmp_db.insert_task(task)

        result = orch.run(task)
        test_state = result["steps"].get("test_verify")
        assert test_state in ("failed", "rejected")

        task_after = tmp_db.get_task("task-escalate")
        assert task_after is not None
        assert task_after.get("rejection_count", 0) >= 2


# ═══════════════════════════════════════════════════════════════
# Tests: Escalation Recording (4.4)
# ═══════════════════════════════════════════════════════════════

class TestEscalationRecording:
    """4.4: Escalation — events recorded, human can query and respond"""

    def test_escalation_recorded_in_db(self, tmp_db):
        """Escalation event is written to escalations table"""
        task = _make_task("task-esc-1", "conductor-test",
                          status="escalated", current_step="test_verify",
                          rejection_count=3)
        tmp_db.insert_task(task)
        tmp_db.update_task_status("task-esc-1", "escalated", "test_verify")

        esc_id = tmp_db.record_escalation(
            task_id="task-esc-1",
            step_id="test_verify",
            reason="Max rejections (3) reached at test_verify",
            context={"rejection_count": 3},
        )
        assert esc_id > 0

        pending = tmp_db.get_pending_escalations()
        assert len(pending) >= 1
        assert any(e["task_id"] == "task-esc-1" for e in pending)

    def test_resolve_escalation(self, tmp_db):
        """Escalation can be resolved (retry/reject)"""
        task = _make_task("task-resolve", "conductor-test",
                          status="escalated", current_step=None)
        tmp_db.insert_task(task)
        tmp_db.update_task_status("task-resolve", "escalated")

        esc_id = tmp_db.record_escalation(
            task_id="task-resolve",
            step_id="dev_fix",
            reason="Test failure",
        )

        result = tmp_db.resolve_escalation(esc_id, "retry")
        assert result is True

        pending = tmp_db.get_pending_escalations()
        assert not any(e["id"] == esc_id for e in pending)

    def test_get_pending_tasks(self, tmp_db):
        """get_pending_tasks returns only pending tasks"""
        for i, status in enumerate(["pending", "pending", "running", "completed"]):
            task = _make_task(f"task-mix-{i}", "conductor-test",
                              status="pending", current_step=None)
            tmp_db.insert_task(task)
            if status != "pending":
                tmp_db.update_task_status(f"task-mix-{i}", status)

        pending = tmp_db.get_pending_tasks()
        assert len(pending) == 2
        assert all(t["status"] == "pending" for t in pending)

    def test_get_escalated_tasks(self, tmp_db):
        """get_escalated_tasks returns only escalated tasks"""
        for i, status in enumerate(["pending", "escalated", "running", "escalated"]):
            task = _make_task(f"task-esc-q-{i}", "conductor-test",
                              status="pending", current_step=None)
            tmp_db.insert_task(task)
            if status != "pending":
                tmp_db.update_task_status(f"task-esc-q-{i}", status)

        escalated = tmp_db.get_escalated_tasks()
        assert len(escalated) == 2
        assert all(t["status"] == "escalated" for t in escalated)


# ═══════════════════════════════════════════════════════════════
# Tests: Conductor Retry & Reject (4.4 continued)
# ═══════════════════════════════════════════════════════════════

class TestConductorRetryReject:
    """Human responds to escalations via conductor retry/reject"""

    @patch("multiagent.engine_cli.cmd_run")
    def test_retry_escalated_task(self, mock_cmd_run, tmp_db, workflow_yaml):
        """Conductor.retry_escalated re-runs an escalated task"""
        mock_cmd_run.return_value = "task-retry-1"

        task = _make_task("task-retry-1", "conductor-test",
                          status="escalated", current_step="test_verify",
                          rejection_count=2,
                          context={"requirements_text": "Fix the bug"})
        tmp_db.insert_task(task)
        tmp_db.update_task_status("task-retry-1", "escalated", "test_verify")
        tmp_db.record_escalation(
            task_id="task-retry-1",
            step_id="test_verify",
            reason="Needs human review",
        )

        c = Conductor(
            db_path=tmp_db.db_path,
            workflow_path=workflow_yaml,
        )

        result = c.retry_escalated("task-retry-1")
        assert result == "task-retry-1"

        task_after = tmp_db.get_task("task-retry-1")
        assert task_after["status"] != "escalated"

        pending = tmp_db.get_pending_escalations()
        assert not any(e["task_id"] == "task-retry-1" for e in pending)

    def test_retry_nonexistent_task(self, tmp_db, workflow_yaml):
        """Retrying non-existent task returns None"""
        c = Conductor(
            db_path=tmp_db.db_path,
            workflow_path=workflow_yaml,
        )
        result = c.retry_escalated("nonexistent-task")
        assert result is None


# ═══════════════════════════════════════════════════════════════
# Tests: End-to-End (4.5)
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:
    """4.5: Full end-to-end: submit → conductor process → complete"""

    @patch("multiagent.engine_cli.cmd_run")
    def test_submit_to_completion_flow(self, mock_cmd_run, tmp_db, tmp_path):
        """Full flow: pending task → conductor process_one → cmd_run called"""
        import uuid

        wf = {
            "workflow": {
                "id": "e2e-test",
                "steps": [
                    {
                        "id": "pm_analyze", "agent": "pm",
                        "description": "Analyze", "sleep": 0.03, "timeout": 10,
                        "output": {"required": ["root_cause", "task_breakdown"]},
                    },
                    {
                        "id": "dev_fix", "agent": "dev",
                        "description": "Implement", "depends_on": "pm_analyze",
                        "sleep": 0.03, "timeout": 10,
                        "output": {"required": ["files_changed"]},
                    },
                    {
                        "id": "test_verify", "agent": "test",
                        "description": "Verify", "depends_on": "dev_fix",
                        "sleep": 0.03, "timeout": 10,
                        "output": {"required": ["verdict"]},
                        "on_verdict_approved": {"action": "mark_complete"},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "e2e.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        task_id = f"task-e2e-{uuid.uuid4().hex[:6]}"
        mock_cmd_run.return_value = task_id

        task = _make_task(task_id, "e2e-test",
                          current_step="pm_analyze", type="feature",
                          source="pm",
                          context={"requirements_text": "Build a new feature"})
        tmp_db.insert_task(task)

        c = Conductor(db_path=tmp_db.db_path, workflow_path=wf_path)
        result = c.process_one()

        assert result == task_id
        assert mock_cmd_run.called

        # Verify cmd_run was called with correct args
        call_kwargs = mock_cmd_run.call_args.kwargs
        assert call_kwargs["task_id"] == task_id

    @patch("multiagent.engine_cli.cmd_run")
    def test_conductor_tracks_processed_count(self, mock_cmd_run, tmp_db, tmp_path):
        """Conductor increments tasks_processed counter"""
        wf = {
            "workflow": {
                "id": "count-test",
                "steps": [
                    {
                        "id": "only_step", "agent": "dev",
                        "description": "Single step", "sleep": 0.03, "timeout": 10,
                        "output": {"required": ["result"]},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "count-test.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        mock_cmd_run.return_value = "task-count-0"

        for i in range(2):
            task = _make_task(f"task-count-{i}", "count-test",
                              current_step=None,
                              context={"requirements_text": f"Task {i}"})
            tmp_db.insert_task(task)

        c = Conductor(db_path=tmp_db.db_path, workflow_path=wf_path)

        c.process_one()
        assert c.state.tasks_processed == 1

        c.process_one()
        assert c.state.tasks_processed == 2

    def test_escalation_detection_via_check_escalations(self, tmp_db, tmp_path):
        """Conductor._check_escalations records events for escalated tasks"""
        wf = {
            "workflow": {
                "id": "esc-check-test",
                "steps": [
                    {
                        "id": "pm_analyze", "agent": "pm",
                        "description": "Analyze", "sleep": 0.02, "timeout": 10,
                        "output": {"required": ["root_cause"]},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "esc-check.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        # Insert an escalated task
        task = _make_task("task-esc-check", "esc-check-test",
                          status="escalated", current_step="test_verify",
                          rejection_count=3)
        tmp_db.insert_task(task)
        tmp_db.update_task_status("task-esc-check", "escalated", "test_verify")

        c = Conductor(db_path=tmp_db.db_path, workflow_path=wf_path)
        c._check_escalations()

        pending = tmp_db.get_pending_escalations()
        assert any(e["task_id"] == "task-esc-check" for e in pending), \
            "Escalation should be recorded for escalated task"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
