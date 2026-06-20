"""Phase 3 Gate: 并行工作流支持 — fan-out 多步骤同时执行"""
import pytest
import sys
import os
import time
import tempfile
import yaml
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.db import StateDB, Task, now_iso
from multiagent.engine import AgentSpawner, load_yaml
from multiagent.orchestrator import WorkflowOrchestrator, StepState


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
def parallel_workflow(tmp_path):
    """Workflow with two independent steps (fan-out)"""
    wf = {
        "workflow": {
            "id": "parallel-test",
            "steps": [
                {
                    "id": "step_a",
                    "agent": "dev",
                    "description": "Independent step A",
                    "timeout": 60,
                    "output": {"required": ["result"]},
                },
                {
                    "id": "step_b",
                    "agent": "test",
                    "description": "Independent step B",
                    "timeout": 60,
                    "output": {"required": ["result"]},
                },
            ],
            "error_policy": {"max_rejections": 3},
        }
    }
    wf_path = tmp_path / "parallel-test.yaml"
    with open(wf_path, "w") as f:
        yaml.dump(wf, f)
    return wf_path


@pytest.fixture
def diamond_workflow(tmp_path):
    """Diamond workflow: A → (B, C) → D"""
    wf = {
        "workflow": {
            "id": "diamond-test",
            "steps": [
                {
                    "id": "analyze",
                    "agent": "pm",
                    "description": "Analysis (first)",
                    "output": {"required": ["finding"]},
                },
                {
                    "id": "fix_backend",
                    "agent": "dev",
                    "description": "Fix backend (parallel)",
                    "depends_on": "analyze",
                    "output": {"required": ["result"]},
                },
                {
                    "id": "fix_frontend",
                    "agent": "dev",
                    "description": "Fix frontend (parallel)",
                    "depends_on": "analyze",
                    "output": {"required": ["result"]},
                },
                {
                    "id": "integration_test",
                    "agent": "test",
                    "description": "Integration test (waits for both)",
                    "depends_on": ["fix_backend", "fix_frontend"],
                    "output": {"required": ["verdict"]},
                },
            ],
            "error_policy": {"max_rejections": 3},
        }
    }
    wf_path = tmp_path / "diamond-test.yaml"
    with open(wf_path, "w") as f:
        yaml.dump(wf, f)
    return wf_path


# ═══════════════════════════════════════════════════════════════
# Tests: Parallel Detection
# ═══════════════════════════════════════════════════════════════

class TestParallelDetection:
    def test_two_independent_steps_parallel_ready(self, tmp_db, parallel_workflow):
        """3.3.1: Two steps with no dependencies are both ready simultaneously"""
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles)
        orch = WorkflowOrchestrator(tmp_db, spawner, parallel_workflow)
        orch.load()

        task = Task(
            id="task-par-1", type="bug", source="test",
            workflow_id="parallel-test", current_step=None,
            created_at=now_iso(),
        )

        ready = orch.get_ready_steps(task)
        assert len(ready) == 2
        step_ids = {s.id for s in ready}
        assert "step_a" in step_ids
        assert "step_b" in step_ids

    def test_diamond_middle_steps_parallel(self, tmp_db, diamond_workflow):
        """3.3.2: Diamond workflow: middle steps (B, C) are parallel after A completes"""
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles)
        orch = WorkflowOrchestrator(tmp_db, spawner, diamond_workflow)
        orch.load()

        task = Task(
            id="task-dia-1", type="bug", source="test",
            workflow_id="diamond-test", current_step=None,
            created_at=now_iso(),
        )

        # First round: only analyze is ready
        ready = orch.get_ready_steps(task)
        assert len(ready) == 1
        assert ready[0].id == "analyze"

    def test_diamond_parallel_after_first_completes(self, tmp_db, diamond_workflow):
        """3.3.3: Diamond: after analyze completes, both fix_backend and fix_frontend ready"""
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles)
        orch = WorkflowOrchestrator(tmp_db, spawner, diamond_workflow)
        orch.load()

        task = Task(
            id="task-dia-2", type="bug", source="test",
            workflow_id="diamond-test", current_step=None,
            created_at=now_iso(),
        )

        # Simulate analyze completed
        orch._step_results["analyze"] = {"finding": "Bug in auth module"}
        orch.steps["analyze"].state = StepState.COMPLETED

        ready = orch.get_ready_steps(task)
        assert len(ready) == 2
        step_ids = {s.id for s in ready}
        assert "fix_backend" in step_ids
        assert "fix_frontend" in step_ids


# ═══════════════════════════════════════════════════════════════
# Tests: Parallel Execution Timing
# ═══════════════════════════════════════════════════════════════

class SleepAdapter:
    """Mock adapter that 'spawns' a sleep call instead of real Claude"""
    def __init__(self):
        self.project_root = Path.cwd()

    def name(self):
        return "sleep-mock"

    def build_command(self, agent_config, task_prompt, step):
        sleep_time = step.get("sleep", 0.2)
        return ["sleep", str(sleep_time)]

    def parse_output(self, stdout, stderr):
        from multiagent.engine import StepResult, StepStatus
        return StepResult(
            step_id="", agent="", status=StepStatus.COMPLETED,
            output={"result": "ok", "verdict": "approved"},
        )

    def get_tool_restriction_flags(self, permissions):
        return [], []

    def _paths_to_tool_patterns(self, deny_paths, write_paths):
        return [], []


class TestParallelExecution:
    def test_parallel_faster_than_sequential(self, tmp_db, tmp_path):
        """3.3.4: Two independent steps run in parallel finish faster than sum of times"""
        wf = {
            "workflow": {
                "id": "speed-test",
                "steps": [
                    {
                        "id": "slow_a",
                        "agent": "dev",
                        "description": "Slow step A",
                        "sleep": 0.3,
                        "timeout": 10,
                        "output": {"required": ["result"]},
                    },
                    {
                        "id": "slow_b",
                        "agent": "test",
                        "description": "Slow step B",
                        "sleep": 0.3,
                        "timeout": 10,
                        "output": {"required": ["result"]},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "speed-test.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=SleepAdapter())
        orch = WorkflowOrchestrator(tmp_db, spawner, wf_path)

        task = Task(
            id="task-speed", type="bug", source="test",
            workflow_id="speed-test", current_step=None,
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        start = time.time()
        result = orch.run(task)
        elapsed = time.time() - start

        # Both steps 0.3s each → parallel should be ~0.3s, not ~0.6s
        assert elapsed < 0.7, f"Parallel execution too slow: {elapsed:.2f}s (expected < 0.7s)"
        assert result["steps"]["slow_a"] == "completed"
        assert result["steps"]["slow_b"] == "completed"

    def test_sequential_steps_respect_dependencies(self, tmp_db, tmp_path):
        """3.3.5: Dependent steps still run sequentially"""
        wf = {
            "workflow": {
                "id": "seq-test",
                "steps": [
                    {
                        "id": "first",
                        "agent": "dev",
                        "description": "First step",
                        "sleep": 0.1,
                        "timeout": 10,
                        "output": {"required": ["result"]},
                    },
                    {
                        "id": "second",
                        "agent": "test",
                        "description": "Second step",
                        "depends_on": "first",
                        "sleep": 0.1,
                        "timeout": 10,
                        "output": {"required": ["result"]},
                    },
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "seq-test.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=SleepAdapter())
        orch = WorkflowOrchestrator(tmp_db, spawner, wf_path)

        task = Task(
            id="task-seq", type="bug", source="test",
            workflow_id="seq-test", current_step=None,
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        result = orch.run(task)
        assert result["steps"]["first"] == "completed"
        assert result["steps"]["second"] == "completed"

    def test_thread_safety_step_results(self, tmp_db, tmp_path):
        """3.3.6: Parallel steps don't corrupt shared _step_results"""
        wf = {
            "workflow": {
                "id": "thread-test",
                "steps": [
                    {
                        "id": f"worker_{i}",
                        "agent": "dev",
                        "description": f"Worker {i}",
                        "sleep": 0.05,
                        "timeout": 10,
                        "output": {"required": ["result"]},
                    }
                    for i in range(4)
                ],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = tmp_path / "thread-test.yaml"
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=SleepAdapter())
        orch = WorkflowOrchestrator(tmp_db, spawner, wf_path)

        task = Task(
            id="task-thread", type="bug", source="test",
            workflow_id="thread-test", current_step=None,
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        result = orch.run(task)

        # All 4 workers should have completed
        for i in range(4):
            assert result["steps"][f"worker_{i}"] == "completed"
        # Results should be unique per worker
        assert len(result["results"]) == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
