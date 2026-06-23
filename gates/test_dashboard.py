"""Phase 5 Gate: Dashboard /api/status + /health + /api/state endpoints"""

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multiagent.dashboard import create_dashboard_app
from multiagent.db import StateDB, Task


# ── Helpers ──


def _make_temp_db():
    """Create a temporary state.db with schema initialized."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def _seed_task(db: StateDB, task_id: str, status: str, wf_id: str = "test-wf"):
    """Insert a single task with given status into the DB."""
    t = Task(
        id=task_id,
        type="bugfix",
        source="test",
        workflow_id=wf_id,
        current_step=None,
        status=status,
    )
    db.insert_task(t)


# ── Fixtures ──


@pytest.fixture
def client(tmp_path):
    """Create a Flask test client with an empty temporary DB (no side effects)."""
    db_path = tmp_path / "test_state.db"
    app = create_dashboard_app(db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def seeded_client():
    """Create a Flask test client backed by a temporary seeded DB."""
    db_path = _make_temp_db()
    # Seed the DB with diverse task statuses
    db = StateDB(db_path)
    db.connect()
    try:
        _seed_task(db, "task-1", "pending")
        _seed_task(db, "task-2", "pending")
        _seed_task(db, "task-3", "running")
        _seed_task(db, "task-4", "running")
        _seed_task(db, "task-5", "running")
        _seed_task(db, "task-6", "completed")
        _seed_task(db, "task-7", "failed")
        _seed_task(db, "task-8", "failed")
        _seed_task(db, "task-9", "escalated")
    finally:
        db.close()

    app = create_dashboard_app(db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    # Cleanup
    os.unlink(db_path)


# ── Page Rendering Tests (catches JS quoting bugs at Python level) ──


class TestPageRendering:
    """Tests that dashboard pages render without Python errors."""

    def test_index_page_renders(self, client):
        """GET / returns 200 and contains expected HTML structure."""
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "AgentForge Dashboard" in html
        assert "<table" in html
        assert "chart.js" in html.lower()
        assert "mermaid" in html.lower()

    def test_designer_page_renders(self, client):
        """GET /designer returns 200 and contains designer elements."""
        resp = client.get("/designer")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Workflow Designer" in html

    def test_commands_page_renders(self, client):
        """GET /commands returns 200 and contains command input."""
        resp = client.get("/commands")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Command Center" in html

    def test_timeseries_endpoint(self, client):
        """GET /api/timeseries returns valid JSON with expected keys."""
        resp = client.get("/api/timeseries")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token_trend" in data
        assert "pass_rate" in data

    def test_workflow_dag_endpoint(self, client):
        """GET /api/workflow-dag returns valid JSON with nodes and edges."""
        resp = client.get("/api/workflow-dag")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "nodes" in data
        assert "edges" in data


# ── /api/status Tests (Task 1: correct counts) ──


class TestApiStatusEndpoint:
    """Tests for the GET /api/status endpoint."""

    # --- Task 1: Correct queue counts ---

    def test_returns_200_with_all_five_fields(self, client):
        """GET /api/status returns 200 with all five queue status fields."""
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        for field in ("pending", "running", "completed", "failed", "escalated"):
            assert field in data, f"Missing field: {field}"
            assert isinstance(data[field], int), f"{field} should be int"

    def test_returns_zero_counts_when_no_db(self, client):
        """Without a DB, all counts are zero and status is 200."""
        resp = client.get("/api/status")
        data = resp.get_json()
        assert data["pending"] == 0
        assert data["running"] == 0
        assert data["completed"] == 0
        assert data["failed"] == 0
        assert data["escalated"] == 0

    def test_returns_correct_counts_with_seeded_db(self, seeded_client):
        """With a seeded DB, counts match the inserted task statuses."""
        resp = seeded_client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pending"] == 2
        assert data["running"] == 3
        assert data["completed"] == 1
        assert data["failed"] == 2
        assert data["escalated"] == 1

    def test_total_counts_match_tasks_count(self, seeded_client):
        """Sum of all counts equals total tasks in the DB."""
        resp = seeded_client.get("/api/status")
        data = resp.get_json()
        total = sum(data.values())
        assert total == 9  # 2+3+1+2+1

    # --- Task 2: Graceful handling of missing/invalid DB ---

    def test_handles_nonexistent_db_path(self, tmp_path):
        """Returns all zeros when the DB file does not exist (graceful degradation)."""
        nonexistent = tmp_path / "ghost.db"
        app = create_dashboard_app(nonexistent)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/api/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["pending"] == 0

    def test_handles_corrupt_db_file(self, tmp_path):
        """Returns all zeros when the DB file is corrupt/unreadable (graceful degradation)."""
        bad_db = tmp_path / "corrupt.db"
        bad_db.write_text("this is not a valid sqlite database")
        app = create_dashboard_app(bad_db)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/api/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["pending"] == 0

    # --- Content type and method checks ---

    def test_content_type_is_json(self, client):
        """GET /api/status response content-type is application/json."""
        resp = client.get("/api/status")
        assert "application/json" in resp.content_type

    def test_post_returns_405(self, client):
        """POST /api/status returns 405 Method Not Allowed."""
        resp = client.post("/api/status")
        assert resp.status_code == 405


# ── /api/state Tests ──


class TestApiStateEndpoint:
    """Tests for the GET /api/state endpoint (partial status)."""

    def test_returns_200_with_three_fields(self, client):
        """GET /api/state returns 200 with pending, running, escalated."""
        resp = client.get("/api/state")
        assert resp.status_code == 200
        data = resp.get_json()
        for field in ("pending", "running", "escalated"):
            assert field in data

    def test_content_type_is_json(self, client):
        """GET /api/state response content-type is application/json."""
        resp = client.get("/api/state")
        assert "application/json" in resp.content_type


# ── /health Tests (Task 3: verify unaffected) ──


class TestHealthEndpoint:
    """Tests for the /health endpoint — verify unaffected by /api/status."""

    def test_get_returns_200_with_correct_json(self, client):
        """GET /health returns 200 with status and version."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "version" in data
        assert isinstance(data["version"], str)

    def test_post_returns_405(self, client):
        """POST /health returns 405 Method Not Allowed."""
        resp = client.post("/health")
        assert resp.status_code == 405

    def test_put_returns_405(self, client):
        """PUT /health returns 405 Method Not Allowed."""
        resp = client.put("/health")
        assert resp.status_code == 405

    def test_delete_returns_405(self, client):
        """DELETE /health returns 405 Method Not Allowed."""
        resp = client.delete("/health")
        assert resp.status_code == 405

    def test_content_type_is_json(self, client):
        """GET /health response content-type is application/json."""
        resp = client.get("/health")
        assert "application/json" in resp.content_type
