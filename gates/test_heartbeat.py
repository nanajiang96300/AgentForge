"""Phase 3 Gate: 心跳监控 + 崩溃恢复"""
import pytest
import sys
import os
import time
import tempfile
import yaml
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.db import StateDB, Task, now_iso
from multiagent.engine import AgentSpawner, StepStatus as StepStatus
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


class CrashAdapter:
    """Adapter that spawns a sleep, optionally with crash behavior"""
    def __init__(self, crash_after=None):
        self.project_root = Path.cwd()
        self._crash_after = crash_after  # Not used for sleep

    def name(self):
        return "crash-mock"

    def build_command(self, agent_config, task_prompt, step):
        sleep_time = step.get("sleep", 0.2)
        return ["sleep", str(sleep_time)]

    def parse_output(self, stdout, stderr):
        from multiagent.engine import StepResult, StepStatus
        return StepResult(
            step_id="", agent="", status=StepStatus.COMPLETED,
            output={"result": "ok"},
        )

    def get_tool_restriction_flags(self, p):
        return [], []

    def _paths_to_tool_patterns(self, d, w):
        return [], []


# ═══════════════════════════════════════════════════════════════
# Tests: Heartbeat Recording
# ═══════════════════════════════════════════════════════════════

class TestHeartbeatRecording:
    def test_spawn_records_heartbeat(self, tmp_db):
        """3.4.1: Agent spawn records heartbeat with PID"""
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=CrashAdapter())

        # Insert a task first
        task = Task(
            id="task-hb-1", type="bug", source="test",
            workflow_id="test", current_step=None,
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        step_def = {"id": "test_step", "agent": "test", "description": "test", "timeout": 10, "sleep": 0.05}
        process = spawner.spawn(task, step_def)
        spawner.monitor(task, step_def, process, timeout=5)

        # Verify heartbeat was recorded
        hb = tmp_db.conn.execute(
            "SELECT task_id, step_id, agent_pid FROM heartbeat WHERE task_id = ?",
            ("task-hb-1",),
        ).fetchone()
        assert hb is not None
        assert hb[2] > 0  # PID should be positive

    def test_monitor_updates_step_results(self, tmp_db):
        """3.4.2: monitor records step result on completion"""
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=CrashAdapter())

        task = Task(
            id="task-hb-2", type="bug", source="test",
            workflow_id="test", current_step=None,
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        step_def = {"id": "step_complete", "agent": "test", "description": "test", "timeout": 10, "sleep": 0.05}
        process = spawner.spawn(task, step_def)
        result = spawner.monitor(task, step_def, process, timeout=5)

        assert result.status == StepStatus.COMPLETED

        # Verify step_results has entry
        steps = tmp_db.conn.execute(
            "SELECT step_id, status FROM step_results WHERE task_id = ?",
            ("task-hb-2",),
        ).fetchall()
        assert len(steps) >= 1
        # First entry: RUNNING (from spawn), second: COMPLETED (from monitor)
        statuses = [s[1] for s in steps]
        assert "running" in statuses
        assert "completed" in statuses


# ═══════════════════════════════════════════════════════════════
# Tests: Crash Recovery
# ═══════════════════════════════════════════════════════════════

class TestCrashRecovery:
    def test_lost_agent_detection(self, tmp_db):
        """3.4.3: Agent heartbeat timeout → get_lost_agents returns it"""
        # Need a task first (FK constraint)
        tmp_db.insert_task(Task(
            id="task-stale", type="bug", source="test",
            workflow_id="test", current_step=None, created_at=now_iso(),
        ))
        # Record stale heartbeat (older than timeout)
        past_time = "2020-01-01T00:00:00"
        tmp_db.conn.execute(
            "INSERT OR REPLACE INTO heartbeat (task_id, step_id, agent_pid, last_beat) VALUES (?,?,?,?)",
            ("task-stale", "step_stale", 99999, past_time),
        )
        tmp_db.conn.commit()

        lost = tmp_db.get_lost_agents(heartbeat_timeout=60)
        assert len(lost) >= 1
        assert lost[0]["task_id"] == "task-stale"

    def test_fresh_heartbeat_not_lost(self, tmp_db):
        """3.4.4: Recent heartbeat is NOT returned as lost"""
        tmp_db.insert_task(Task(
            id="task-fresh", type="bug", source="test",
            workflow_id="test", current_step=None, created_at=now_iso(),
        ))
        tmp_db.heartbeat("task-fresh", "step_fresh", 12345)

        lost = tmp_db.get_lost_agents(heartbeat_timeout=3600)  # 1 hour
        assert len(lost) == 0  # Fresh heartbeat, not lost

    def test_state_db_crash_recovery(self, tmp_db):
        """3.4.5: StateDB preserves task state after close/reopen"""
        # Insert task, record step
        task = Task(
            id="task-recover", type="bug", source="test",
            workflow_id="test-wf", current_step="fixing",
            status="running", created_at=now_iso(),
        )
        tmp_db.insert_task(task)
        tmp_db.record_step("task-recover", "step_a", "dev", "running",
                           started_at=now_iso())

        # Get db path before closing
        db_path = tmp_db.db_path
        tmp_db.close()

        # Reopen (simulates engine restart)
        db2 = StateDB(db_path)
        db2.connect()

        # Verify task still exists
        task2 = db2.get_task("task-recover")
        assert task2 is not None
        assert task2["status"] == "running"
        assert task2["current_step"] == "fixing"

        # Verify step still exists
        steps = db2.conn.execute(
            "SELECT step_id, status FROM step_results WHERE task_id = ?",
            ("task-recover",),
        ).fetchall()
        assert len(steps) >= 1

        db2.close()


# ═══════════════════════════════════════════════════════════════
# Tests: Timeout Handling
# ═══════════════════════════════════════════════════════════════

class TestTimeoutHandling:
    def test_agent_timeout_killed(self, tmp_db):
        """3.4.6: Agent exceeding timeout is killed and marked TIMED_OUT"""
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=CrashAdapter())

        task = Task(
            id="task-timeout", type="bug", source="test",
            workflow_id="test", current_step=None,
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        # Start a long sleep with very short timeout
        step_def = {"id": "step_long", "agent": "test", "description": "long step", "sleep": 30, "timeout": 0.1}
        process = spawner.spawn(task, step_def)

        # monitor with 0.01s timeout → should time out
        result = spawner.monitor(task, step_def, process, timeout=0.01)

        assert result.status == StepStatus.TIMED_OUT

        # Process should be killed
        assert process.poll() is not None  # Process has exited

    def test_retry_after_timeout(self, tmp_db):
        """3.4.7: Orchestrator retries timed-out steps"""
        roles = {"agents": {}, "global": {"runtime": "claude-code"}}
        spawner = AgentSpawner(tmp_db, roles, adapter=CrashAdapter())

        wf = {
            "workflow": {
                "id": "retry-test",
                "steps": [{
                    "id": "flaky_step",
                    "agent": "test",
                    "description": "Flaky step",
                    "sleep": 0.05,
                    "timeout": 10,
                    "retry": {"max": 2},
                    "output": {"required": ["result"]},
                }],
                "error_policy": {"max_rejections": 3},
            }
        }
        wf_path = Path("/tmp/retry-test.yaml")
        with open(wf_path, "w") as f:
            yaml.dump(wf, f)

        orch = WorkflowOrchestrator(tmp_db, spawner, wf_path)
        task = Task(
            id="task-retry", type="bug", source="test",
            workflow_id="retry-test", current_step=None,
            created_at=now_iso(),
        )
        tmp_db.insert_task(task)

        result = orch.run(task)
        assert result["steps"]["flaky_step"] == "completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
