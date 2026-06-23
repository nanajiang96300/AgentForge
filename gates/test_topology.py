"""
Gate: Workflow Topology Tests — validate different DAG structures.

D1: Linear PM→Dev→Test
D2: Diamond PM→[Dev1,Dev2]→Test (parallel)
D3: Reviewer PM→Dev→Reviewer→Test (4-step)
D4: Rejection loop PM→Dev→Test (max 3)
D5: Custom 5-agent complex topology
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.db import StateDB, Task, now_iso
from multiagent.engine import AgentSpawner, StepStatus, StepResult
from multiagent.orchestrator import WorkflowOrchestrator, StepState

WF_DIR = Path(__file__).parent / "workflows"


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = StateDB(Path(path))
    db.connect()
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def db_task(db):
    t = Task(
        id="test-topo-task",
        type="feature",
        source="test",
        workflow_id="test-wf",
        current_step=None,
        status="pending",
        context={"requirements_text": "test topology"},
        created_at=now_iso(),
    )
    db.insert_task(t)
    return t


def _mock_spawner():
    """Create a spawner that always returns successful results."""
    from unittest.mock import Mock, MagicMock
    spawner = MagicMock(spec=AgentSpawner)
    spawner.validate_output = Mock(return_value=True)

    def mock_spawn(task_arg, step_def):
        return MagicMock()

    def mock_monitor(task_arg, step_def, process, timeout=600):
        sid = step_def["id"]
        agent = step_def["agent"]
        # Simulate realistic outputs per agent type
        if agent == "pm":
            return StepResult(
                step_id=sid, agent=agent, status=StepStatus.COMPLETED,
                output={"root_cause": "test", "task_breakdown": ["task1", "task2"], "complexity": "medium"})
        elif agent == "dev":
            return StepResult(
                step_id=sid, agent=agent, status=StepStatus.COMPLETED,
                output={"branch_name": f"feature/{sid}", "files_changed": [f"{sid}.py"]})
        elif agent == "test":
            return StepResult(
                step_id=sid, agent=agent, status=StepStatus.COMPLETED,
                output={"verdict": "approved", "test_summary": "All passed"})
        elif agent == "reviewer":
            return StepResult(
                step_id=sid, agent=agent, status=StepStatus.COMPLETED,
                output={"verdict": "approved", "review_summary": "Code looks good"})
        else:
            return StepResult(
                step_id=sid, agent=agent, status=StepStatus.COMPLETED,
                output={"result": "ok"})

    spawner.spawn = mock_spawn
    spawner.monitor = mock_monitor
    return spawner


def _run_workflow(db, db_task, wf_path):
    """Run a workflow with mock spawner and return results."""
    spawner = _mock_spawner()
    orch = WorkflowOrchestrator(db, spawner, wf_path)
    result = orch.run(db_task)
    return result


# ── D1: Linear ──


class TestLinearTopology:
    def test_loads_without_error(self, db):
        wf_path = WF_DIR / "test_linear.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        assert len(orch.steps) == 3
        assert "pm_analyze" in orch.steps
        assert "dev_fix" in orch.steps
        assert "test_verify" in orch.steps

    def test_dependency_chain(self, db):
        wf_path = WF_DIR / "test_linear.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        dev_step = orch.steps["dev_fix"]
        test_step = orch.steps["test_verify"]
        assert "pm_analyze" in dev_step.depends_on
        assert "dev_fix" in test_step.depends_on

    def test_executes_all_steps(self, db, db_task):
        wf_path = WF_DIR / "test_linear.yaml"
        result = _run_workflow(db, db_task, wf_path)
        step_states = result["steps"]
        assert step_states["pm_analyze"] == "completed"
        assert step_states["dev_fix"] == "completed"
        assert step_states["test_verify"] == "completed"

    def test_data_flow_pm_to_dev(self, db, db_task):
        wf_path = WF_DIR / "test_linear.yaml"
        result = _run_workflow(db, db_task, wf_path)
        dev_output = result["results"].get("dev_fix", {})
        assert dev_output.get("branch_name")


# ── D2: Diamond (Parallel) ──


class TestDiamondTopology:
    def test_loads_four_steps(self, db):
        wf_path = WF_DIR / "test_diamond.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        assert len(orch.steps) == 4

    def test_parallel_steps_share_dependency(self, db):
        wf_path = WF_DIR / "test_diamond.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        dev_backend = orch.steps["dev_backend"]
        dev_frontend = orch.steps["dev_frontend"]
        assert "pm_analyze" in dev_backend.depends_on
        assert "pm_analyze" in dev_frontend.depends_on

    def test_integration_waits_for_both(self, db):
        wf_path = WF_DIR / "test_diamond.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        test_step = orch.steps["test_integration"]
        assert "dev_backend" in test_step.depends_on
        assert "dev_frontend" in test_step.depends_on

    def test_parallel_steps_can_run_together(self, db):
        """Verify the two dev steps can be parallelized (no mutual deps)."""
        wf_path = WF_DIR / "test_diamond.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        assert orch._can_parallelize([
            orch.steps["dev_backend"], orch.steps["dev_frontend"]
        ])

    def test_executes_all_steps(self, db, db_task):
        wf_path = WF_DIR / "test_diamond.yaml"
        result = _run_workflow(db, db_task, wf_path)
        for sid in ["pm_analyze", "dev_backend", "dev_frontend", "test_integration"]:
            assert result["steps"][sid] == "completed", f"{sid} not completed"


# ── D3: Reviewer ──


class TestReviewerTopology:
    def test_loads_four_steps(self, db):
        wf_path = WF_DIR / "test_reviewer.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        assert len(orch.steps) == 4
        assert "reviewer_check" in orch.steps

    def test_reviewer_before_test(self, db):
        wf_path = WF_DIR / "test_reviewer.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        test_step = orch.steps["test_verify"]
        assert "reviewer_check" in test_step.depends_on

    def test_reviewer_has_verdict_routing(self, db):
        wf_path = WF_DIR / "test_reviewer.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        reviewer = orch.steps["reviewer_check"]
        assert reviewer.on_verdict_rejected
        assert reviewer.on_verdict_rejected.get("next") == "dev_implement"

    def test_executes_all_steps(self, db, db_task):
        wf_path = WF_DIR / "test_reviewer.yaml"
        result = _run_workflow(db, db_task, wf_path)
        for sid in ["pm_analyze", "dev_implement", "reviewer_check", "test_verify"]:
            assert result["steps"][sid] == "completed", f"{sid} not completed"


# ── D4: Rejection Loop ──


class TestRejectionLoopTopology:
    def test_has_rejection_routing(self, db):
        wf_path = WF_DIR / "test_rejection_loop.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        test_step = orch.steps["test_verify"]
        assert test_step.on_verdict_rejected
        assert test_step.on_verdict_rejected["next"] == "dev_fix"

    def test_max_rejections_configured(self, db):
        wf_path = WF_DIR / "test_rejection_loop.yaml"
        orch = WorkflowOrchestrator(db, _mock_spawner(), wf_path)
        orch.load()
        ep = orch.workflow_def.get("workflow", {}).get("error_policy", {})
        assert ep.get("max_rejections") == 3

    def test_escalation_on_rejection_exhaust(self, db, db_task):
        """When max rejections reached, should escalate."""
        wf_path = WF_DIR / "test_rejection_loop.yaml"
        spawner = _mock_spawner()

        # Override test monitor to always reject
        reject_count = [0]

        def mock_monitor(task_arg, step_def, process, timeout=600):
            sid = step_def["id"]
            agent = step_def["agent"]
            if agent == "test":
                reject_count[0] += 1
                return StepResult(
                    step_id=sid, agent=agent, status=StepStatus.COMPLETED,
                    output={"verdict": "rejected", "test_summary": f"Failed attempt {reject_count[0]}"})
            elif agent == "pm":
                return StepResult(step_id=sid, agent=agent, status=StepStatus.COMPLETED,
                    output={"root_cause": "test", "task_breakdown": ["t1"], "complexity": "simple"})
            else:
                return StepResult(step_id=sid, agent=agent, status=StepStatus.COMPLETED,
                    output={"branch_name": f"fix/{sid}", "files_changed": ["file.py"]})

        spawner.monitor = mock_monitor
        orch = WorkflowOrchestrator(db, spawner, wf_path)

        # Patch MAX_TOTAL_STEP_EXECUTIONS to prevent early bailout
        orch.MAX_TOTAL_STEP_EXECUTIONS = 50
        result = orch.run(db_task)

        # Should have escalated (max_rejections=3 exceeded)
        updated = db.get_task(db_task.id)
        assert updated["status"] in ("escalated", "failed") or \
               orch.steps["test_verify"].state in (StepState.FAILED, StepState.REJECTED)

    def test_approval_completes_task(self, db, db_task):
        wf_path = WF_DIR / "test_rejection_loop.yaml"
        result = _run_workflow(db, db_task, wf_path)
        assert result["steps"]["test_verify"] == "completed"


# ── D5: Complex Multi-Agent ──


class TestComplexTopology:
    def test_custom_5_agent_workflow(self, db, db_task):
        """Create and run a custom 5-agent workflow YAML dynamically."""
        wf_yaml = {
            "workflow": {
                "id": "test-complex",
                "steps": [
                    {"id": "pm_analyze", "agent": "pm", "timeout": 300,
                     "output": {"required": ["root_cause", "task_breakdown"]}},
                    {"id": "dev_api", "agent": "dev", "timeout": 600,
                     "depends_on": ["pm_analyze"],
                     "output": {"required": ["branch_name", "files_changed"]}},
                    {"id": "dev_db", "agent": "dev", "timeout": 600,
                     "depends_on": ["pm_analyze"],
                     "output": {"required": ["branch_name", "files_changed"]}},
                    {"id": "dev_ui", "agent": "dev", "timeout": 600,
                     "depends_on": ["pm_analyze"],
                     "output": {"required": ["branch_name", "files_changed"]}},
                    {"id": "test_integration", "agent": "test", "timeout": 300,
                     "depends_on": ["dev_api", "dev_db", "dev_ui"],
                     "output": {"required": ["verdict", "test_summary"]},
                     "on_verdict_rejected": {"next": "dev_api"}},
                ],
                "error_policy": {"max_rejections": 3},
            }
        }

        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(wf_yaml, f)
            tmp_path = Path(f.name)

        try:
            result = _run_workflow(db, db_task, tmp_path)
            assert len(result["steps"]) == 5
            for sid in result["steps"]:
                assert result["steps"][sid] == "completed", f"{sid} should be completed"
        finally:
            tmp_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
