"""Phase 3 Gate: Metrics CLI — token/cost 统计命令"""
import pytest
import sys
import os
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.metrics_cli import parse_metrics_args, cmd_metrics
from multiagent.db import StateDB, Task, AgentMetrics, now_iso


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
def db_with_metrics(tmp_db):
    """DB pre-populated with sample tasks and metrics"""
    # Insert tasks first (FK requirement)
    for tid in ["task-1", "task-2"]:
        tmp_db.insert_task(Task(
            id=tid, type="bug", source="test",
            workflow_id="test-wf", current_step=None,
            created_at=now_iso(),
        ))
    metrics = [
        AgentMetrics(task_id="task-1", step_id="pm_analyze", agent="pm",
                     adapter="claude-code", model="test-model",
                     input_tokens=10000, output_tokens=2000, cost_usd=0.15,
                     duration_ms=30000, num_turns=2, status="completed"),
        AgentMetrics(task_id="task-1", step_id="dev_fix", agent="dev",
                     adapter="claude-code", model="test-model",
                     input_tokens=5000, output_tokens=3000, cost_usd=0.20,
                     duration_ms=45000, num_turns=5, status="completed"),
        AgentMetrics(task_id="task-2", step_id="pm_analyze", agent="pm",
                     adapter="claude-code", model="test-model",
                     input_tokens=7000, output_tokens=1500, cost_usd=0.10,
                     duration_ms=20000, num_turns=1, status="completed"),
    ]
    for m in metrics:
        tmp_db.record_metrics(m)
    return tmp_db


class TestParseMetricsArgs:
    def test_default(self):
        """3.5.1: Parse args with defaults"""
        args = parse_metrics_args(["metrics"])
        assert args["agent"] is None
        assert args["task_id"] is None
        assert args["json"] is False

    def test_agent_filter(self):
        """3.5.2: Parse --agent flag"""
        args = parse_metrics_args(["metrics", "--agent", "pm"])
        assert args["agent"] == "pm"

    def test_json_output(self):
        """3.5.3: Parse --json flag"""
        args = parse_metrics_args(["metrics", "--json"])
        assert args["json"] is True


class TestMetricsEmpty:
    def test_empty_db(self, tmp_db, capsys):
        """3.5.4: Empty DB shows helpful message"""
        cmd_metrics(db=tmp_db)
        captured = capsys.readouterr()
        assert "No metrics recorded" in captured.out


class TestMetricsWithData:
    def test_summary_output(self, db_with_metrics, capsys):
        """3.5.5: Summary shows totals"""
        cmd_metrics(db=db_with_metrics)
        captured = capsys.readouterr()
        assert "3" in captured.out  # total calls
        assert "22,000" in captured.out  # total input tokens

    def test_agent_filter(self, db_with_metrics, capsys):
        """3.5.6: --agent pm shows only PM metrics"""
        cmd_metrics(db=db_with_metrics, agent="pm")
        captured = capsys.readouterr()
        assert "pm" in captured.out.lower()

    def test_json_output(self, db_with_metrics, capsys):
        """3.5.7: --json outputs valid JSON"""
        cmd_metrics(db=db_with_metrics, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["total_calls"] == 3
        assert data["total_cost_usd"] == pytest.approx(0.45, abs=0.01)
        assert "per_agent" in data
        assert len(data["per_agent"]) == 2  # pm, dev

    def test_json_details(self, db_with_metrics, capsys):
        """3.5.8: --json --details shows per-call records"""
        cmd_metrics(db=db_with_metrics, json_output=True, details=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "calls" in data
        assert len(data["calls"]) == 3

    def test_task_filter(self, db_with_metrics, capsys):
        """3.5.9: --task-id filters by task"""
        cmd_metrics(db=db_with_metrics, task_id="task-2")
        captured = capsys.readouterr()
        assert "task-2" in captured.out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
