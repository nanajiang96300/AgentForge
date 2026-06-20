"""Gate: WorkflowOrchestrator 测试"""
import sys, uuid, json
from pathlib import Path
from multiagent.db import StateDB, Task, now_iso
from multiagent.engine import AgentSpawner, StepResult, StepStatus, load_yaml
from multiagent.orchestrator import WorkflowOrchestrator, WorkflowStep, StepState

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROLES = PROJECT_ROOT / "architectures" / "dev-test-loop" / "config" / "roles.yaml"
WF_PATH = PROJECT_ROOT / "architectures" / "dev-test-loop" / "workflow" / "dev-test.yaml"
DB = Path("/tmp/test_orchestrator.db")

def clean():
    if DB.exists(): DB.unlink()

def test_load_workflow():
    print("[TEST 1] Load workflow YAML...", end=" ", flush=True)
    clean(); db = StateDB(DB); db.connect()
    config = load_yaml(ROLES)
    spawner = AgentSpawner(db, config)
    orch = WorkflowOrchestrator(db, spawner, WF_PATH)
    orch.load()
    assert len(orch.steps) == 3
    assert "dev_fix" in orch.steps
    assert "test_verify" in orch.steps
    assert orch.steps["test_verify"].depends_on == ["dev_fix"]
    db.close(); clean()
    print("✅ PASS")

def test_dependency_resolution():
    print("[TEST 2] Dependency resolution...", end=" ", flush=True)
    clean(); db = StateDB(DB); db.connect()
    config = load_yaml(ROLES)
    spawner = AgentSpawner(db, config)
    orch = WorkflowOrchestrator(db, spawner, WF_PATH)
    orch.load()

    task = Task(id=f"t-{uuid.uuid4().hex[:6]}", type="bugfix", source="test",
                workflow_id="dev-test-loop", current_step="dev_fix", created_at=now_iso())
    db.insert_task(task)

    # 初始状态：dev_fix 应该就绪（无依赖），test_verify 应 pending
    ready = orch.get_ready_steps(task)
    ready_ids = [s.id for s in ready]
    assert "dev_fix" in ready_ids, f"dev_fix should be ready, got {ready_ids}"
    assert "test_verify" not in ready_ids, "test_verify should wait for dev_fix"

    # 模拟 dev_fix 完成
    orch._step_results["dev_fix"] = {"branch_name": "bugfix/1", "pr_number": "42", "files_changed": ["x.cpp"]}
    orch.steps["dev_fix"].state = StepState.COMPLETED
    orch.steps["notify_conductor"].state = StepState.SKIPPED  # 条件不满足

    ready2 = orch.get_ready_steps(task)
    ready_ids2 = [s.id for s in ready2]
    assert "test_verify" in ready_ids2, f"test_verify should be ready after dev_fix, got {ready_ids2}"

    db.close(); clean()
    print("✅ PASS")

def test_step_input_resolution():
    print("[TEST 3] Step input resolution...", end=" ", flush=True)
    orch = WorkflowOrchestrator(None, None, WF_PATH)
    orch.load()
    orch._step_results["pm_analyze"] = {"root_cause": "null ptr", "target_module": "calc.cpp", "complexity": "simple"}

    # 模拟一个需要 pm_analyze.output 的步骤
    user_step = WorkflowStep(id="test", agent="dev", input={
        "from": "pm_analyze.output",
        "fields": ["root_cause", "target_module"]
    })
    inp = orch.build_step_input(user_step, Task(id="t1", type="bugfix", source="t", workflow_id="w", current_step="s"))
    assert inp["root_cause"] == "null ptr"
    assert inp["target_module"] == "calc.cpp"
    assert "complexity" not in inp  # 不在 fields 中
    print("✅ PASS")

def test_task_context_input():
    print("[TEST 4] Task context input...", end=" ", flush=True)
    orch = WorkflowOrchestrator(None, None, WF_PATH)
    orch.load()
    task = Task(id="t1", type="feature", source="pm", workflow_id="pm-dev-test",
                current_step="pm_analyze", context={"requirements": "Build a web app", "project_type": "flask"})
    step = WorkflowStep(id="pm_analyze", agent="pm", input={
        "from": "task.context", "fields": ["requirements", "project_type"]
    })
    inp = orch.build_step_input(step, task)
    assert inp["requirements"] == "Build a web app"
    assert inp["project_type"] == "flask"
    print("✅ PASS")

def test_rejection_loop():
    print("[TEST 5] Rejection loop...", end=" ", flush=True)
    clean(); db = StateDB(DB); db.connect()

    # 手动测试 rejection_counter
    task = Task(id=f"t-{uuid.uuid4().hex[:6]}", type="bugfix", source="test",
                workflow_id="dev-test-loop", current_step="dev_fix", created_at=now_iso())
    db.insert_task(task)
    assert db.increment_rejection(task.id) == 1
    assert db.increment_rejection(task.id) == 2
    assert db.increment_rejection(task.id) == 3  # 到达 max_rejections
    db.close(); clean()
    print("✅ PASS")

def test_condition_check():
    print("[TEST 6] Condition evaluation...", end=" ", flush=True)
    orch = WorkflowOrchestrator(None, None, WF_PATH)
    assert orch._check_condition("test_verify.output.verdict == 'approved'",
                                  {"test_verify": {"output": {"verdict": "approved"}}})
    assert not orch._check_condition("test_verify.output.verdict == 'approved'",
                                      {"test_verify": {"output": {"verdict": "rejected"}}})
    print("✅ PASS")

if __name__ == "__main__":
    tests = [
        ("Load workflow", test_load_workflow),
        ("Dependency resolution", test_dependency_resolution),
        ("Step input resolution", test_step_input_resolution),
        ("Task context input", test_task_context_input),
        ("Rejection loop", test_rejection_loop),
        ("Condition evaluation", test_condition_check),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"❌ FAIL: {e}")
    print(f"  {passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
