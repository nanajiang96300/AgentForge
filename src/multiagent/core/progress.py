"""
Generic task progress calculation.

No hardcoded step names. Reads steps dynamically from step_results table.
Works with any workflow (3-step, 4-step, N-step).
"""


def progress_bar(pct: float, width: int = 16) -> str:
    """ASCII progress bar: [████░░░░░░░░░░░░] 67%"""
    filled = int(width * pct / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct:.0f}%"


def calculate_task_progress(db, task_id: str) -> dict:
    """Calculate completion progress for any task.

    Dynamically reads steps from step_results table.
    No hardcoded step names — works with any workflow configuration.

    Returns {pct, stage, total_steps, completed_steps, bar, subtasks_done, subtasks_total}
    """
    result = {
        "pct": 0, "stage": "pending", "bar": progress_bar(0),
        "total_steps": 0, "completed_steps": 0,
        "subtasks_done": 0, "subtasks_total": 0,
    }

    # Get all steps for this task
    rows = db.execute(
        "SELECT step_id, agent, status FROM step_results "
        "WHERE task_id = ? ORDER BY id",
        (task_id,)
    ).fetchall()

    if not rows:
        result["stage"] = "pending"
        result["pct"] = 5
        result["bar"] = progress_bar(5)
        return result

    # Build step status map (last status per step wins)
    step_status = {}
    for r in rows:
        step_status[r[0]] = r[2]

    total = len(step_status)
    completed = sum(1 for s in step_status.values() if s == "completed")
    running = any(s == "running" for s in step_status.values())
    has_failed = any(s in ("failed", "rejected") for s in step_status.values())

    result["total_steps"] = total
    result["completed_steps"] = completed

    # Estimate subtask progress from latest completed step output
    _estimate_subtasks(db, task_id, result)

    # Stage determination (dynamic, no hardcoded names)
    if has_failed:
        result["stage"] = "failed"
        result["pct"] = max(10, completed * 100 // max(total, 1))
    elif completed == total:
        result["stage"] = "done"
        result["pct"] = 100
        result["bar"] = progress_bar(100)
        return result
    elif running:
        # Find which step is running
        running_step = next((s for s in step_status if step_status[s] == "running"), None)
        if running_step:
            result["stage"] = running_step
        else:
            result["stage"] = "running"
        # Proportional progress: each completed step contributes equally
        base_pct = (completed * 100) // total
        # Bonus from subtask completion within current step
        bonus = 0
        if result["subtasks_total"] > 0:
            bonus = min(100 // total,
                       result["subtasks_done"] * (100 // total) // result["subtasks_total"])
        result["pct"] = min(99, base_pct + bonus)
    elif completed > 0:
        result["stage"] = "partial"
        result["pct"] = (completed * 100) // total
    else:
        result["stage"] = "pending"
        result["pct"] = 5

    result["bar"] = progress_bar(result["pct"])
    return result


def _estimate_subtasks(db, task_id: str, result: dict):
    """Estimate subtask completion from step outputs.

    Checks the latest completed step's output for subtask/task_breakdown info.
    """
    import json as _json

    # Find the latest completed step with output
    row = db.execute(
        "SELECT step_id, output FROM step_results "
        "WHERE task_id = ? AND status = 'completed' AND output IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (task_id,)
    ).fetchone()

    if not row or not row[1]:
        return

    try:
        out = _json.loads(row[1])
    except Exception:
        return

    resp = out.get("response", "")
    if not resp:
        return

    # Try to extract JSON block from response
    jb = _extract_json(resp)
    if not jb:
        return

    # Count subtasks from task_breakdown (PM output)
    tb = jb.get("task_breakdown", [])
    if isinstance(tb, list) and len(tb) > 0:
        result["subtasks_total"] = max(result["subtasks_total"], len(tb))

    # Count completed subtasks
    sc = jb.get("subtasks_completed", jb.get("tasks_completed", []))
    if isinstance(sc, list) and len(sc) > 0:
        done = sum(1 for x in sc
                   if isinstance(x, dict) and x.get("status") in ("done", "completed"))
        if done == 0:
            done = len(sc)
        result["subtasks_done"] = max(result["subtasks_done"], done)

    # Also estimate from files_changed vs estimated_files
    fc = jb.get("files_changed", [])
    ef = jb.get("estimated_files", [])
    if isinstance(fc, list) and isinstance(ef, list) and len(ef) > 0:
        hit = len(set(str(f) for f in fc) & set(str(e) for e in ef))
        result["subtasks_done"] = max(result["subtasks_done"], hit)
        result["subtasks_total"] = max(result["subtasks_total"], len(ef))


def _extract_json(text: str) -> dict | None:
    """Extract JSON block from agent response text."""
    import json as _json
    import re as _re

    # Try ```json block first
    m = _re.search(r'```json\s*\n(.*?)\n```', text, _re.DOTALL)
    if m:
        try:
            return _json.loads(m.group(1))
        except Exception:
            pass

    # Try bare JSON objects
    for m in _re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, _re.DOTALL):
        try:
            return _json.loads(m.group())
        except Exception:
            continue

    return None
