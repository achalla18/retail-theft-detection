/**
 * app.js — AerialGuard 3-page dashboard
 *
 * Page 1: Live Monitoring  — SSE-driven real-time updates
 * Page 2: Incident Review  — REST: /api/incidents + detail
 * Page 3: Flight Analytics — REST: /api/analytics/tracks + Chart.js
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let _sse          = null;
let _activeAlerts = [];     // alerts in last 60 s (for live intrusion panel)
let _incidents    = [];     // loaded incident list
let _trajChart    = null;   // Chart.js instance for detail page trajectory
let _charts       = {};     // {speed, alt, conf, count} Chart.js instances
let _chartTimer   = null;   // analytics poll interval

// Track-level point buffers for analytics charts {track_id: [{ts,speed,alt,conf}]}
let _trackBufs    = {};

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  startClock();
  connectSSE();
  loadZones();
});


// ── Tab navigation ────────────────────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const page = document.getElementById('page-' + btn.dataset.page);
      if (page) page.classList.add('active');

      if (btn.dataset.page === 'incidents') loadIncidents();
      if (btn.dataset.page === 'analytics') startAnalytics();
      if (btn.dataset.page === 'live'      ) stopAnalytics();
    });
  });
}


// ── Clock ─────────────────────────────────────────────────────────────────────
function startClock() {
  const el = document.getElementById('clock');
  const tick = () => {
    el.textContent = new Date().toLocaleTimeString('en-US', { hour12: false });
  };
  tick();
  setInterval(tick, 1000);
}


// ── SSE ───────────────────────────────────────────────────────────────────────
function connectSSE() {
  if (_sse) _sse.close();
  _sse = new EventSource('/api/events');

  _sse.onopen    = () => setStatus('live');
  _sse.onerror   = () => { setStatus('error'); _sse.close(); setTimeout(connectSSE, 4000); };
  _sse.onmessage = e => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === 'status')         applyStatus(msg.data);
    if (msg.type === 'alert')          handleAlert(msg.data);
    if (msg.type === 'incident_start') handleIncidentStart(msg.data);
    if (msg.type === 'incident_end')   handleIncidentEnd(msg.data);
  };
}

function setStatus(state) {
  document.getElementById('status-dot').className  = 'status-dot ' + state;
  document.getElementById('status-text').textContent =
    state === 'live' ? 'LIVE' : state === 'error' ? 'OFFLINE' : 'CONNECTING';
}


// ── Status update (from SSE keepalive or status event) ────────────────────────
function applyStatus(d) {
  set('sc-objects',    d.object_count ?? '—');
  set('sc-fps',        d.fps != null ? d.fps.toFixed(1) : '—');
  set('sc-inc-today',  d.today_incidents ?? '—');
  set('sc-alerts-hr',  d.hour_alerts     ?? '—');

  // Risk badge
  const risk = d.risk_score ?? 0;
  set('risk-val', risk);
  const rv = document.getElementById('risk-val');
  rv.className = 'risk-val' + (risk >= 60 ? ' high' : risk >= 30 ? ' med' : '');
}


// ── Live: active objects (polled via REST every 1.5s) ─────────────────────────
setInterval(async () => {
  if (!document.getElementById('page-live').classList.contains('active')) return;
  try {
    const res  = await fetch('/api/objects');
    const objs = await res.json();
    renderObjectStats(objs);
    renderIntrusions(objs);
  } catch {}
}, 1500);

function renderObjectStats(objects) {
  const el = document.getElementById('object-stats');
  if (!objects.length) { el.innerHTML = '<p class="empty">No objects detected</p>'; return; }
  el.innerHTML = objects.map(o => {
    const flags = [
      o.hovering  ? '⊙ HOVER'    : '',
      o.circling  ? '↻ CIRCLING'  : '',
      (o.current_zones||[]).length ? `[${o.current_zones.join(',')}]` : '',
    ].filter(Boolean).join(' ');
    return `<div class="obj-row">
      <span class="obj-id">T${o.track_id}</span>
      <span class="obj-spd">${o.speed?.toFixed(1) ?? '—'} m/s</span>
      <span class="obj-alt">~${o.altitude_proxy?.toFixed(0) ?? '—'}m</span>
      ${flags ? `<span class="obj-flag">${escHtml(flags)}</span>` : ''}
    </div>`;
  }).join('');
}

function renderIntrusions(objects) {
  const list  = document.getElementById('intrusion-list');
  const badge = document.getElementById('intrusion-badge');
  const alertTids = new Set(_activeAlerts.map(a => a.track_id));
  const active = objects.filter(o =>
    (o.current_zones||[]).length || o.hovering || o.circling || alertTids.has(o.track_id)
  );
  badge.textContent = active.length;
  if (!active.length) { list.innerHTML = '<p class="empty">No active intrusions</p>'; return; }
  list.innerHTML = active.map(o => {
    const reasons = [];
    if ((o.current_zones||[]).length) reasons.push(`Zone: ${o.current_zones.join(', ')}`);
    if (o.hovering)  reasons.push(`Hovering ${o.hover_duration?.toFixed(0) ?? '?'}s`);
    if (o.circling)  reasons.push('Circling');
    return `<div class="intrusion-item">
      <div class="intrusion-title">&#9888; Track T${o.track_id}</div>
      <div class="intrusion-meta">${reasons.join(' &bull; ') || 'Detected'} &bull; ${o.speed?.toFixed(1) ?? '—'} m/s</div>
    </div>`;
  }).join('');
}


// ── Incoming alert ────────────────────────────────────────────────────────────
function handleAlert(a) {
  a._received = Date.now();
  _activeAlerts.unshift(a);
  _activeAlerts = _activeAlerts.filter(x => Date.now() - x._received < 60000);

  // Flash
  document.body.classList.add('flash');
  setTimeout(() => document.body.classList.remove('flash'), 2200);
}

// ── Incident events ───────────────────────────────────────────────────────────
function handleIncidentStart(ev) {
  // Could highlight the video or update a badge — handled via object poll
}
function handleIncidentEnd(ev) {
  // If incidents page is active, refresh the grid
  if (document.getElementById('page-incidents').classList.contains('active')) {
    loadIncidents();
  }
}


// ── Zones (Page 1) ────────────────────────────────────────────────────────────
async function loadZones() {
  try {
    const res   = await fetch('/api/zones');
    const zones = await res.json();
    renderZoneCards(zones);
  } catch {}
}

// Refresh zones every 3 s while page is live
setInterval(async () => {
  if (!document.getElementById('page-live').classList.contains('active')) return;
  try {
    const res   = await fetch('/api/zones');
    const zones = await res.json();
    renderZoneCards(zones);
  } catch {}
}, 3000);

function renderZoneCards(zones) {
  const el = document.getElementById('zone-cards');
  if (!zones.length) { el.innerHTML = '<p class="empty">No zones configured</p>'; return; }
  el.innerHTML = zones.map(z => {
    const breached = z.status === 'BREACH';
    return `<div class="zone-card ${breached ? 'breach' : ''}">
      <span class="zone-dot" style="background:${escHtml(z.color_hex||'#00e87a')}"></span>
      <span class="zone-name">${escHtml(z.name)}</span>
      <span class="zone-status-badge ${breached ? 'breach' : 'clear'}">${z.status}</span>
    </div>`;
  }).join('');
}


// ══════════════════════════════════════════════════════ PAGE 2: INCIDENTS

async function loadIncidents() {
  try {
    const res  = await fetch('/api/incidents?limit=50');
    _incidents = await res.json();
    renderIncidentGrid();
  } catch {
    document.getElementById('incident-grid').innerHTML =
      '<p class="empty">Failed to load incidents.</p>';
  }
}

function renderIncidentGrid() {
  const grid = document.getElementById('incident-grid');
  if (!_incidents.length) {
    grid.innerHTML = '<p class="empty">No incidents recorded yet.</p>'; return;
  }
  grid.innerHTML = _incidents.map(inc => incCard(inc)).join('');
}

function incCard(inc) {
  const start  = fmtDateTime(inc.start_time);
  const dur    = fmtDuration(inc.duration || 0);
  const rules  = JSON.parse(inc.triggered_rules || '[]');
  const zones  = JSON.parse(inc.zones_entered   || '[]');
  const chips  = rules.map(r => `<span class="rule-chip ${r}">${r.replace('_',' ')}</span>`).join('');
  const thumb  = inc.has_thumb
    ? `<img src="/api/incidents/${inc.id}/thumb" alt="thumb" onerror="this.parentElement.innerHTML='<div class=inc-no-thumb>No preview</div>'">`
    : `<div class="inc-no-thumb">No preview</div>`;

  return `<div class="incident-card" onclick="openIncident(${inc.id})">
    <div class="inc-thumb">${thumb}<span class="inc-id-badge">INC-${String(inc.id).padStart(4,'0')}</span></div>
    <div class="inc-body">
      <div class="inc-time">${start}</div>
      <div class="inc-stats">
        <div class="inc-stat">Duration <strong>${dur}</strong></div>
        <div class="inc-stat">Max spd <strong>${(inc.max_speed||0).toFixed(1)} m/s</strong></div>
      </div>
      ${zones.length ? `<div class="inc-stat" style="margin-bottom:6px;font-size:11px">
        Zones: <strong>${escHtml(zones.join(', '))}</strong></div>` : ''}
      <div class="inc-rules">${chips || '<span class="empty" style="padding:0;font-size:10px">No alerts triggered</span>'}</div>
      ${inc.summary ? `<div class="inc-summary-preview">${escHtml(inc.summary)}</div>` : ''}
    </div>
  </div>`;
}

async function openIncident(id) {
  document.getElementById('incidents-view').style.display  = 'none';
  document.getElementById('incident-detail').style.display = 'block';
  document.getElementById('detail-title').textContent = `INC-${String(id).padStart(4,'0')}`;

  // Clear previous
  document.getElementById('detail-stats-tbl').innerHTML = '';
  document.getElementById('alert-timeline').innerHTML   = '<p class="empty">Loading&hellip;</p>';
  document.getElementById('detail-summary').textContent = '';
  if (_trajChart) { _trajChart.destroy(); _trajChart = null; }

  try {
    const res = await fetch(`/api/incidents/${id}`);
    const inc = await res.json();

    // Stats table
    const rules = JSON.parse(inc.triggered_rules || '[]');
    const zones = JSON.parse(inc.zones_entered   || '[]');
    document.getElementById('detail-stats-tbl').innerHTML = rows([
      ['Track ID',        `T${inc.track_id}`],
      ['Start',           fmtDateTime(inc.start_time)],
      ['End',             inc.end_time ? fmtDateTime(inc.end_time) : 'Open'],
      ['Duration',        fmtDuration(inc.duration || 0)],
      ['Max speed',       `${(inc.max_speed||0).toFixed(2)} m/s`],
      ['Avg speed',       `${(inc.avg_speed||0).toFixed(2)} m/s`],
      ['Frames',          inc.frame_count ?? '—'],
      ['Zones entered',   zones.join(', ') || 'None'],
      ['Triggered rules', rules.join(', ') || 'None'],
    ]);

    // Video clip
    const vid    = document.getElementById('detail-video');
    const noClip = document.getElementById('no-clip-msg');
    if (inc.has_clip) {
      vid.src    = `/api/incidents/${id}/clip`;
      vid.style.display  = 'block';
      noClip.style.display = 'none';
    } else {
      vid.style.display  = 'none';
      noClip.style.display = 'block';
    }

    // Alert timeline
    const tl = document.getElementById('alert-timeline');
    if (!inc.alerts?.length) {
      tl.innerHTML = '<p class="empty">No alerts for this incident.</p>';
    } else {
      tl.innerHTML = inc.alerts.map(a => {
        const t = new Date(a.timestamp * 1000).toLocaleTimeString('en-US', {hour12: false});
        return `<div class="tl-item">
          <div class="tl-dot ${a.rule}"></div>
          <span class="tl-time">${t}</span>
          <span class="tl-rule">${a.rule.replace('_',' ')}</span>
          <span class="tl-zone">${a.zone ? escHtml(a.zone) : ''}</span>
        </div>`;
      }).join('');
    }

    // Summary
    document.getElementById('detail-summary').textContent = inc.summary || 'No summary available.';

    // Trajectory scatter chart
    if (inc.track_points?.length) {
      const pts  = inc.track_points;
      const ctx  = document.getElementById('traj-chart').getContext('2d');
      const data = pts.map(p => ({ x: p.cx, y: p.cy }));
      _trajChart = new Chart(ctx, {
        type: 'scatter',
        data: {
          datasets: [{
            label: `Track T${inc.track_id}`,
            data,
            borderColor: '#00d4c8',
            backgroundColor: 'rgba(0,212,200,.25)',
            pointRadius: 3,
            showLine: true,
            tension: 0.3,
          }]
        },
        options: {
          animation: false,
          plugins: { legend: { labels: { color: '#4a6878' } } },
          scales: {
            x: { title: { display: true, text: 'X (px)', color: '#4a6878' },
                 ticks: { color: '#4a6878' }, grid: { color: '#1a3045' } },
            y: { title: { display: true, text: 'Y (px)', color: '#4a6878' },
                 ticks: { color: '#4a6878' }, grid: { color: '#1a3045' }, reverse: true },
          }
        }
      });
    }
  } catch (e) {
    console.error('Failed to load incident detail', e);
  }
}

function closeDetail() {
  document.getElementById('incident-detail').style.display = 'none';
  document.getElementById('incidents-view').style.display  = 'block';
  if (_trajChart) { _trajChart.destroy(); _trajChart = null; }
}

function rows(pairs) {
  return pairs.map(([k, v]) =>
    `<tr><td>${escHtml(k)}</td><td>${escHtml(String(v))}</td></tr>`
  ).join('');
}


// ══════════════════════════════════════════════════════ PAGE 3: ANALYTICS

const CHART_OPTS = (label, color) => ({
  animation: false,
  plugins: {
    legend: { labels: { color: '#4a6878', font: { size: 11 } } },
  },
  scales: {
    x: { ticks: { color: '#4a6878', maxTicksLimit: 8 }, grid: { color: '#1a3045' } },
    y: { ticks: { color: '#4a6878' }, grid: { color: '#1a3045' }, beginAtZero: true },
  }
});

function startAnalytics() {
  initCharts();
  refreshCharts();
  _chartTimer = setInterval(refreshCharts, 3000);
}

function stopAnalytics() {
  if (_chartTimer) { clearInterval(_chartTimer); _chartTimer = null; }
}

function initCharts() {
  if (_charts.speed) return;  // already created
  const mkLine = (id, label, color) => {
    const ctx = document.getElementById(id)?.getContext('2d');
    if (!ctx) return null;
    return new Chart(ctx, {
      type: 'line',
      data: { labels: [], datasets: [] },
      options: CHART_OPTS(label, color),
    });
  };
  _charts.speed = mkLine('chart-speed', 'Speed (m/s)',    '#00d4c8');
  _charts.alt   = mkLine('chart-alt',   'Altitude (m)',   '#4488ff');
  _charts.conf  = mkLine('chart-conf',  'Confidence',     '#ff8c00');
  _charts.count = mkLine('chart-count', 'Object count',   '#00e87a');
}

async function refreshCharts() {
  const range = parseInt(document.getElementById('analytics-range')?.value || '60');
  const since = Date.now() / 1000 - range;
  try {
    const res    = await fetch(`/api/analytics/tracks?since=${since}&limit=1000`);
    const points = await res.json();
    buildCharts(points, since);
    buildZoneTimeline(points, since);
  } catch {}
}

function buildCharts(points, since) {
  // Group by track_id
  const byTrack = {};
  for (const p of points) {
    (byTrack[p.track_id] = byTrack[p.track_id] || []).push(p);
  }
  // Sort each track by timestamp
  for (const pts of Object.values(byTrack))
    pts.sort((a, b) => a.timestamp - b.timestamp);

  const colors = ['#00d4c8','#ff8c00','#4488ff','#00e87a','#ff2244','#cc44ff'];
  const trackIds = Object.keys(byTrack).slice(0, 5);

  // Build per-chart datasets
  const makeDatasets = (key) =>
    trackIds.map((tid, i) => ({
      label: `T${tid}`,
      data: byTrack[tid].map(p => ({ x: fmtTime(p.timestamp), y: p[key] })),
      borderColor: colors[i % colors.length],
      backgroundColor: 'transparent',
      pointRadius: 0,
      tension: 0.3,
      borderWidth: 1.5,
    }));

  updateChart(_charts.speed, makeDatasets('speed'));
  updateChart(_charts.alt,   makeDatasets('altitude_proxy'));
  updateChart(_charts.conf,  makeDatasets('confidence'));

  // Object count over time (bin by 5-second buckets)
  updateCountChart(points, since);
}

function updateChart(chart, datasets) {
  if (!chart) return;
  chart.data.datasets = datasets;
  chart.update('none');
}

function updateCountChart(points, since) {
  if (!_charts.count) return;
  const buckets = {};
  for (const p of points) {
    const bucket = Math.floor(p.timestamp / 5) * 5;
    buckets[bucket] = (buckets[bucket] || new Set()).add(p.track_id);
  }
  const sorted = Object.entries(buckets).sort((a, b) => +a[0] - +b[0]);
  _charts.count.data.datasets = [{
    label: 'Objects',
    data: sorted.map(([ts, ids]) => ({ x: fmtTime(+ts), y: ids.size })),
    borderColor: '#00e87a',
    backgroundColor: 'rgba(0,232,122,.10)',
    fill: true,
    tension: 0.3,
    pointRadius: 0,
    borderWidth: 1.5,
  }];
  _charts.count.update('none');
}

function buildZoneTimeline(points, since) {
  const el = document.getElementById('zone-timeline');
  // Find zone transition events from track_points
  const entries = [];
  const byTrack = {};
  for (const p of points) {
    (byTrack[p.track_id] = byTrack[p.track_id] || []).push(p);
  }
  for (const [tid, pts] of Object.entries(byTrack)) {
    pts.sort((a, b) => a.timestamp - b.timestamp);
    let prevZones = [];
    for (const p of pts) {
      const zones = JSON.parse(p.in_zones || '[]');
      for (const z of zones) {
        if (!prevZones.includes(z)) {
          entries.push({ tid, zone: z, timestamp: p.timestamp, event: 'enter' });
        }
      }
      prevZones = zones;
    }
  }
  entries.sort((a, b) => b.timestamp - a.timestamp);
  if (!entries.length) {
    el.innerHTML = '<p class="empty">No zone activity in the selected period.</p>'; return;
  }
  el.innerHTML = entries.slice(0, 30).map(e =>
    `<div class="zt-row">
      <div class="zt-dot" style="background:#ff2244"></div>
      <span class="zt-zone">${escHtml(e.zone)}</span>
      <span class="zt-track">Track T${e.tid}</span>
      <span class="zt-time">${fmtDateTime(e.timestamp)}</span>
    </div>`
  ).join('');
}


// ── Utilities ─────────────────────────────────────────────────────────────────
function set(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtDuration(secs) {
  const s = Math.floor(secs);
  const m = Math.floor(s / 60), rem = s % 60;
  const h = Math.floor(m / 60), min  = m % 60;
  return h ? `${h}h ${min}m ${rem}s` : m ? `${m}m ${rem}s` : `${rem}s`;
}

function fmtDateTime(ts) {
  return new Date(ts * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('en-US', { hour12: false });
}
