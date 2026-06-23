/**
 * AgentForge Dashboard — charts, search/filter, DAG visualization.
 * Loaded at page bottom, after DOM and CDN scripts are ready.
 */

// ── 7-Day Trend Charts ──

function initCharts() {
  var tokenCanvas = document.getElementById('tokenChart');
  var passCanvas = document.getElementById('passRateChart');
  if (!tokenCanvas || !passCanvas) return;

  fetch('/api/timeseries')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.token_trend && d.token_trend.length) {
        try {
          new Chart(tokenCanvas, {
            type: 'bar',
            data: {
              labels: d.token_trend.map(function(x) { return x.date; }),
              datasets: [{
                label: 'Tokens',
                data: d.token_trend.map(function(x) { return x.tokens; }),
                backgroundColor: '#58a6ff',
                borderColor: '#58a6ff',
                maxBarThickness: 60
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              animation: { duration: 300 },
              plugins: { legend: { labels: { color: '#8b949e' } } },
              scales: {
                x: { ticks: { color: '#8b949e', maxRotation: 45 } },
                y: {
                  beginAtZero: true,
                  ticks: {
                    color: '#8b949e',
                    callback: function(v) {
                      return v >= 1e6 ? (v/1e6).toFixed(1) + 'M' : v >= 1e3 ? (v/1e3).toFixed(0) + 'K' : v;
                    }
                  }
                }
              }
            }
          });

          new Chart(passCanvas, {
            type: 'line',
            data: {
              labels: d.pass_rate.map(function(x) { return x.date; }),
              datasets: [{
                label: 'Pass Rate %',
                data: d.pass_rate.map(function(x) { return x.rate; }),
                borderColor: '#3fb950',
                backgroundColor: 'rgba(63,185,80,0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 4
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              animation: { duration: 300 },
              plugins: { legend: { labels: { color: '#8b949e' } } },
              scales: {
                x: { ticks: { color: '#8b949e', maxRotation: 45 } },
                y: { min: 0, max: 100, ticks: { color: '#8b949e', stepSize: 20 } }
              }
            }
          });
        } catch(e) {
          tokenCanvas.parentElement.innerHTML +=
            '<p class="error-msg">Chart error: ' + e.message + '</p>';
        }
      } else {
        tokenCanvas.parentElement.innerHTML +=
          '<p class="muted" style="text-align:center;padding:20px;">' +
          'No chart data yet — run tasks to populate</p>';
      }
    })
    .catch(function(e) {
      tokenCanvas.parentElement.innerHTML +=
        '<p class="error-msg">Failed to load trend data</p>';
    });
}

// ── Search / Filter ──

function initSearchFilter() {
  var search = document.getElementById('searchInput');
  var status = document.getElementById('statusFilter');
  if (!search || !status) return;

  function filter() {
    var q = search.value.toLowerCase();
    var s = status.value;
    document.querySelectorAll('.task-row').forEach(function(row) {
      var matchId = row.getAttribute('data-id').toLowerCase().indexOf(q) !== -1;
      var matchStatus = !s || row.getAttribute('data-status') === s;
      row.style.display = (matchId && matchStatus) ? '' : 'none';
    });
  }
  search.addEventListener('input', filter);
  status.addEventListener('change', filter);
}

// ── Workflow DAG (Mermaid.js) ──

function initDAG() {
  var container = document.getElementById('mermaid-container');
  if (!container) return;

  fetch('/api/workflow-dag')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error || !d.nodes || !d.nodes.length) {
        container.innerHTML =
          '<span class="muted">No workflow DAG available</span>';
        return;
      }
      var graph = ['graph LR'];
      d.nodes.forEach(function(n) {
        var label = n.agent + ': ' + n.id;
        var cls = n.status === 'completed' ? 'done' :
                  n.status === 'running' ? 'active' : 'pending';
        graph.push(n.id + '("' + label + '"):::' + cls);
      });
      d.edges.forEach(function(e) {
        graph.push(e.source + '-->' + e.target);
      });
      container.innerHTML =
        '<div class="mermaid">' + graph.join('\n') + '</div>';
      try {
        mermaid.run({ nodes: [container.querySelector('.mermaid')] });
      } catch(e2) {
        container.innerHTML =
          '<span class="error-msg">DAG render error: ' + e2.message + '</span>';
      }
    })
    .catch(function(e) {
      container.innerHTML =
        '<span class="muted">Workflow DAG unavailable</span>';
    });
}

// ── Init on DOM ready ──

document.addEventListener('DOMContentLoaded', function() {
  initCharts();
  initSearchFilter();
  initDAG();
});
