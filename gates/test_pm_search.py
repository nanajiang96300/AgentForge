"""Gate: PM Search — keyword + status filtering"""
import sys, uuid, json
from pathlib import Path
from multiagent.db import StateDB, Task, now_iso

DB = Path("/tmp/test_pm_search_gate.db")


def clean():
    if DB.exists():
        DB.unlink()


def _setup_db():
    """Insert test tasks with known context values."""
    clean()
    db = StateDB(DB)
    db.connect()

    tasks = [
        Task(
            id="task-test1", type="feature", source="pm",
            workflow_id="pm-dev-test-loop", current_step="pm_analyze",
            status="pending", dedup_key="dk-search1",
            context={"requirements_text": "Build a health check endpoint", "source_file": "/tmp/req1.md"},
            created_at="2026-01-01T00:00:00Z",
        ),
        Task(
            id="task-test2", type="bugfix", source="pm",
            workflow_id="pm-dev-test-loop", current_step="dev_fix",
            status="running", dedup_key="dk-search2",
            context={"requirements_text": "Fix the login page crash", "source_file": "/tmp/req2.md"},
            created_at="2026-01-02T00:00:00Z",
        ),
        Task(
            id="task-test3", type="feature", source="pm",
            workflow_id="pm-dev-test-loop", current_step="test_verify",
            status="completed", dedup_key="dk-search3",
            context={"requirements_text": "Add /health endpoint to server", "source_file": "/tmp/req3.md"},
            created_at="2026-01-03T00:00:00Z",
        ),
    ]
    for t in tasks:
        db.insert_task(t)
    return db


def test_search_by_keyword():
    """Search tasks matching a keyword in requirements text."""
    db = _setup_db()
    results = db.search_tasks("health")
    db.close()
    clean()
    assert len(results) == 2
    ids = set(r["id"] for r in results)
    assert "task-test1" in ids
    assert "task-test3" in ids
    assert "task-test2" not in ids
    return True


def test_search_by_keyword_no_results():
    """Empty results when keyword matches nothing."""
    db = _setup_db()
    results = db.search_tasks("nonexistent_keyword_xyz")
    db.close()
    clean()
    assert len(results) == 0
    return True


def test_search_with_status_filter():
    """Combine keyword search with status filtering."""
    db = _setup_db()
    results = db.search_tasks("health", status="completed")
    db.close()
    clean()
    assert len(results) == 1
    assert results[0]["id"] == "task-test3"
    return True


def test_search_status_only_match():
    """Status filter narrows results - no match when status mismatch."""
    db = _setup_db()
    results = db.search_tasks("login", status="completed")
    db.close()
    clean()
    assert len(results) == 0
    return True


def test_search_all_statuses():
    """Search with each valid status filter."""
    db = _setup_db()
    for status in ["pending", "running", "completed", "failed", "escalated"]:
        results = db.search_tasks("endpoint", status=status)
        if status == "completed":
            assert len(results) == 1
            assert results[0]["id"] == "task-test3"
        elif status == "pending":
            assert len(results) == 1
            assert results[0]["id"] == "task-test1"
        else:
            assert len(results) == 0
    db.close()
    clean()
    return True


def test_context_parsed_in_results():
    """Search results include parsed context dict."""
    db = _setup_db()
    results = db.search_tasks("login")
    db.close()
    clean()
    assert len(results) == 1
    ctx = results[0].get("context_parsed")
    assert isinstance(ctx, dict)
    assert "requirements_text" in ctx
    assert "login" in ctx["requirements_text"].lower()
    return True


def test_search_returns_ordered_by_date():
    """Results return ordered by created_at DESC (newest first)."""
    db = _setup_db()
    results = db.search_tasks("health")
    db.close()
    clean()
    assert len(results) >= 2
    assert results[0]["id"] == "task-test3"
    return True


if __name__ == "__main__":
    tests = [
        ("Search by keyword", test_search_by_keyword),
        ("Empty results", test_search_by_keyword_no_results),
        ("Status filter", test_search_with_status_filter),
        ("Status mismatch", test_search_status_only_match),
        ("All statuses", test_search_all_statuses),
        ("Context parsed", test_context_parsed_in_results),
        ("Order by date", test_search_returns_ordered_by_date),
    ]
    results = []
    for name, fn in tests:
        try:
            ok = fn()
            print(f"  OK {name}")
            results.append(ok)
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            results.append(False)
    passed = sum(results)
    print(f"  {passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)
