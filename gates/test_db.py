"""Gate: StateDB CRUD + 并发去重"""
import sys, uuid, json
from pathlib import Path
from multiagent.db import StateDB, Task, now_iso

DB = Path("/tmp/test_multiagent_gate.db")

def clean():
    if DB.exists(): DB.unlink()

def test_crud():
    clean(); db = StateDB(DB); db.connect()
    t = Task(id=f"t-{uuid.uuid4().hex[:6]}", type="bugfix", source="test",
             workflow_id="dev-test", current_step="dev_fix", dedup_key=f"dk-{uuid.uuid4().hex[:6]}", created_at=now_iso())
    assert db.insert_task(t)
    claimed = db.claim_pending_task("dev-test")
    assert claimed and claimed.id == t.id
    db.update_task_status(t.id, "completed")
    assert db.claim_pending_task("dev-test") is None
    db.close(); clean()
    return True

def test_dedup():
    clean(); db = StateDB(DB); db.connect()
    dk = f"dk-{uuid.uuid4().hex[:8]}"
    t1 = Task(id=f"t1-{uuid.uuid4().hex[:6]}", type="bugfix", source="t", workflow_id="w", current_step="s", dedup_key=dk, created_at=now_iso())
    t2 = Task(id=f"t2-{uuid.uuid4().hex[:6]}", type="bugfix", source="t", workflow_id="w", current_step="s", dedup_key=dk, created_at=now_iso())
    assert db.insert_task(t1)
    assert not db.insert_task(t2)
    db.close(); clean()
    return True

def test_metrics_table():
    clean(); db = StateDB(DB); db.connect()
    tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "agent_metrics" in tables
    db.close(); clean()
    return True

def test_context():
    clean(); db = StateDB(DB); db.connect()
    ctx = {"requirements": "Build a web app", "project_type": "flask"}
    t = Task(id=f"t-{uuid.uuid4().hex[:6]}", type="feature", source="pm",
             workflow_id="pm-dev-test", current_step="pm_analyze",
             context=ctx, dedup_key=f"dk-{uuid.uuid4().hex[:6]}", created_at=now_iso())
    assert db.insert_task(t)
    task_data = db.get_task(t.id)
    stored_ctx = json.loads(task_data["context"]) if isinstance(task_data["context"], str) else task_data["context"]
    assert stored_ctx["requirements"] == "Build a web app"
    db.close(); clean()
    return True

def test_rejection_counter():
    clean(); db = StateDB(DB); db.connect()
    t = Task(id=f"t-{uuid.uuid4().hex[:6]}", type="bugfix", source="test",
             workflow_id="pm-dev-test", current_step="dev_fix",
             dedup_key=f"dk-{uuid.uuid4().hex[:6]}", created_at=now_iso())
    assert db.insert_task(t)
    assert db.increment_rejection(t.id) == 1
    assert db.increment_rejection(t.id) == 2
    assert db.increment_rejection(t.id) == 3
    db.close(); clean()
    return True

if __name__ == "__main__":
    tests = [("CRUD", test_crud), ("Dedup", test_dedup), ("Metrics table", test_metrics_table),
             ("Context", test_context), ("Rejection counter", test_rejection_counter)]
    results = []
    for name, fn in tests:
        try:
            ok = fn()
            print(f"  ✅ {name}")
            results.append(ok)
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            results.append(False)
    passed = sum(results)
    print(f"  {passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)
