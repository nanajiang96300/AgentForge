"""
验证 P0 修复：无限重试循环已被硬约束阻止。

测试场景：
  1. Schema validation retry: Dev 始终缺少 commit_hash → 最多重试 3 次
  2. Crash/Timeout retry: Agent 始终崩溃 → 最多重试 3 次
  3. 全局硬顶: 超过 MAX_TOTAL_STEP_EXECUTIONS → force escalate
  4. 正常成功不重试
  5. Rejection loop 尊重 max_rejections
"""
import pytest, json, tempfile, os, sys, yaml
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from multiagent.db import StateDB, Task, now_iso
from multiagent.engine import AgentSpawner, StepResult, StepStatus
from multiagent.orchestrator import WorkflowOrchestrator, WorkflowStep, StepState


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
def task(db):
    t = Task(
        id="test-retry-cap", type="feature", source="test",
        workflow_id="test-wf", current_step="dev_fix",
        context={"requirements_text": "test"},
        created_at=now_iso(),
    )
    db.insert_task(t)
    return t


def _setup_orchestrator(db):
    """Create orchestrator with mock spawner but REAL YAML (loaded via tempfile)"""
    spawner = MagicMock(spec=AgentSpawner)

    orch = WorkflowOrchestrator(db, spawner, Path("/fake/workflow.yaml"))
    # Manually set workflow_def and steps to avoid FileNotFoundError
    orch.workflow_def = {
        "workflow": {
            "id": "test-wf",
            "error_policy": {"max_rejections": 3},
        }
    }
    orch.steps = {
        "dev_fix": WorkflowStep(
            id="dev_fix", agent="dev",
            retry={"max": 3},
            output={"required": ["branch_name", "commit_hash"]},
        ),
        "test_verify": WorkflowStep(
            id="test_verify", agent="test",
            depends_on=["dev_fix"],
            on_verdict_rejected={"next": "dev_fix"},
            on_verdict_approved={"action": "mark_complete"},
        ),
    }
    return orch, spawner


class TestRetryHardCap:

    def test_schema_validation_retry_capped(self, db, task):
        """Dev 缺少 required 字段 → 重试 3 次后 escalate"""
        orch, spawner = _setup_orchestrator(db)

        call_count = [0]
        def mock_monitor(task_arg, step_def, process, timeout=600):
            call_count[0] += 1
            return StepResult(
                step_id=step_def["id"], agent=step_def["agent"],
                status=StepStatus.COMPLETED,
                output={"branch_name": "fix/test"},  # 缺 commit_hash
            )

        spawner.monitor = mock_monitor
        spawner.spawn = Mock(return_value=MagicMock())
        spawner.validate_output = Mock(return_value=False)  # 始终校验失败

        dev_step = orch.steps["dev_fix"]
        result = orch.execute_step(task, dev_step)

        assert call_count[0] == 4, f"Expected 4 (1 init + 3 retries), got {call_count[0]}"
        assert orch._step_attempts["dev_fix"] >= 3
        updated = db.get_task(task.id)
        assert updated["status"] == "escalated", f"Should be escalated, got {updated['status']}"

    def test_crash_retry_capped(self, db, task):
        """Agent 持续崩溃 → 重试 3 次后 escalate"""
        orch, spawner = _setup_orchestrator(db)

        call_count = [0]
        def mock_monitor(task_arg, step_def, process, timeout=600):
            call_count[0] += 1
            return StepResult(
                step_id=step_def["id"], agent=step_def["agent"],
                status=StepStatus.CRASHED, error="Process died", output={},
            )

        spawner.monitor = mock_monitor
        spawner.spawn = Mock(return_value=MagicMock())

        result = orch.execute_step(task, orch.steps["dev_fix"])

        assert call_count[0] == 4, f"Expected 4 (1 init + 3 retries), got {call_count[0]}"
        updated = db.get_task(task.id)
        assert updated["status"] == "escalated"

    def test_global_max_executions_hard_cap(self, db, task):
        """超过 MAX_TOTAL_STEP_EXECUTIONS → force escalate"""
        orch, spawner = _setup_orchestrator(db)
        orch.MAX_TOTAL_STEP_EXECUTIONS = 3  # 降低以加速测试

        call_count = [0]
        def mock_monitor(task_arg, step_def, process, timeout=600):
            call_count[0] += 1
            return StepResult(
                step_id=step_def["id"], agent=step_def["agent"],
                status=StepStatus.VALIDATION_FAILED,
                error="Missing", output={},
            )

        spawner.monitor = mock_monitor
        spawner.spawn = Mock(return_value=MagicMock())

        result = orch.execute_step(task, orch.steps["dev_fix"])

        assert call_count[0] <= 4, f"Global cap exceeded: {call_count[0]}"
        assert "Global step execution cap" in (result.error or "")
        updated = db.get_task(task.id)
        assert updated["status"] == "escalated"

    def test_successful_step_no_retries(self, db, task):
        """正常成功的步骤不触发重试"""
        orch, spawner = _setup_orchestrator(db)

        call_count = [0]
        def mock_monitor(task_arg, step_def, process, timeout=600):
            call_count[0] += 1
            return StepResult(
                step_id=step_def["id"], agent=step_def["agent"],
                status=StepStatus.COMPLETED,
                output={"branch_name": "fix/test", "commit_hash": "abc123"},
            )

        spawner.monitor = mock_monitor
        spawner.spawn = Mock(return_value=MagicMock())
        spawner.validate_output = Mock(return_value=True)

        result = orch.execute_step(task, orch.steps["dev_fix"])

        assert call_count[0] == 1, f"No retries needed, got {call_count[0]}"
        assert result.status == StepStatus.COMPLETED
        assert orch._total_executions == 1

    def test_rejection_loop_respects_max(self, db, task):
        """Rejection loop 尊重 max_rejections=3 → 最多 4 次 dev"""
        # Create a real temp workflow YAML so run().load() works
        wf_yaml = tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        )
        yaml.dump({
            "workflow": {
                "id": "test-wf",
                "error_policy": {"max_rejections": 3},
                "steps": [
                    {"id": "dev_fix", "agent": "dev", "retry": {"max": 3},
                     "output": {"required": ["branch_name", "commit_hash"]}},
                    {"id": "test_verify", "agent": "test", "depends_on": ["dev_fix"],
                     "on_verdict_rejected": {"next": "dev_fix"},
                     "on_verdict_approved": {"action": "mark_complete"}},
                ]
            }
        }, wf_yaml)
        wf_yaml.close()

        spawner = MagicMock(spec=AgentSpawner)
        orch = WorkflowOrchestrator(db, spawner, Path(wf_yaml.name))

        dev_calls = [0]
        test_calls = [0]

        def mock_monitor(task_arg, step_def, process, timeout=600):
            sid = step_def["id"]
            if sid == "dev_fix":
                dev_calls[0] += 1
                return StepResult(
                    step_id=sid, agent="dev", status=StepStatus.COMPLETED,
                    output={"branch_name": "fix/x", "commit_hash": "abc"},
                )
            else:
                test_calls[0] += 1
                return StepResult(
                    step_id=sid, agent="test", status=StepStatus.COMPLETED,
                    output={"verdict": "rejected", "reason": "still broken"},
                )

        spawner.monitor = mock_monitor
        spawner.spawn = Mock(return_value=MagicMock())
        spawner.validate_output = Mock(return_value=True)

        orch.run(task)

        os.unlink(wf_yaml.name)

        # Dev: 1 initial + 最多 3 rejection retries = 最多 4
        assert dev_calls[0] <= 4, f"Dev {dev_calls[0]} > 4 (max_rejections+1)"
        assert test_calls[0] <= 4, f"Test {test_calls[0]} > 4"
        updated = db.get_task(task.id)
        assert updated["status"] == "escalated" or orch.steps["test_verify"].state == StepState.FAILED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
