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

    def _render_page(title, body):
        return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="15">
<title>{title} — AgentForge</title>{_CSS}
</head><body><div class="container">
<header><h1>🤖 AgentForge Dashboard</h1>
<div class="sub">Pipeline Monitoring • <a href="/designer" style="color:var(--purple);">Designer</a> • <a href="/commands" style="color:var(--purple);">Commands</a></div></header>
<div class="refresh" id="clock"></div>
<script>document.getElementById('clock').textContent='Updated: '+new Date().toLocaleTimeString()</script>
{body}
<footer>AgentForge v0.6.0-dev • Multi-Agent Framework</footer>
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
        return jsonify({"status": "ok", "version": "0.6.0-dev"}), 200

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

function addNode(name){{var a=AGENTS.find(x=>x.name===name);var n={{id:'n'+Date.now(),label:name.toUpperCase(),agent:name,x:80+nodes.length*20,y:80+nodes.length*25,timeout:a?a.timeout:300}};nodes.push(n);draw();}}

function addEdge(f,t){{if(f&&t&&f!==t&&f!=='—'&&t!=='—'){{edges.push({{from:f,to:t}});draw();}}}}

function removeNode(id){{nodes=nodes.filter(n=>n.id!==id);edges=edges.filter(e=>e.from!==id&&e.to!==id);draw();}}

function draw(){{
var s=document.getElementById('canvas');s.innerHTML='';
var defs=document.createElementNS('http://www.w3.org/2000/svg','defs');
defs.innerHTML='<marker id=\"arrow\" viewBox=\"0 0 10 10\" refX=\"10\" refY=\"5\" markerWidth=\"6\" markerHeight=\"6\" orient=\"auto\"><path d=\"M0,0 L10,5 L0,10 z\" fill=\"#58a6ff\"/></marker>';
s.appendChild(defs);
edges.forEach(e=>{{var f=nodes.find(n=>n.id===e.from),t=nodes.find(n=>n.id===e.to);if(f&&t){{var l=document.createElementNS('http://www.w3.org/2000/svg','line');l.setAttribute('x1',f.x+60);l.setAttribute('y1',f.y+25);l.setAttribute('x2',t.x+60);l.setAttribute('y2',t.y+25);l.setAttribute('stroke','#58a6ff');l.setAttribute('stroke-width','2');l.setAttribute('marker-end','url(#arrow)');s.appendChild(l);}}}});
nodes.forEach(n=>{{var g=document.createElementNS('http://www.w3.org/2000/svg','g');g.setAttribute('transform','translate('+n.x+','+n.y+')');g.style.cursor='move';var r=document.createElementNS('http://www.w3.org/2000/svg','rect');r.setAttribute('width','120');r.setAttribute('height','50');r.setAttribute('rx','8');r.setAttribute('fill','#161b22');r.setAttribute('stroke','#30363d');r.setAttribute('stroke-width','2');g.appendChild(r);var t=document.createElementNS('http://www.w3.org/2000/svg','text');t.setAttribute('x','60');t.setAttribute('y','20');t.setAttribute('text-anchor','middle');t.setAttribute('fill','#c9d1d9');t.setAttribute('font-size','13');t.setAttribute('font-weight','600');t.textContent=n.label;g.appendChild(t);var u=document.createElementNS('http://www.w3.org/2000/svg','text');u.setAttribute('x','60');u.setAttribute('y','38');u.setAttribute('text-anchor','middle');u.setAttribute('fill','#8b949e');u.setAttribute('font-size','11');u.textContent=n.timeout+'s';g.appendChild(u);var drag=null,ox,oy;g.onmousedown=function(ev){{drag=n;ox=ev.clientX-n.x;oy=ev.clientY-n.y;}};g.ondblclick=function(){{removeNode(n.id);}};s.appendChild(g);}});
var upSel=function(){{
['efrom','eto'].forEach(id=>{{var sel=document.getElementById(id),v=sel.value;sel.innerHTML='<option>—</option>';nodes.forEach(n=>sel.innerHTML+='<option value=\"'+n.id+'\">'+n.label+'</option>');if(v)sel.value=v;}});}};
document.onmousemove=function(e){{if(window._dragNode){{window._dragNode.x=e.clientX-window._dragOffX;window._dragNode.y=e.clientY-window._dragOffY;draw();}}}};
document.onmouseup=function(){{window._dragNode=null;}};
// Re-bind drag vars at global scope
nodes.forEach(n=>{{var orig=n;var g=document.querySelectorAll('g');g.forEach(gg=>{{if(gg.querySelector('text')&&gg.querySelector('text').textContent===orig.label){{gg.onmousedown=function(ev){{window._dragNode=orig;window._dragOffX=ev.clientX-orig.x;window._dragOffY=ev.clientY-orig.y;}};}}}});}});
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
        return _render_page("Designer", body)

    # ── B3: Web Command Center ──

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
        return _render_page("Commands", body)

    return app
