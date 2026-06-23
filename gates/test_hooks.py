"""
验证 A6: 步骤生命周期钩子系统。

测试:
  1. before_step 在 spawn 前触发
  2. after_step 在 monitor 后触发
  3. on_rejection 在 Test 打回时触发
  4. on_escalation 在超过上限时触发
  5. 钩子异常不影响工作流执行
"""
import pytest, tempfile, os, sys
from pathlib import Path
from unittest.mock import Mock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from multiagent.db import StateDB, Task, now_iso
from multiagent.engine import AgentSpawner, StepResult, StepStatus
from multiagent.orchestrator import WorkflowOrchestrator, WorkflowStep, StepState
from multiagent.interfaces import StepHook


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
        id="test-hooks", type="feature", source="test",
        workflow_id="test-wf", current_step="dev_fix",
        context={"requirements_text": "test"},
        created_at=now_iso(),
    )
    db.insert_task(t)
    return t


class TestStepHooks:
    """步骤生命周期钩子测试"""

    def test_before_and_after_hooks_fire(self, db, task):
        """before_step 和 after_step 在正确时机触发"""
        spawner = MagicMock(spec=AgentSpawner)
        spawner.spawn = Mock(return_value=MagicMock())
        spawner.validate_output = Mock(return_value=True)

        def mock_monitor(*args, **kwargs):
            return StepResult(
                step_id="dev_fix", agent="dev",
                status=StepStatus.COMPLETED,
                output={"branch_name": "fix/x", "commit_hash": "abc123"},
            )
        spawner.monitor = mock_monitor

        orch = WorkflowOrchestrator(db, spawner, Path("/fake/wf.yaml"))
        orch.workflow_def = {"workflow": {"id": "test", "error_policy": {"max_rejections": 3}}}
        orch.steps = {"dev_fix": WorkflowStep(id="dev_fix", agent="dev")}

        # Track hook calls
        events = []
        class TestHook(StepHook):
            def before_step(self, tid, sid):
                events.append(("before", tid, sid))
            def after_step(self, tid, sid, result):
                events.append(("after", tid, sid, result.status))
            def on_rejection(self, tid, sid, count):
                events.append(("rejection", tid, sid, count))
            def on_escalation(self, tid, sid, reason):
                events.append(("escalation", tid, sid, reason))

        orch.register_hook(TestHook())
        orch.execute_step(task, orch.steps["dev_fix"])

        assert ("before", "test-hooks", "dev_fix") in events
        assert len([e for e in events if e[0] == "after"]) == 1

    def test_on_rejection_fires(self, db, task):
        """Test打回时触发 on_rejection 钩子"""
        spawner = MagicMock(spec=AgentSpawner)
        spawner.spawn = Mock(return_value=MagicMock())
        spawner.validate_output = Mock(return_value=True)

        def mock_monitor(*args, **kwargs):
            return StepResult(
                step_id="test_verify", agent="test",
                status=StepStatus.COMPLETED,
                output={"verdict": "rejected", "reason": "bug"},
            )
        spawner.monitor = mock_monitor

        orch = WorkflowOrchestrator(db, spawner, Path("/fake/wf.yaml"))
        orch.workflow_def = {"workflow": {"id": "test", "error_policy": {"max_rejections": 3}}}
        orch.steps = {
            "dev_fix": WorkflowStep(id="dev_fix", agent="dev"),
            "test_verify": WorkflowStep(
                id="test_verify", agent="test", depends_on=["dev_fix"],
                on_verdict_rejected={"next": "dev_fix"},
            ),
        }
        # Pre-populate dev result so test_verify is ready
        orch._step_results["dev_fix"] = {"branch_name": "fix/x", "commit_hash": "abc"}

        events = []
        class TestHook(StepHook):
            def before_step(self, tid, sid): pass
            def after_step(self, tid, sid, result): pass
            def on_rejection(self, tid, sid, count):
                events.append(("rejection", count))
            def on_escalation(self, tid, sid, reason): pass

        orch.register_hook(TestHook())
        orch.execute_step(task, orch.steps["test_verify"])

        assert len(events) == 1
        assert events[0][1] == 1  # rejection_count = 1

    def test_on_escalation_fires_on_retry_exhaustion(self, db, task):
        """重试耗尽时触发 on_escalation 钩子"""
        spawner = MagicMock(spec=AgentSpawner)
        spawner.spawn = Mock(return_value=MagicMock())
        spawner.validate_output = Mock(return_value=False)  # always fails validation

        def mock_monitor(*args, **kwargs):
            return StepResult(
                step_id="dev_fix", agent="dev",
                status=StepStatus.COMPLETED,
                output={"branch_name": "fix/x"},  # missing commit_hash
            )
        spawner.monitor = mock_monitor

        orch = WorkflowOrchestrator(db, spawner, Path("/fake/wf.yaml"))
        orch.workflow_def = {"workflow": {"id": "test", "error_policy": {"max_rejections": 3}}}
        orch.steps = {"dev_fix": WorkflowStep(
            id="dev_fix", agent="dev", retry={"max": 3},
            output={"required": ["branch_name", "commit_hash"]},
        )}

        events = []
        class TestHook(StepHook):
            def before_step(self, tid, sid): pass
            def after_step(self, tid, sid, result): pass
            def on_rejection(self, tid, sid, count): pass
            def on_escalation(self, tid, sid, reason):
                events.append(("escalation", reason))

        orch.register_hook(TestHook())
        orch.execute_step(task, orch.steps["dev_fix"])

        assert len(events) == 1
        assert "Retry cap" in events[0][1]

    def test_hook_exception_does_not_block_workflow(self, db, task):
        """钩子抛出异常不影响工作流执行"""
        spawner = MagicMock(spec=AgentSpawner)
        spawner.spawn = Mock(return_value=MagicMock())
        spawner.validate_output = Mock(return_value=True)
        spawner.monitor = Mock(return_value=StepResult(
            step_id="dev_fix", agent="dev",
            status=StepStatus.COMPLETED,
            output={"branch_name": "fix/x", "commit_hash": "abc"},
        ))

        orch = WorkflowOrchestrator(db, spawner, Path("/fake/wf.yaml"))
        orch.workflow_def = {"workflow": {"id": "test", "error_policy": {"max_rejections": 3}}}
        orch.steps = {"dev_fix": WorkflowStep(id="dev_fix", agent="dev")}

        class BrokenHook(StepHook):
            def before_step(self, tid, sid):
                raise RuntimeError("hook bug!")
            def after_step(self, tid, sid, result):
                raise RuntimeError("hook bug!")
            def on_rejection(self, tid, sid, count): pass
            def on_escalation(self, tid, sid, reason): pass

        orch.register_hook(BrokenHook())
        result = orch.execute_step(task, orch.steps["dev_fix"])

        # Workflow should complete despite hook errors
        assert result.status == StepStatus.COMPLETED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
