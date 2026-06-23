"""
Web Dashboard — Flask app with Jinja2 templates and static assets.

Routes:
  GET  /                    Pipeline Monitoring (main dashboard)
  GET  /designer            Visual Workflow Designer
  GET  /commands            Web Command Center
  GET  /api/state           {{pending, running, escalated}}
  GET  /api/status          {{pending, running, completed, failed, escalated}}
  GET  /api/timeseries      {{token_trend, pass_rate}} (7-day)
  GET  /api/workflow-dag    {{nodes, edges, workflow_id}}
  GET  /health              {{status, version}}
  POST /api/graph/export    JSON graph -> YAML
  POST /api/graph/from-agents  Agent names -> linear graph
"""

import json
from pathlib import Path

from flask import Flask, jsonify, request, render_template

from .db import StateDB
from .core.progress import calculate_task_progress
from .config.loader import find_state_db


def _token_fmt(n):
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(n)


def _badge(status):
    return f'<span class="badge {status}">{status}</span>'


def _pipeline_html(db, task_id):
    rows = db.conn.execute(
        "SELECT DISTINCT step_id, status FROM step_results "
        "WHERE task_id = ? ORDER BY id", (task_id,)
    ).fetchall()
    seen = set()
    steps = []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            steps.append((r[0], r[1]))
    if not steps:
        return '<div class="pipeline"><span class="step pending">—</span></div>'

    parts = ['<div class="pipeline">']
    for i, (sid, status) in enumerate(steps):
        if i > 0:
            parts.append('<span class="arrow">→</span>')
        label = sid.replace("_", " ").title()[:12]
        cls = "done" if status == "completed" else (
            "active" if status == "running" else "pending")
        parts.append(f'<span class="step {cls}">{label}</span>')
    parts.append('</div>')
    return ''.join(parts)


def _progress_html(db, task_id):
    p = calculate_task_progress(db, task_id)
    pct = p["pct"]
    stage = p["stage"]
    cls = ("done" if stage == "done" else
           "test" if "test" in stage else
           "dev" if "dev" in stage else "pm")
    subtasks = ""
    if p.get("subtasks_total", 0) > 0:
        subtasks = f' ({p.get("subtasks_done", 0)}/{p["subtasks_total"]})'
    elif p.get("completed_steps", 0) > 0:
        subtasks = f' ({p["completed_steps"]}/{p["total_steps"]} steps)'
    return (
        f'<div class="progress-bar">'
        f'<div class="bar"><div class="fill {cls}" style="width:{pct}%"></div></div>'
        f'<div class="pct">{pct}%{subtasks}</div></div>'
    )


def create_dashboard_app(db_path: Path = None) -> Flask:
    if db_path is None:
        db_path = find_state_db()

    _here = Path(__file__).resolve().parent
    app = Flask(__name__,
                template_folder=str(_here / "templates"),
                static_folder=str(_here / "static"))

    # ── Page Routes ──

    @app.route("/")
    def index():
        db = StateDB(db_path)
        db.connect()
        try:
            pending = db.get_pending_tasks()
            escalated = db.get_escalated_tasks()
            row = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'running'"
            ).fetchone()
            running_count = row[0] if row else 0
            comp = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'completed'"
            ).fetchone()
            completed_count = comp[0] if comp else 0

            metrics_row = db.conn.execute(
                "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), "
                "SUM(cost_usd) FROM agent_metrics"
            ).fetchone()

            metrics = None
            if metrics_row and metrics_row[0]:
                metrics = {
                    "total_calls": metrics_row[0],
                    "input_tokens_fmt": _token_fmt(metrics_row[1] or 0),
                    "output_tokens_fmt": _token_fmt(metrics_row[2] or 0),
                    "total_cost_usd": metrics_row[3] or 0,
                }

            task_rows = db.conn.execute(
                "SELECT id, type, status, current_step, retry_count, "
                "rejection_count, created_at FROM tasks "
                "ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            cols = ["id", "type", "status", "current_step",
                    "retry_count", "rejection_count", "created_at"]
            tasks = [dict(zip(cols, r)) for r in task_rows]

            in_flight = []
            for t in tasks:
                if t["status"] in ("running", "assigned"):
                    m = db.conn.execute(
                        "SELECT SUM(input_tokens), SUM(output_tokens), "
                        "SUM(cost_usd) FROM agent_metrics WHERE task_id = ?",
                        (t["id"],)
                    ).fetchone()
                    t["tokens_in_fmt"] = _token_fmt(m[0] or 0)
                    t["tokens_out_fmt"] = _token_fmt(m[1] or 0)
                    t["cost"] = m[2] or 0
                    t["pipeline_html"] = _pipeline_html(db, t["id"])
                    t["progress_html"] = _progress_html(db, t["id"])
                    in_flight.append(t)

            # Add pipeline HTML to all tasks for table rendering
            for t in tasks:
                t["pipeline_html"] = _pipeline_html(db, t["id"])

            return render_template("index.html",
                pending_count=len(pending),
                running_count=running_count,
                completed_count=completed_count,
                escalated_count=len(escalated),
                metrics=metrics,
                tasks=tasks[:50],
                in_flight=in_flight,
                active_page="index",
                auto_refresh=True,
            )
        finally:
            db.close()

    @app.route("/designer")
    def designer():
        from .runtime.registry import AgentRegistry
        agents_list = [{
            "name": a.name,
            "timeout": a.timeout,
            "description": a.description[:60],
            "output_required": a.output_required,
            "permissions": a.permissions,
            "skill": a.skill,
            "model": a.model,
            "session": a.session,
        } for a in AgentRegistry.list_all()]

        templates = {
            a["name"]: {
                "description": a["description"],
                "timeout": a["timeout"],
                "output_required": a["output_required"],
                "permissions": a["permissions"],
                "skill": a["skill"],
                "model": a["model"],
                "session": a["session"],
            }
            for a in agents_list
        }

        return render_template("designer.html",
            agents_json=json.dumps(agents_list),
            templates_json=json.dumps(templates),
            active_page="designer",
            auto_refresh=False,
        )

    @app.route("/commands", methods=["GET", "POST"])
    def commands():
        result = ""
        if request.method == "POST":
            cmd = request.form.get("command", "")
            try:
                import subprocess
                r = subprocess.run(
                    [".venv/bin/python", "-m", "multiagent.pm_cli"] + cmd.split(),
                    capture_output=True, text=True, timeout=30,
                    cwd=str(db_path.parent)
                )
                result = r.stdout + r.stderr
            except Exception as e:
                result = f"Error: {e}"

        return render_template("commands.html",
            result=result,
            active_page="commands",
            auto_refresh=False,
        )

    # ── API Routes ──

    @app.route("/api/state")
    def api_state():
        db = StateDB(db_path)
        db.connect()
        try:
            pending = db.get_pending_tasks()
            escalated = db.get_escalated_tasks()
            row = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'running'"
            ).fetchone()
            return jsonify({
                "pending": len(pending),
                "running": row[0] if row else 0,
                "escalated": len(escalated),
            })
        finally:
            db.close()

    @app.route("/api/status")
    def api_status():
        db = StateDB(db_path)
        try:
            db.connect()
        except Exception:
            return jsonify({"pending": 0, "running": 0, "completed": 0,
                           "failed": 0, "escalated": 0})
        try:
            pending = len(db.get_pending_tasks())
            row = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'running'"
            ).fetchone()
            running = row[0] if row else 0
            comp = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'completed'"
            ).fetchone()
            completed = comp[0] if comp else 0
            fail = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'failed'"
            ).fetchone()
            failed = fail[0] if fail else 0
            escalated = len(db.get_escalated_tasks())
            return jsonify({
                "pending": pending, "running": running,
                "completed": completed, "failed": failed,
                "escalated": escalated,
            })
        except Exception:
            return jsonify({"pending": 0, "running": 0, "completed": 0,
                           "failed": 0, "escalated": 0})
        finally:
            db.close()

    @app.route("/api/timeseries")
    def api_timeseries():
        db = StateDB(db_path)
        db.connect()
        try:
            token_rows = db.conn.execute(
                """SELECT date(recorded_at) as day,
                          SUM(input_tokens + output_tokens) as tokens,
                          SUM(cost_usd) as cost, COUNT(*) as calls
                   FROM agent_metrics
                   WHERE recorded_at IS NOT NULL
                     AND date(recorded_at) >= date('now', '-7 days')
                   GROUP BY date(recorded_at) ORDER BY day ASC"""
            ).fetchall()
            task_rows = db.conn.execute(
                """SELECT date(completed_at) as day,
                          COUNT(*) as total,
                          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as passed
                   FROM tasks
                   WHERE completed_at IS NOT NULL
                     AND date(completed_at) >= date('now', '-7 days')
                   GROUP BY date(completed_at) ORDER BY day ASC"""
            ).fetchall()
            return jsonify({
                "token_trend": [
                    {"date": r[0], "tokens": r[1] or 0,
                     "cost": round(r[2] or 0, 4), "calls": r[3] or 0}
                    for r in token_rows
                ],
                "pass_rate": [
                    {"date": r[0], "total": r[1] or 0, "passed": r[2] or 0,
                     "rate": round((r[2] or 0) * 100 / max(r[1] or 1, 1), 1)}
                    for r in task_rows
                ],
            })
        finally:
            db.close()

    @app.route("/api/workflow-dag")
    def api_workflow_dag():
        db = StateDB(db_path)
        db.connect()
        try:
            from .config.loader import find_workflow_yaml
            wf_path = find_workflow_yaml()
            if not wf_path or not wf_path.exists():
                return jsonify({"nodes": [], "edges": [], "error": "No workflow found"})

            from .engine import load_yaml
            wf_def = load_yaml(wf_path)
            steps = wf_def.get("workflow", {}).get("steps", [])

            nodes = []
            edges = []
            for step in steps:
                sid = step["id"]
                agent = step.get("agent", "?")
                nodes.append({"id": sid, "agent": agent, "status": "pending"})
                deps = step.get("depends_on", [])
                if isinstance(deps, str):
                    deps = [deps]
                for dep in deps:
                    edges.append({"source": dep, "target": sid, "label": ""})

            running_task = db.conn.execute(
                "SELECT id FROM tasks WHERE status='running' LIMIT 1"
            ).fetchone()
            if running_task:
                task_id = running_task[0]
                step_rows = db.conn.execute(
                    "SELECT DISTINCT step_id, status FROM step_results "
                    "WHERE task_id=? ORDER BY id", (task_id,)
                ).fetchall()
                seen = set()
                for r in step_rows:
                    if r[0] not in seen:
                        seen.add(r[0])
                        for node in nodes:
                            if node["id"] == r[0]:
                                node["status"] = r[1]

            return jsonify({
                "nodes": nodes, "edges": edges,
                "workflow_id": wf.get("id", "unknown"),
            })
        except Exception as e:
            return jsonify({"nodes": [], "edges": [], "error": str(e)})
        finally:
            db.close()

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "version": "0.7.0"}), 200

    # ── Graph API ──

    @app.route("/api/graph/export", methods=["POST"])
    def api_graph_export():
        from .core.graph_engine import WorkflowGraph
        try:
            data = request.get_json(force=True)
            g = WorkflowGraph.from_json(data)
            yaml_str = g.to_workflow_yaml()
            return jsonify({"yaml": yaml_str, "status": "ok"})
        except Exception as e:
            return jsonify({"error": str(e), "status": "error"}), 400

    @app.route("/api/graph/from-agents", methods=["POST"])
    def api_graph_from_agents():
        from .core.graph_engine import WorkflowGraph
        try:
            data = request.get_json(force=True)
            agents = data.get("agents", [])
            g = WorkflowGraph.from_registry(agents)
            return jsonify(g.to_json())
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    return app
