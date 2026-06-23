"""
P0 Gate: engine.py — process lifecycle: _kill_process, reap_lost_agents

Verifies the process-group-aware kill logic and lost agent reaping.
"""

import os
import signal
import sys
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.engine import AgentSpawner, StepStatus, StepResult
from multiagent.db import StateDB, now_iso


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = StateDB(Path(path))
    db.connect()
    yield db
    db.close()
    os.unlink(path)


def _dummy_roles():
    return {"agents": {}, "global": {"runtime": "claude-code"}}


# ── _kill_process ──


class TestKillProcess:
    def test_kill_process_with_pgid(self):
        """_kill_process uses os.killpg when process group is available."""
        spawner = AgentSpawner(Mock(), _dummy_roles())
        # Create a real process in its own session to get a proper PGID
        p = subprocess.Popen(
            ["sleep", "10"],
            start_new_session=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        pgid = os.getpgid(p.pid)
        assert pgid != os.getpid()  # Different process group

        spawner._kill_process(p)

        # Process should be dead
        try:
            os.kill(p.pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert not alive

    def test_kill_already_dead_process(self):
        """_kill_process should handle already-dead processes gracefully."""
        spawner = AgentSpawner(Mock(), _dummy_roles())
        p = subprocess.Popen(
            ["true"],
            start_new_session=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        p.wait()  # Process already exited

        # Should not raise
        spawner._kill_process(p)

    def test_kill_without_process_group(self):
        """Fallback: direct terminate/kill when no separate process group."""
        spawner = AgentSpawner(Mock(), _dummy_roles())
        p = subprocess.Popen(
            ["sleep", "10"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        # Mock getpgid to simulate no separate group
        with patch("os.getpgid", side_effect=OSError("no pgid")):
            spawner._kill_process(p)

        # Process should be dead (via fallback)
        try:
            os.kill(p.pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert not alive


# ── reap_lost_agents ──


class TestReapLostAgents:
    def test_reap_finds_and_kills_lost_agents(self, db):
        """reap_lost_agents kills agents with stale heartbeats."""
        # Insert task first to satisfy FOREIGN KEY
        with db._write_lock:
            db.conn.execute(
                "INSERT INTO tasks (id, type, workflow_id, status, context, created_at) "
                "VALUES ('task-reap', 'test', 'test-wf', 'running', '{}', ?)",
                (now_iso(),))
            db.conn.commit()

        # Create a real subprocess
        p = subprocess.Popen(
            ["sleep", "30"],
            start_new_session=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        pgid = os.getpgid(p.pid)

        # Record an old heartbeat
        with db._write_lock:
            db.conn.execute(
                "INSERT OR REPLACE INTO heartbeat (task_id, step_id, agent_pid, last_beat) "
                "VALUES ('task-reap', 'dev_fix', ?, ?)",
                (p.pid, "2020-01-01T00:00:00"),
            )
            db.conn.commit()

        spawner = AgentSpawner(db, _dummy_roles())
        killed = spawner.reap_lost_agents(heartbeat_timeout=60)

        # Wait briefly for SIGTERM to take effect
        try:
            p.wait(timeout=3)
        except Exception:
            p.kill()
            p.wait(timeout=2)

        # Process should be dead
        try:
            os.kill(p.pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False

        assert killed >= 1
        assert not alive

    def test_reap_empty_db_returns_zero(self, db):
        """reap_lost_agents on clean DB returns 0."""
        spawner = AgentSpawner(db, _dummy_roles())
        killed = spawner.reap_lost_agents()
        assert killed == 0


# ── start_new_session ──


class TestSpawnProcessGroup:
    def test_spawn_creates_new_session(self, db):
        """spawn() should create subprocess with start_new_session=True."""
        # Use a custom adapter that runs a simple sleep command
        from multiagent.adapters.base import AgentAdapter

        class SleepAdapter(AgentAdapter):
            def name(self): return "test-sleep"
            def build_command(self, agent_config, task_prompt, step):
                return ["sleep", str(step.get("timeout", 5))]
            def parse_output(self, stdout, stderr):
                from multiagent.engine import StepResult, StepStatus
                return StepResult(
                    step_id=step["id"] if isinstance(step, dict) else "test",
                    agent="test", status=StepStatus.COMPLETED, output={})
            def _paths_to_tool_patterns(self, deny, write):
                return [], []

        roles = {"agents": {"test": {}}, "global": {"runtime": "test-sleep"}}
        adapter = SleepAdapter(Path("/tmp"))
        spawner = AgentSpawner(db, roles, adapter=adapter)

        # Insert task to satisfy FOREIGN KEY
        with db._write_lock:
            db.conn.execute(
                "INSERT INTO tasks (id, type, workflow_id, status, context, created_at) "
                "VALUES ('task-test', 'test', 'test-wf', 'running', '{}', ?)",
                (now_iso(),))
            db.conn.commit()

        task = Mock()
        task.id = "task-test"
        task.type = "feature"

        step = {
            "id": "test_step",
            "agent": "test",
            "description": "test",
            "timeout": 5,
            "input": {},
            "output": {},
        }

        p = spawner.spawn(task, step)
        try:
            pgid = os.getpgid(p.pid)
            assert pgid != os.getpid()  # Different process group = new session
        finally:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
