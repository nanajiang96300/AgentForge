"""
Web Dashboard — Phase 5 实时管线仪表盘。

Flask 轻量 Web 服务，读取 state.db 显示任务队列、进度条、Token 统计。

用法:
    multiagent dashboard              # 启动仪表盘
    multiagent dashboard --port 5001  # 指定端口
"""

import json
from pathlib import Path

from flask import Flask, jsonify, request

from .db import StateDB
from .core.progress import calculate_task_progress, progress_bar
from .config.loader import find_state_db


def create_dashboard_app(db_path: Path = None) -> Flask:
    if db_path is None:
        db_path = find_state_db()

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

    def _render_page(title, body, auto_refresh=True):
        refresh_tag = '<meta http-equiv="refresh" content="15">' if auto_refresh else ''
        return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
{refresh_tag}
<title>{title} — AgentForge</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
{_CSS}
</head><body><div class="container">
<header><h1>🤖 AgentForge Dashboard</h1>
<div class="sub">Pipeline Monitoring • <a href="/designer" style="color:var(--purple);">Designer</a> • <a href="/commands" style="color:var(--purple);">Commands</a></div></header>
<div class="refresh" id="clock"></div>
<script>document.getElementById('clock').textContent='Updated: '+new Date().toLocaleTimeString()</script>
{body}
<footer>AgentForge v0.7.0 • Multi-Agent Framework</footer>
</div></body></html>"""

    def _pipeline_html(db, task_id):
        """Dynamic pipeline visualization — reads steps from DB, no hardcoded names."""
        rows = db.conn.execute(
            "SELECT DISTINCT step_id, status FROM step_results "
            "WHERE task_id = ? ORDER BY id",
            (task_id,)
        ).fetchall()
        # Build unique step list preserving order
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
            cls = "done" if status == "completed" else ("active" if status == "running" else "pending")
            parts.append(f'<span class="step {cls}">{label}</span>')
        parts.append('</div>')
        return ''.join(parts)

    def _badge(status):
        return f'<span class="badge {status}">{status}</span>'

    def _progress_html(db, task_id):
        p = calculate_task_progress(db, task_id)
        pct = p["pct"]
        stage = p["stage"]
        # Map stage to CSS class — generic, no hardcoded step names
        cls = "done" if stage == "done" else (
            "test" if "test" in stage else (
            "dev" if "dev" in stage else "pm"))
        subtasks = ""
        if p.get("subtasks_total", 0) > 0:
            subtasks = f' ({p.get("subtasks_done", 0)}/{p["subtasks_total"]})'
        elif p.get("completed_steps", 0) > 0:
            subtasks = f' ({p["completed_steps"]}/{p["total_steps"]} steps)'
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

            # ── Charts Section (Fix 3A) ──
            body.append('<div class="section"><h2>📈 7-Day Trends</h2>')
            body.append('<div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">')
            body.append('<div style="background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px;">')
            body.append('<h3 style="font-size:0.9em;color:var(--muted);margin-bottom:8px;">Token Usage</h3>')
            body.append('<canvas id="tokenChart" height="200"></canvas></div>')
            body.append('<div style="background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px;">')
            body.append('<h3 style="font-size:0.9em;color:var(--muted);margin-bottom:8px;">Task Pass Rate</h3>')
            body.append('<canvas id="passRateChart" height="200"></canvas></div>')
            body.append('</div></div>')

            # ── Workflow DAG Section (Fix 3C) ──
            body.append('<div class="section"><h2>🔀 Workflow DAG</h2>')
            body.append('<div id="mermaid-container" style="background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;overflow:auto;min-height:120px;text-align:center;color:var(--muted);">')
            body.append('Loading workflow graph...</div></div>')

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
            body.append('<div class="section"><h2>📋 All Tasks</h2>')
            body.append('<div style="display:flex; gap:8px; margin-bottom:12px; align-items:center;">'
                '<input type="text" id="searchInput" placeholder="Search task ID..." '
                'style="flex:1;padding:8px;background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:4px;" autocomplete="off">'
                '<select id="statusFilter" style="padding:8px;background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:4px;">'
                '<option value="">All Statuses</option>'
                '<option value="pending">Pending</option>'
                '<option value="running">Running</option>'
                '<option value="assigned">Assigned</option>'
                '<option value="completed">Completed</option>'
                '<option value="failed">Failed</option>'
                '<option value="escalated">Escalated</option>'
                '</select></div>')
            body.append('<table id="taskTable"><tr><th>ID</th><th>Type</th><th>Status</th><th>Pipeline</th><th>Created</th></tr>')
            for t in tasks[:50]:
                body.append('<tr class="task-row" data-status="' + t["status"] + '" data-id="' + t["id"] + '">'
                    f'<td><code>{t["id"][:14]}</code></td>'
                    f'<td>{t["type"]}</td>'
                    f'<td>{_badge(t["status"])}</td>'
                    f'<td>{_pipeline_html(db, t["id"])}</td>'
                    f'<td>{t["created_at"][:19] if t["created_at"] else "-"}</td>'
                    '</tr>')
            body.append('</table></div>')

            # ── Charts + Search/Filter + DAG JavaScript ──
            body.append('<script>'
                '// -- Charts (Fix 3A)'
                'fetch("/api/timeseries").then(function(r){return r.json()}).then(function(d){'
                'if(d.token_trend&&d.token_trend.length){'
                'new Chart(document.getElementById("tokenChart"),{'
                'type:"bar",data:{labels:d.token_trend.map(function(x){return x.date}),'
                'datasets:[{label:"Tokens",data:d.token_trend.map(function(x){return x.tokens}),'
                'backgroundColor:"#58a6ff",borderColor:"#58a6ff"}]},'
                'options:{responsive:true,maintainAspectRatio:false,'
                'plugins:{legend:{labels:{color:"#8b949e"}}},'
                'scales:{x:{ticks:{color:"#8b949e"}},y:{ticks:{color:"#8b949e",'
                'callback:function(v){return v>=1e6?(v/1e6)+"M":v>=1e3?(v/1e3)+"K":v}}}}});'
                'new Chart(document.getElementById("passRateChart"),{'
                'type:"line",data:{labels:d.pass_rate.map(function(x){return x.date}),'
                'datasets:[{label:"Pass Rate %",data:d.pass_rate.map(function(x){return x.rate}),'
                'borderColor:"#3fb950",backgroundColor:"rgba(63,185,80,0.1)",fill:true,tension:0.3}]},'
                'options:{responsive:true,maintainAspectRatio:false,'
                'plugins:{legend:{labels:{color:"#8b949e"}}},'
                'scales:{x:{ticks:{color:"#8b949e"}},y:{min:0,max:100,ticks:{color:"#8b949e"}}}}});'
                '}});'
                '// -- Search/Filter (Fix 3B)'
                '(function(){'
                'var search=document.getElementById("searchInput");'
                'var status=document.getElementById("statusFilter");'
                'if(!search||!status)return;'
                'function filter(){'
                'var q=search.value.toLowerCase();'
                'var s=status.value;'
                'document.querySelectorAll(".task-row").forEach(function(row){'
                'var mid=row.getAttribute("data-id").toLowerCase().includes(q);'
                'var ms=!s||row.getAttribute("data-status")===s;'
                'row.style.display=(mid&&ms)?"":"none";});}'
                'search.addEventListener("input",filter);'
                'status.addEventListener("change",filter);})();'
                '// -- Workflow DAG (Fix 3C)'
                'fetch("/api/workflow-dag").then(function(r){return r.json()}).then(function(d){'
                'if(d.error||!d.nodes.length){'
                'document.getElementById("mermaid-container").innerHTML='
                '"<span style=\\"color:var(--muted)\\">No workflow DAG available</span>";return;}'
                'var graph=["graph LR"];'
                'd.nodes.forEach(function(n){'
                'var label=n.agent+": "+n.id;'
                'var cls=n.status==="completed"?"done":n.status==="running"?"active":"pending";'
                'graph.push(n.id+"(\\""+label+"\\"):::"+cls);});'
                'd.edges.forEach(function(e){graph.push(e.source+"-->"+e.target);});'
                'var mc=document.getElementById("mermaid-container");'
                'mc.innerHTML="<div class=\\"mermaid\\">"+graph.join("\\n")+"</div>";'
                'mermaid.run({nodes:[mc.querySelector(".mermaid")]});'
                '}).catch(function(e){'
                'document.getElementById("mermaid-container").innerHTML='
                '"<span style=\\"color:var(--muted)\\">Workflow DAG unavailable</span>";});'
                '</script>')

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

    @app.route("/api/status")
    def api_status():
        """Return full queue status with all five task state counts."""
        db = StateDB(db_path)
        try:
            db.connect()
        except Exception:
            return jsonify({"pending": 0, "running": 0, "completed": 0,
                           "failed": 0, "escalated": 0})
        try:
            pending = len(db.get_pending_tasks())
            row = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()
            running = row[0] if row else 0
            comp = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'completed'").fetchone()
            completed = comp[0] if comp else 0
            fail = db.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'failed'").fetchone()
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
        """Return time-series data for charts: daily token usage and task pass rate."""
        db = StateDB(db_path)
        db.connect()
        try:
            token_rows = db.conn.execute(
                """SELECT date(recorded_at) as day,
                          SUM(input_tokens + output_tokens) as tokens,
                          SUM(cost_usd) as cost,
                          COUNT(*) as calls
                   FROM agent_metrics
                   WHERE recorded_at IS NOT NULL
                     AND date(recorded_at) >= date('now', '-7 days')
                   GROUP BY date(recorded_at)
                   ORDER BY day ASC"""
            ).fetchall()
            task_rows = db.conn.execute(
                """SELECT date(completed_at) as day,
                          COUNT(*) as total,
                          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as passed
                   FROM tasks
                   WHERE completed_at IS NOT NULL
                     AND date(completed_at) >= date('now', '-7 days')
                   GROUP BY date(completed_at)
                   ORDER BY day ASC"""
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
        """Return DAG structure for Mermaid visualization."""
        db = StateDB(db_path)
        db.connect()
        try:
            from .config.loader import find_workflow_yaml
            wf_path = find_workflow_yaml()
            if not wf_path or not wf_path.exists():
                return jsonify({"nodes": [], "edges": [], "error": "No workflow found"})

            from .engine import load_yaml
            wf_def = load_yaml(wf_path)
            wf = wf_def.get("workflow", {})
            steps = wf.get("steps", [])

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

            # Color nodes based on running task's step statuses
            running_task = db.conn.execute(
                "SELECT id FROM tasks WHERE status='running' LIMIT 1"
            ).fetchone()
            if running_task:
                task_id = running_task[0]
                step_rows = db.conn.execute(
                    "SELECT DISTINCT step_id, status FROM step_results "
                    "WHERE task_id=? ORDER BY id",
                    (task_id,)
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

    # ── B2: Visual Workflow Designer ──

    @app.route("/designer")
    def designer():
        from .runtime.registry import AgentRegistry
        agents_list = AgentRegistry.list_all()
        agents_json = json.dumps([{
            "name": a.name, "timeout": a.timeout,
            "description": a.description[:60]
        } for a in agents_list])

        body = f"""<div class="section"><h2>🎨 Workflow Designer</h2>
<div style="display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap;" id="agent-btns"></div>
<div style="display:grid; grid-template-columns: 1fr 280px; gap: 12px;">
<div style="background:var(--card); border:1px solid var(--border); border-radius:8px; min-height:400px;">
<svg id="canvas" width="100%" height="420" style="display:block;">
<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#58a6ff"/></marker></defs>
</svg></div>
<div>
<div style="background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px; margin-bottom:12px;">
<h3 style="margin-bottom:8px;font-size:0.9em;">🔗 Connect</h3>
<select id="efrom" style="width:100%;padding:6px;margin-bottom:4px;background:#0d1117;color:var(--text);border:1px solid var(--border);border-radius:4px;"><option>— From —</option></select>
<select id="eto" style="width:100%;padding:6px;margin-bottom:8px;background:#0d1117;color:var(--text);border:1px solid var(--border);border-radius:4px;"><option>— To —</option></select>
<button onclick="addEdge(document.getElementById('efrom').value,document.getElementById('eto').value)" style="width:100%;padding:8px;background:var(--blue);color:#fff;border:none;border-radius:4px;cursor:pointer;">Connect</button>
</div>
<button onclick="exportYAML()" style="width:100%;padding:10px;background:var(--green);color:#fff;border:none;border-radius:4px;cursor:pointer;margin-bottom:8px;">📋 Export YAML</button>
<pre id="yaml-out" style="background:#0d1117;color:var(--green);padding:10px;border-radius:4px;font-size:0.7em;max-height:180px;overflow:auto;"></pre>
</div></div></div>
<script>
const AGENTS = {agents_json};
let nodes=[],edges=[];
if(loadState()){{draw();setTimeout(function(){{upSel&&upSel();setInterval(upSel,800);}},200);}}

function addNode(name){{var a=AGENTS.find(x=>x.name===name);var n={{id:'n'+Date.now()+'_'+Math.random().toString(36).substr(2,5),label:name.toUpperCase(),agent:name,x:80+nodes.length*20,y:80+nodes.length*25,timeout:a?a.timeout:300}};nodes.push(n);draw();saveState();}}

function addEdge(f,t){{if(f&&t&&f!==t&&f!=='—'&&t!=='—'){{edges.push({{from:f,to:t}});draw();saveState();}}}}

function removeNode(id){{nodes=nodes.filter(n=>n.id!==id);edges=edges.filter(e=>e.from!==id&&e.to!==id);draw();saveState();}}

function saveState(){{try{{localStorage.setItem('agentforge-designer',JSON.stringify({{nodes:nodes,edges:edges}}));}}catch(e){{}}}}
function loadState(){{try{{var d=JSON.parse(localStorage.getItem('agentforge-designer'));if(d&&d.nodes){{nodes=d.nodes;edges=d.edges||[];return true;}}}}catch(e){{}}return false;}}

function draw(){{
var s=document.getElementById('canvas');s.innerHTML='';
var defs=document.createElementNS('http://www.w3.org/2000/svg','defs');
defs.innerHTML='<marker id=\"arrow\" viewBox=\"0 0 10 10\" refX=\"10\" refY=\"5\" markerWidth=\"6\" markerHeight=\"6\" orient=\"auto\"><path d=\"M0,0 L10,5 L0,10 z\" fill=\"#58a6ff\"/></marker>';
s.appendChild(defs);
edges.forEach(e=>{{var f=nodes.find(n=>n.id===e.from),t=nodes.find(n=>n.id===e.to);if(f&&t){{var l=document.createElementNS('http://www.w3.org/2000/svg','line');l.setAttribute('x1',f.x+60);l.setAttribute('y1',f.y+25);l.setAttribute('x2',t.x+60);l.setAttribute('y2',t.y+25);l.setAttribute('stroke','#58a6ff');l.setAttribute('stroke-width','2');l.setAttribute('marker-end','url(#arrow)');s.appendChild(l);}}}});
nodes.forEach(n=>{{var g=document.createElementNS('http://www.w3.org/2000/svg','g');g.setAttribute('transform','translate('+n.x+','+n.y+')');g.setAttribute('data-node-id',n.id);g.style.cursor='move';var r=document.createElementNS('http://www.w3.org/2000/svg','rect');r.setAttribute('width','120');r.setAttribute('height','50');r.setAttribute('rx','8');r.setAttribute('fill','#161b22');r.setAttribute('stroke','#30363d');r.setAttribute('stroke-width','2');g.appendChild(r);var t=document.createElementNS('http://www.w3.org/2000/svg','text');t.setAttribute('x','60');t.setAttribute('y','20');t.setAttribute('text-anchor','middle');t.setAttribute('fill','#c9d1d9');t.setAttribute('font-size','13');t.setAttribute('font-weight','600');t.textContent=n.label;g.appendChild(t);var u=document.createElementNS('http://www.w3.org/2000/svg','text');u.setAttribute('x','60');u.setAttribute('y','38');u.setAttribute('text-anchor','middle');u.setAttribute('fill','#8b949e');u.setAttribute('font-size','11');u.textContent=n.timeout+'s';g.appendChild(u);var drag=null,ox,oy;g.onmousedown=function(ev){{drag=n;ox=ev.clientX-n.x;oy=ev.clientY-n.y;}};g.ondblclick=function(){{removeNode(n.id);}};s.appendChild(g);}});
var upSel=function(){{
['efrom','eto'].forEach(id=>{{var sel=document.getElementById(id),v=sel.value;sel.innerHTML='<option>—</option>';nodes.forEach(n=>sel.innerHTML+='<option value=\"'+n.id+'\">'+n.label+'</option>');if(v)sel.value=v;}});}};
document.onmousemove=function(e){{if(window._dragNode){{window._dragNode.x=e.clientX-window._dragOffX;window._dragNode.y=e.clientY-window._dragOffY;draw();}}}};
document.onmouseup=function(){{window._dragNode=null;}};
// Re-bind drag vars at global scope (use data-node-id for correct node targeting)
nodes.forEach(n=>{{var orig=n;var gg=document.querySelector('g[data-node-id=\"'+orig.id+'\"]');if(gg){{gg.onmousedown=function(ev){{window._dragNode=orig;window._dragOffX=ev.clientX-orig.x;window._dragOffY=ev.clientY-orig.y;}};}}}});
setInterval(upSel,800);
}}

function exportYAML(){{
var steps=nodes.map(n=>{{var deps=edges.filter(e=>e.to===n.id).map(e=>{{var f=nodes.find(nn=>nn.id===e.from);return f?f.agent+'_step':'';}}).filter(Boolean);return{{id:n.agent+'_step',agent:n.agent,description:n.agent+' step',timeout:n.timeout,depends_on:deps.length?deps:undefined}};}});
var y=['workflow:','  id: custom','  steps:'];
steps.forEach(s=>{{y.push('    - id: '+s.id);y.push('      agent: '+s.agent);y.push('      timeout: '+s.timeout);if(s.depends_on)y.push('      depends_on: '+JSON.stringify(s.depends_on));}});
document.getElementById('yaml-out').textContent=y.join('\\n');
}}

var btns=document.getElementById('agent-btns');
AGENTS.forEach(a=>{{var b=document.createElement('button');b.textContent='+'+a.name.toUpperCase();b.title=a.description;b.onclick=function(){{addNode(a.name);}};b.style.cssText='padding:8px 14px;background:var(--purple);color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;';btns.appendChild(b);}});
</script>"""
        return _render_page("Designer", body, auto_refresh=False)

    # ── B3: Web Command Center ──

    # ── B5: Graph API ──

    @app.route("/api/graph/export", methods=["POST"])
    def api_graph_export():
        """Export graph JSON to workflow YAML."""
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
        """Create linear graph from agent names list."""
        from .core.graph_engine import WorkflowGraph
        try:
            data = request.get_json(force=True)
            agents = data.get("agents", [])
            g = WorkflowGraph.from_registry(agents)
            return jsonify(g.to_json())
        except Exception as e:
            return jsonify({"error": str(e)}), 400

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

        body = f"""<div class="section"><h2>⚡ Command Center</h2>
<div class="grid">
<div class="stat-card" style="cursor:pointer;" onclick="runCmd('pm list')"><div class="label">📋 List Tasks</div></div>
<div class="stat-card" style="cursor:pointer;" onclick="runCmd('conductor status')"><div class="label">📊 Status</div></div>
<div class="stat-card" style="cursor:pointer;" onclick="runCmd('metrics')"><div class="label">💰 Costs</div></div>
<div class="stat-card" style="cursor:pointer;" onclick="runCmd('agent list')"><div class="label">🤖 Agents</div></div>
</div>
<form method="post" style="margin-top:16px;">
<input name="command" id="cmd-input" placeholder="e.g. conductor status" style="width:100%;padding:10px;background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:4px;font-size:1em;margin-bottom:8px;">
<button type="submit" style="padding:10px 24px;background:var(--green);color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;">Run</button>
</form>
<pre id="cmd-result" style="background:#0d1117;color:var(--green);padding:12px;border-radius:4px;margin-top:12px;max-height:400px;overflow:auto;">{result}</pre>
<script>function runCmd(c){{document.getElementById('cmd-input').value=c;document.forms[0].submit();}}</script></div>"""
        return _render_page("Commands", body, auto_refresh=False)

    return app
