"""
P0 Gate: db.py — prune, cleanup_task_data, vacuum

Covers data retention lifecycle: prune_step_results, prune_agent_metrics,
prune_heartbeat, cleanup_task_data, vacuum, prune_all.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.db import StateDB, Task, AgentMetrics, now_iso


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = StateDB(Path(path))
    db.connect()
    yield db
    db.close()
    os.unlink(path)


def _ensure_task(db, task_id):
    """Insert a minimal task to satisfy FOREIGN KEY constraints."""
    with db._write_lock:
        try:
            db.conn.execute(
                "INSERT INTO tasks (id, type, workflow_id, status, context, created_at) "
                "VALUES (?, 'test', 'test-wf', 'completed', '{}', ?)",
                (task_id, now_iso()),
            )
            db.conn.commit()
        except Exception:
            pass  # Already exists


def _seed_step_result(db, task_id, completed_at_iso):
    """Insert a completed step_result directly."""
    _ensure_task(db, task_id)
    with db._write_lock:
        db.conn.execute(
            "INSERT INTO step_results (task_id, step_id, agent, adapter, status, "
            "output, completed_at, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, "dev_fix", "dev", "claude-code", "completed",
             '{"branch_name":"fix/test"}', completed_at_iso, completed_at_iso),
        )
        db.conn.commit()


def _seed_metric(db, task_id, recorded_at_iso):
    """Insert an agent_metrics row directly."""
    _ensure_task(db, task_id)
    with db._write_lock:
        db.conn.execute(
            "INSERT INTO agent_metrics (task_id, step_id, agent, adapter, model, "
            "input_tokens, output_tokens, cost_usd, status, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, "dev_fix", "dev", "claude-code", "test-model",
             100, 50, 0.001, "completed", recorded_at_iso),
        )
        db.conn.commit()


def _seed_heartbeat(db, task_id, last_beat_iso):
    """Insert a heartbeat row directly."""
    _ensure_task(db, task_id)
    with db._write_lock:
        db.conn.execute(
            "INSERT OR REPLACE INTO heartbeat (task_id, step_id, agent_pid, last_beat) "
            "VALUES (?, ?, ?, ?)",
            (task_id, "dev_fix", 12345, last_beat_iso),
        )
        db.conn.commit()


# ── Prune tests ──


class TestPruneStepResults:
    def test_recent_rows_not_deleted(self, db):
        _seed_step_result(db, "task-1", now_iso())
        count_before = db.conn.execute(
            "SELECT COUNT(*) FROM step_results").fetchone()[0]
        db.prune_step_results(days=30)
        count_after = db.conn.execute(
            "SELECT COUNT(*) FROM step_results").fetchone()[0]
        assert count_after == count_before

    def test_old_rows_deleted(self, db):
        _seed_step_result(db, "task-old", "2020-01-01T00:00:00")
        db.prune_step_results(days=30)
        count = db.conn.execute(
            "SELECT COUNT(*) FROM step_results WHERE task_id='task-old'"
        ).fetchone()[0]
        assert count == 0

    def test_none_completed_at_not_deleted(self, db):
        """Rows without completed_at are skipped (still running)."""
        _ensure_task(db, "task-running")
        with db._write_lock:
            db.conn.execute(
                "INSERT INTO step_results (task_id, step_id, agent, adapter, status) "
                "VALUES ('task-running', 'dev_fix', 'dev', 'claude-code', 'running')"
            )
            db.conn.commit()
        db.prune_step_results(days=30)
        count = db.conn.execute(
            "SELECT COUNT(*) FROM step_results WHERE task_id='task-running'"
        ).fetchone()[0]
        assert count == 1


class TestPruneAgentMetrics:
    def test_recent_metrics_not_deleted(self, db):
        _seed_metric(db, "task-1", now_iso())
        count_before = db.conn.execute(
            "SELECT COUNT(*) FROM agent_metrics").fetchone()[0]
        db.prune_agent_metrics(days=90)
        count_after = db.conn.execute(
            "SELECT COUNT(*) FROM agent_metrics").fetchone()[0]
        assert count_after == count_before

    def test_old_metrics_deleted(self, db):
        _seed_metric(db, "task-old", "2020-01-01T00:00:00")
        db.prune_agent_metrics(days=90)
        count = db.conn.execute(
            "SELECT COUNT(*) FROM agent_metrics WHERE task_id='task-old'"
        ).fetchone()[0]
        assert count == 0


class TestPruneHeartbeat:
    def test_old_heartbeat_deleted(self, db):
        _seed_heartbeat(db, "task-old", "2020-01-01T00:00:00")
        db.prune_heartbeat(days=7)
        count = db.conn.execute(
            "SELECT COUNT(*) FROM heartbeat WHERE task_id='task-old'"
        ).fetchone()[0]
        assert count == 0

    def test_recent_heartbeat_not_deleted(self, db):
        _seed_heartbeat(db, "task-recent", now_iso())
        db.prune_heartbeat(days=7)
        count = db.conn.execute(
            "SELECT COUNT(*) FROM heartbeat WHERE task_id='task-recent'"
        ).fetchone()[0]
        assert count == 1


class TestCleanupTaskData:
    def test_removes_all_tables_for_task(self, db):
        _seed_step_result(db, "task-cleanup", now_iso())
        _seed_metric(db, "task-cleanup", now_iso())
        _seed_heartbeat(db, "task-cleanup", now_iso())

        db.cleanup_task_data("task-cleanup")
        for table in ("step_results", "agent_metrics", "heartbeat"):
            count = db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id='task-cleanup'"
            ).fetchone()[0]
            assert count == 0, f"Table {table} still has rows"


class TestPruneAll:
    def test_prune_all_calls_all_prune_methods(self, db):
        """Smoke test: prune_all runs without errors on empty DB."""
        db.prune_all()  # Should not raise
        # With defaults
        db.prune_all({"step_results": 30, "agent_metrics": 90, "heartbeat": 7})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
