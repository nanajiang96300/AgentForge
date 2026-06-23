"""
Web Dashboard — Phase 5 实时管线仪表盘。

Flask 轻量 Web 服务，读取 state.db 显示任务队列、进度条、Token 统计。

用法:
    multiagent dashboard              # 启动仪表盘
    multiagent dashboard --port 5001  # 指定端口
"""

import json
from pathlib import Path

from flask import Flask, jsonify

from .db import StateDB
from .conductor import _calculate_task_progress


def _find_db():
    cwd = Path.cwd()
    for p in [cwd / "state.db", cwd / ".framework" / "workflow" / "state.db"]:
        if p.exists():
            return p
    return cwd / "state.db"


def create_dashboard_app(db_path: Path = None) -> Flask:
    if db_path is None:
        db_path = _find_db()

    app = Flask(__name__)

    _CSS = """
    <style>
      :root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9;
              --muted: #8b949e; --green: #3fb950; --red: #f85149; --blue: #58a6ff;
              --orange: #d2991d; --purple: #a371f7; }
      * { box-sizing: border-box; margin: 0; padding: 0; }
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
             background: var(--bg); color: var(--text); line-height: 1.5; padding: 20px; }
      .container { max-width: 1100px; margin: 0 auto; }
      header { margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
      header h1 { font-size: 1.6em; color: var(--purple); }
      .sub { color: var(--muted); font-size: 0.9em; margin-top: 4px; }
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
      .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
      .stat-card .label { color: var(--muted); font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.5px; }
      .stat-card .value { font-size: 2em; font-weight: 700; margin-top: 4px; }
      .green { color: var(--green); } .blue { color: var(--blue); }
      .red { color: var(--red); } .orange { color: var(--orange); }
      .section { margin-bottom: 24px; }
      .section h2 { font-size: 1.2em; margin-bottom: 12px; color: var(--blue); }
      table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
      th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }
      th { background: #1c2129; color: var(--muted); font-weight: 600; font-size: 0.85em; text-transform: uppercase; }
      td { font-size: 0.95em; }
      tr:hover td { background: #1c2129; }
      .progress-bar { display: flex; align-items: center; gap: 8px; min-width: 200px; }
      .progress-bar .bar { flex: 1; height: 8px; background: #21262d; border-radius: 4px; overflow: hidden; }
      .progress-bar .fill { height: 100%; border-radius: 4px; transition: width 1s; }
      .fill.pm { background: var(--blue); } .fill.dev { background: var(--orange); }
      .fill.test { background: var(--purple); } .fill.done { background: var(--green); }
      .progress-bar .pct { font-size: 0.85em; color: var(--muted); min-width: 75px; text-align: right; }
      .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; font-weight: 600; }
      .badge.pending { background: #1c2129; color: var(--muted); }
      .badge.running, .badge.assigned { background: #1a2d3d; color: var(--blue); }
      .badge.completed { background: #1a2d1a; color: var(--green); }
      .badge.failed { background: #3d1a1a; color: var(--red); }
      .badge.escalated { background: #3d2a1a; color: var(--orange); }
      .pipeline { display: flex; gap: 4px; align-items: center; white-space: nowrap; }
      .pipeline .step { padding: 4px 10px; border-radius: 4px; font-size: 0.8em; }
      .pipeline .step.done { background: #1a2d1a; color: var(--green); }
      .pipeline .step.active { background: #1a2d3d; color: var(--blue); }
      .pipeline .step.pending { background: #1c2129; color: var(--muted); }
      .pipeline .arrow { color: var(--muted); margin: 0 2px; }
      .empty { text-align: center; padding: 40px; color: var(--muted); }
      .refresh { color: var(--muted); font-size: 0.8em; text-align: right; margin-bottom: 12px; }
      footer { text-align: center; color: var(--muted); font-size: 0.8em; margin-top: 40px; padding: 20px; border-top: 1px solid var(--border); }
    </style>
    """

    def _render_page(title, body):
        return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="15">
<title>{title} — AgentForge</title>{_CSS}
</head><body><div class="container">
<header><h1>🤖 AgentForge Dashboard</h1>
<div class="sub">Pipeline Monitoring • Auto-refresh 15s</div></header>
<div class="refresh" id="clock"></div>
<script>document.getElementById('clock').textContent='Updated: '+new Date().toLocaleTimeString()</script>
{body}
<footer>AgentForge v0.6.0-dev • Multi-Agent Framework</footer>
</div></body></html>"""

    def _pipeline_html(db, task_id):
        steps_order = [("pm_analyze", "PM"), ("dev_fix", "Dev"), ("test_verify", "Test")]
        step_status = {}
        rows = db.conn.execute(
            "SELECT step_id, status FROM step_results WHERE task_id = ? ORDER BY id",
            (task_id,)
        ).fetchall()
        for r in rows:
            step_status[r[0]] = r[1]
        parts = ['<div class="pipeline">']
        for i, (sid, label) in enumerate(steps_order):
            if i > 0:
                parts.append('<span class="arrow">→</span>')
            s = step_status.get(sid, "pending")
            cls = "done" if s == "completed" else ("active" if s == "running" else "pending")
            parts.append(f'<span class="step {cls}">{label}</span>')
        parts.append('</div>')
        return ''.join(parts)

    def _badge(status):
        return f'<span class="badge {status}">{status}</span>'

    def _progress_html(db, task_id):
        p = _calculate_task_progress(db, task_id)
        pct = p["pct"]
        stage = p["stage"]
        cls = {"pm": "pm", "pm_done": "pm", "dev": "dev",
               "dev_done": "dev", "test": "test"}.get(stage, "done")
        subtasks = ""
        if p.get("total_subtasks", 0) > 0:
            subtasks = f' ({p["completed_subtasks"]}/{p["total_subtasks"]})'
        return (f'<div class="progress-bar">'
                f'<div class="bar"><div class="fill {cls}" style="width:{pct}%"></div></div>'
                f'<div class="pct">{pct}%{subtasks}</div></div>')

    def _token_fmt(n):
        if n >= 1_000_000: return f"{n/1e6:.1f}M"
        if n >= 1000: return f"{n/1000:.0f}K"
        return str(n)

    # ── Routes ──

    @app.route("/")
    def index():
        db = StateDB(db_path)
        db.connect()
        try:
            pending = db.get_pending_tasks()
            escalated = db.get_escalated_tasks()
            row = db.conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()
            running_count = row[0] if row else 0

            metrics = db.conn.execute(
                "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) FROM agent_metrics"
            ).fetchone()

            tasks_rows = db.conn.execute(
                "SELECT id, type, status, current_step, retry_count, rejection_count, created_at "
                "FROM tasks ORDER BY created_at DESC LIMIT 30"
            ).fetchall()
            cols = ["id", "type", "status", "current_step", "retry_count", "rejection_count", "created_at"]
            tasks = [dict(zip(cols, r)) for r in tasks_rows]

            in_flight = []
            for t in tasks:
                if t["status"] in ("running", "assigned"):
                    m = db.conn.execute(
                        "SELECT SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) "
                        "FROM agent_metrics WHERE task_id = ?", (t["id"],)
                    ).fetchone()
                    t["tokens_in"] = m[0] or 0
                    t["tokens_out"] = m[1] or 0
                    t["cost"] = m[2] or 0
                    in_flight.append(t)

            completed = [t for t in tasks if t["status"] == "completed"][:10]
            failed = [t for t in tasks if t["status"] in ("failed", "escalated")][:10]

            # Build HTML
            body = []

            # Stat cards
            body.append('<div class="grid">')
            body.append(f'<div class="stat-card"><div class="label">Pending</div><div class="value blue">{len(pending)}</div></div>')
            body.append(f'<div class="stat-card"><div class="label">Running</div><div class="value orange">{running_count}</div></div>')
            body.append(f'<div class="stat-card"><div class="label">Completed</div><div class="value green">{len(completed)}</div></div>')
            body.append(f'<div class="stat-card"><div class="label">Escalated</div><div class="value red">{len(escalated)}</div></div>')
            body.append('</div>')

            # Token summary
            if metrics and metrics[0]:
                body.append('<div class="grid">')
                body.append(f'<div class="stat-card"><div class="label">Total Calls</div><div class="value">{metrics[0]}</div></div>')
                body.append(f'<div class="stat-card"><div class="label">Input Tokens</div><div class="value">{_token_fmt(metrics[1] or 0)}</div></div>')
                body.append(f'<div class="stat-card"><div class="label">Output Tokens</div><div class="value">{_token_fmt(metrics[2] or 0)}</div></div>')
                body.append(f'<div class="stat-card"><div class="label">Total Cost</div><div class="value green">\${metrics[3] or 0:.2f}</div></div>')
                body.append('</div>')

            # In-flight with progress
            if in_flight:
                body.append('<div class="section"><h2>🔄 In-Flight Tasks</h2><table>')
                body.append('<tr><th>Task</th><th>Pipeline</th><th>Progress</th><th>Tokens</th><th>Cost</th></tr>')
                for t in in_flight:
                    body.append('<tr>'
                        f'<td><code>{t["id"][:14]}</code></td>'
                        f'<td>{_pipeline_html(db, t["id"])}</td>'
                        f'<td>{_progress_html(db, t["id"])}</td>'
                        f'<td>{_token_fmt(t["tokens_in"])} / {_token_fmt(t["tokens_out"])}</td>'
                        f'<td>\${t["cost"]:.4f}</td>'
                        '</tr>')
                body.append('</table></div>')

            # All tasks
            body.append('<div class="section"><h2>📋 All Tasks</h2><table>')
            body.append('<tr><th>ID</th><th>Type</th><th>Status</th><th>Pipeline</th><th>Created</th></tr>')
            for t in tasks[:20]:
                body.append('<tr>'
                    f'<td><code>{t["id"][:14]}</code></td>'
                    f'<td>{t["type"]}</td>'
                    f'<td>{_badge(t["status"])}</td>'
                    f'<td>{_pipeline_html(db, t["id"])}</td>'
                    f'<td>{t["created_at"][:19] if t["created_at"] else "-"}</td>'
                    '</tr>')
            body.append('</table></div>')

            return _render_page("Dashboard", '\n'.join(body))
        finally:
            db.close()

    @app.route("/api/state")
    def api_state():
        db = StateDB(db_path)
        db.connect()
        try:
            pending = db.get_pending_tasks()
            escalated = db.get_escalated_tasks()
            row = db.conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()
            return jsonify({
                "pending": len(pending),
                "running": row[0] if row else 0,
                "escalated": len(escalated),
            })
        finally:
            db.close()

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "version": "0.3.0"}), 200

    return app
