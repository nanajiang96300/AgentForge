/**
 * AgentForge Workflow Designer — SVG drag/drop DAG builder.
 */

// AGENTS and TEMPLATES are set by the HTML template before this script loads
var nodes = [];
var edges = [];
var _upSelTimer = null;

// ── State Persistence ──

function saveState() {
  try {
    localStorage.setItem('agentforge-designer',
      JSON.stringify({ nodes: nodes, edges: edges }));
  } catch(e) {}
}

function loadState() {
  try {
    var d = JSON.parse(localStorage.getItem('agentforge-designer'));
    if (d && d.nodes) {
      nodes = d.nodes;
      edges = d.edges || [];
      return true;
    }
  } catch(e) {}
  return false;
}

// ── Node / Edge Operations ──

function addNode(name) {
  var a = AGENTS.find(function(x) { return x.name === name; });
  var same = nodes.filter(function(n) { return n.agent === name; }).length;
  var suffix = same > 0 ? ' ' + (same + 1) : '';
  var n = {
    id: 'n' + Date.now() + '_' + Math.random().toString(36).substr(2, 5),
    label: name.toUpperCase() + suffix,
    agent: name,
    x: 80 + nodes.length * 20,
    y: 80 + nodes.length * 25,
    timeout: a ? a.timeout : 300
  };
  nodes.push(n);
  draw();
  saveState();
}

function addEdge(f, t, etype) {
  if (f && t && f !== t && f !== '—' && t !== '—') {
    etype = etype || 'depends_on';
    edges.push({ from: f, to: t, type: etype });
    draw();
    saveState();
  }
}

function addEdgeFromUI() {
  var f = document.getElementById('efrom').value;
  var t = document.getElementById('eto').value;
  var etype = document.getElementById('etype').value;
  addEdge(f, t, etype);
}

function removeNode(id) {
  nodes = nodes.filter(function(n) { return n.id !== id; });
  edges = edges.filter(function(e) { return e.from !== id && e.to !== id; });
  draw();
  saveState();
}

// ── SVG Drawing ──

function draw() {
  var s = document.getElementById('canvas');
  s.innerHTML = '';

  var defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  defs.innerHTML = '<marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#58a6ff"/></marker>';
  s.appendChild(defs);

  // Edge type colors
  var edgeColors = {
    'depends_on': '#58a6ff',
    'verdict_rejected': '#f85149',
    'verdict_approved': '#3fb950'
  };
  var edgeDash = {
    'depends_on': '',
    'verdict_rejected': '8,4',
    'verdict_approved': '4,2'
  };

  // Draw edges
  edges.forEach(function(e) {
    var f = nodes.find(function(n) { return n.id === e.from; });
    var t = nodes.find(function(n) { return n.id === e.to; });
    if (f && t) {
      var etype = e.type || 'depends_on';
      var color = edgeColors[etype] || '#58a6ff';

      var l = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      l.setAttribute('x1', f.x + 60);
      l.setAttribute('y1', f.y + 25);
      l.setAttribute('x2', t.x + 60);
      l.setAttribute('y2', t.y + 25);
      l.setAttribute('stroke', color);
      l.setAttribute('stroke-width', etype === 'verdict_rejected' ? '3' : '2');
      l.setAttribute('stroke-dasharray', edgeDash[etype] || '');
      l.setAttribute('marker-end', 'url(#arrow)');

      // Edge label
      var mx = (f.x + t.x) / 2 + 60;
      var my = (f.y + t.y) / 2 + 20;
      var label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', mx);
      label.setAttribute('y', my);
      label.setAttribute('text-anchor', 'middle');
      label.setAttribute('fill', color);
      label.setAttribute('font-size', '10');
      var labelText = etype === 'depends_on' ? '' :
                      etype === 'verdict_rejected' ? '✗ rejected' : '✓ approved';
      label.textContent = labelText;
      s.appendChild(label);

      s.appendChild(l);
    }
  });

  // Draw nodes
  nodes.forEach(function(n) {
    var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('transform', 'translate(' + n.x + ',' + n.y + ')');
    g.setAttribute('data-node-id', n.id);
    g.style.cursor = 'move';

    var r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    r.setAttribute('width', '120');
    r.setAttribute('height', '50');
    r.setAttribute('rx', '8');
    r.setAttribute('fill', '#161b22');
    r.setAttribute('stroke', '#30363d');
    r.setAttribute('stroke-width', '2');
    g.appendChild(r);

    var t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', '60');
    t.setAttribute('y', '20');
    t.setAttribute('text-anchor', 'middle');
    t.setAttribute('fill', '#c9d1d9');
    t.setAttribute('font-size', '13');
    t.setAttribute('font-weight', '600');
    t.textContent = n.label;
    g.appendChild(t);

    var u = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    u.setAttribute('x', '60');
    u.setAttribute('y', '38');
    u.setAttribute('text-anchor', 'middle');
    u.setAttribute('fill', '#8b949e');
    u.setAttribute('font-size', '11');
    u.textContent = n.timeout + 's';
    g.appendChild(u);

    // Drag
    g.addEventListener('mousedown', function(ev) {
      window._dragNode = n;
      window._dragOffX = ev.clientX - n.x;
      window._dragOffY = ev.clientY - n.y;
    });

    // Double-click to remove
    g.addEventListener('dblclick', function() {
      removeNode(n.id);
    });

    s.appendChild(g);
  });

  updateSelects();
}

// Re-bind drag using data-node-id (correct targeting)
function rebindDrag() {
  nodes.forEach(function(n) {
    var gg = document.querySelector('g[data-node-id="' + n.id + '"]');
    if (gg) {
      gg.addEventListener('mousedown', function(ev) {
        window._dragNode = n;
        window._dragOffX = ev.clientX - n.x;
        window._dragOffY = ev.clientY - n.y;
      });
    }
  });
}

// Global drag handlers
document.addEventListener('mousemove', function(e) {
  if (window._dragNode) {
    window._dragNode.x = e.clientX - window._dragOffX;
    window._dragNode.y = e.clientY - window._dragOffY;
    draw();
    rebindDrag();
  }
});

document.addEventListener('mouseup', function() {
  if (window._dragNode) {
    saveState();
    window._dragNode = null;
  }
});

// ── Select Dropdowns ──

function updateSelects() {
  ['efrom', 'eto'].forEach(function(id) {
    var sel = document.getElementById(id);
    if (!sel) return;
    var v = sel.value;
    sel.innerHTML = '<option>—</option>';
    nodes.forEach(function(n) {
      sel.innerHTML += '<option value="' + n.id + '">' + n.label + '</option>';
    });
    if (v) sel.value = v;
  });
}

// ── YAML Export ──

function exportYAML() {
  // Build step definitions with edge types
  var stepMap = {};
  nodes.forEach(function(n) {
    stepMap[n.id] = {
      id: n.agent + '_step',
      agent: n.agent,
      timeout: n.timeout,
      depends_on: [],
      on_verdict_rejected: null,
      on_verdict_approved: null
    };
  });

  edges.forEach(function(e) {
    var targetStep = stepMap[e.to];
    if (!targetStep) return;
    var fromNode = nodes.find(function(n) { return n.id === e.from; });
    if (!fromNode) return;
    var fromId = fromNode.agent + '_step';

    if (e.type === 'verdict_rejected') {
      // Find the originating node's step entry
      var origStep = stepMap[e.from];
      if (origStep) origStep.on_verdict_rejected = { next: targetStep.id };
    } else if (e.type === 'verdict_approved') {
      var origStep2 = stepMap[e.from];
      if (origStep2) origStep2.on_verdict_approved = { action: 'mark_complete' };
    } else {
      // depends_on
      if (targetStep.depends_on.indexOf(fromId) === -1) {
        targetStep.depends_on.push(fromId);
      }
    }
  });

  var steps = Object.values(stepMap).map(function(s) {
    var step = {
      id: s.id,
      agent: s.agent,
      timeout: s.timeout
    };
    if (s.depends_on.length) step.depends_on = s.depends_on;
    if (s.on_verdict_rejected) step.on_verdict_rejected = s.on_verdict_rejected;
    if (s.on_verdict_approved) step.on_verdict_approved = s.on_verdict_approved;
    return step;
  });

  // Validate
  var wf = { workflow: { id: 'custom', steps: steps, error_policy: { max_rejections: 3, escalation_target: 'console' } } };
  var y = ['workflow:', '  id: custom', '  version: "1.0"', '  steps:'];
  steps.forEach(function(s) {
    y.push('    - id: ' + s.id);
    y.push('      agent: ' + s.agent);
    y.push('      timeout: ' + s.timeout);
    if (s.depends_on) y.push('      depends_on: ' + JSON.stringify(s.depends_on));
    if (s.on_verdict_rejected) y.push('      on_verdict_rejected: {next: "' + s.on_verdict_rejected.next + '"}');
    if (s.on_verdict_approved) y.push('      on_verdict_approved: {action: "mark_complete"}');
  });
  y.push('  error_policy:');
  y.push('    max_rejections: 3');
  y.push('    escalation_target: "console"');

  var yamlText = y.join('\n');
  document.getElementById('yaml-out').textContent = yamlText;

  // Show validation
  fetch('/api/graph/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      graph_id: 'custom',
      nodes: nodes.map(function(n) {
        return { id: n.id, agent: n.agent, timeout: n.timeout };
      }),
      edges: edges
    })
  }).then(function(r) { return r.json(); }).then(function(result) {
    var v = document.getElementById('validation-warnings');
    if (result.error) {
      v.innerHTML = '<p class="error-msg">⚠ ' + result.error + '</p>';
    } else if (result.status === 'ok') {
      v.innerHTML = '<p style="color:var(--green);">✅ YAML valid — ready to use</p>';
    }
  }).catch(function() {});
}

// ── Custom Agent Creation ──

function applyTemplate() {
  var k = document.getElementById('newAgentTemplate').value;
  var t = TEMPLATES[k];
  if (!t) return;
  document.getElementById('newAgentDesc').value = t.description || '';
  document.getElementById('newAgentTimeout').value = t.timeout || 600;
  document.getElementById('newAgentModel').value = t.model || 'deepseek/deepseek-chat';
  document.getElementById('newAgentOutput').value = (t.output_required || []).join(', ');
  var p = t.permissions || {};
  document.getElementById('newAgentWrite').value = (p.write || []).join(', ');
  document.getElementById('newAgentRead').value = (p.read || []).join(', ');
  document.getElementById('newAgentDeny').value = (p.deny || []).join(', ');
  document.getElementById('newAgentSkill').value = t.skill || '';
}

function addCustomAgent() {
  var name = document.getElementById('newAgentName').value.trim();
  if (!name) { alert('Agent name is required'); return; }
  if (AGENTS.find(function(a) { return a.name === name; })) {
    alert('Agent ' + name + ' already exists');
    return;
  }

  var desc = document.getElementById('newAgentDesc').value.trim();
  var timeout = parseInt(document.getElementById('newAgentTimeout').value) || 600;
  var model = document.getElementById('newAgentModel').value.trim() || 'deepseek/deepseek-chat';
  var outputReq = document.getElementById('newAgentOutput').value
    .split(',').map(function(s) { return s.trim(); }).filter(Boolean);
  var skill = document.getElementById('newAgentSkill').value.trim();
  var writePaths = document.getElementById('newAgentWrite').value
    .split(',').map(function(s) { return s.trim(); }).filter(Boolean);
  var readPaths = document.getElementById('newAgentRead').value
    .split(',').map(function(s) { return s.trim(); }).filter(Boolean);
  var denyPaths = document.getElementById('newAgentDeny').value
    .split(',').map(function(s) { return s.trim(); }).filter(Boolean);

  var agentInfo = {
    name: name, description: desc || name + ' step',
    timeout: timeout, model: model,
    output_required: outputReq,
    permissions: { write: writePaths, read: readPaths, deny: denyPaths },
    skill: skill
  };

  AGENTS.push(agentInfo);
  if (!TEMPLATES[name]) {
    TEMPLATES[name] = agentInfo;
    var sel = document.getElementById('newAgentTemplate');
    var o = document.createElement('option');
    o.value = name;
    o.textContent = name.toUpperCase();
    sel.appendChild(o);
  }

  // Add button
  var btn = document.createElement('button');
  btn.textContent = '+' + name.toUpperCase();
  btn.className = 'btn btn-green';
  btn.title = desc || name;
  btn.style.cssText = 'margin:4px;';
  btn.onclick = function() { addNode(name); };
  document.getElementById('agent-btns').appendChild(btn);

  // Clear form
  document.getElementById('newAgentName').value = '';
  document.getElementById('newAgentDesc').value = '';
}

// ── Init ──

document.addEventListener('DOMContentLoaded', function() {
  // Populate template dropdown
  var sel = document.getElementById('newAgentTemplate');
  if (sel) {
    Object.keys(TEMPLATES).forEach(function(k) {
      var o = document.createElement('option');
      o.value = k;
      o.textContent = k.toUpperCase();
      sel.appendChild(o);
    });
  }

  // Load saved state
  if (loadState()) {
    draw();
    rebindDrag();
  }

  // Periodic select refresh
  _upSelTimer = setInterval(updateSelects, 800);
});
