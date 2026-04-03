/**
 * app.js — AI Surveillance Dashboard
 *
 * Connects to the Flask backend via:
 *   - /api/events  (Server-Sent Events) for real-time stats + alerts
 *   - /api/alerts  (REST)              for initial history load
 *   - /api/zones   (REST)              for zone list
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
const MAX_ACTIVE = 5;   // max alerts shown in the live feed
const MAX_HISTORY = 200;

let activeAlerts = [];
let allAlerts    = [];
let zones        = [];
let _sse         = null;


// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  startClock();
  connectSSE();
  fetchZones();
  fetchHistory();
});


// ── Clock ─────────────────────────────────────────────────────────────────────
function startClock() {
  const el = document.getElementById('clock');
  const tick = () => {
    el.textContent = new Date().toLocaleTimeString('en-US', { hour12: false });
  };
  tick();
  setInterval(tick, 1000);
}


// ── Server-Sent Events ────────────────────────────────────────────────────────
function connectSSE() {
  if (_sse) { _sse.close(); }

  _sse = new EventSource('/api/events');

  _sse.onopen = () => setStatus('live');

  _sse.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }

    if (msg.type === 'stats') applyStats(msg.data);
    if (msg.type === 'alert') handleIncomingAlert(msg.data);
  };

  _sse.onerror = () => {
    setStatus('error');
    _sse.close();
    setTimeout(connectSSE, 4000);   // auto-reconnect
  };
}


// ── Status indicator ──────────────────────────────────────────────────────────
function setStatus(state) {
  const dot  = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  dot.className = 'status-dot ' + state;
  text.textContent = state === 'live' ? 'LIVE'
                   : state === 'error' ? 'DISCONNECTED'
                   : 'CONNECTING';
}


// ── Stats ─────────────────────────────────────────────────────────────────────
function applyStats(data) {
  setText('stat-people', data.people_count ?? '—');
  setText('stat-today',  data.today        ?? '—');
  setText('stat-fps',    data.fps != null  ? data.fps.toFixed(1) : '—');
  setText('stat-hour',   data.last_hour    ?? '—');
  setText('uptime',      formatDuration(data.uptime ?? 0));
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}


// ── Incoming alert (from SSE) ─────────────────────────────────────────────────
function handleIncomingAlert(alert) {
  // Flash the page border briefly
  document.body.classList.add('flash-alert');
  setTimeout(() => document.body.classList.remove('flash-alert'), 2200);

  // Add to active feed
  activeAlerts.unshift(alert);
  if (activeAlerts.length > MAX_ACTIVE) activeAlerts.length = MAX_ACTIVE;
  renderActiveFeed();

  // Prepend to history without full re-render
  allAlerts.unshift(alert);
  if (allAlerts.length > MAX_HISTORY) allAlerts.length = MAX_HISTORY;
  prependHistoryRow(alert);
}


// ── Active alerts feed ────────────────────────────────────────────────────────
function renderActiveFeed() {
  const feed  = document.getElementById('alert-feed');
  const badge = document.getElementById('alert-badge');
  badge.textContent = activeAlerts.length;

  if (activeAlerts.length === 0) {
    feed.innerHTML = '<p class="empty">No active alerts</p>';
    return;
  }

  feed.innerHTML = activeAlerts.map(a => {
    const age = Math.max(0, Math.round(Date.now() / 1000 - a.timestamp));
    return `
      <div class="alert-item">
        <div class="alert-title">&#9888; Person #${a.person_id} &mdash; ${escHtml(a.zone)}</div>
        <div class="alert-meta">Dwell: <strong>${a.dwell_seconds}s</strong> &nbsp;&bull;&nbsp; ${age}s ago</div>
      </div>`;
  }).join('');
}

// Refresh "X ago" timestamps every 5 s without hitting the network
setInterval(renderActiveFeed, 5000);


// ── Alert history ─────────────────────────────────────────────────────────────
async function fetchHistory() {
  try {
    const res = await fetch('/api/alerts?limit=200');
    allAlerts = await res.json();
    renderFullHistory();
  } catch (err) {
    console.warn('Could not load alert history:', err);
  }
}

function renderFullHistory() {
  const tbody = document.getElementById('history-body');
  if (allAlerts.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">No alerts recorded yet</td></tr>';
    return;
  }
  tbody.innerHTML = allAlerts.map(alertRow).join('');
}

function prependHistoryRow(alert) {
  const tbody = document.getElementById('history-body');
  // Remove placeholder row if present
  const empty = tbody.querySelector('.empty');
  if (empty) tbody.innerHTML = '';

  const tr = document.createElement('tr');
  tr.innerHTML = alertRow(alert);
  tbody.insertBefore(tr, tbody.firstChild);
}

function alertRow(a) {
  const dt = new Date(a.timestamp * 1000);
  const ts = dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
           + ' '
           + dt.toLocaleTimeString('en-US', { hour12: false });
  return `<td>${ts}</td><td>#${a.person_id}</td><td>${escHtml(a.zone)}</td>`
       + `<td>${a.dwell_seconds}s</td><td>${a.threshold}s</td>`;
}


// ── Zones ─────────────────────────────────────────────────────────────────────
async function fetchZones() {
  try {
    const res = await fetch('/api/zones');
    zones = await res.json();
    renderZones();
  } catch (err) {
    console.warn('Could not load zones:', err);
  }
}

function renderZones() {
  const list = document.getElementById('zone-list');
  if (zones.length === 0) {
    list.innerHTML = '<p class="empty">No zones configured</p>';
    return;
  }
  list.innerHTML = zones.map(z => `
    <div class="zone-item">
      <span class="zone-dot" style="background:${escHtml(z.color_hex || '#00d4aa')}"></span>
      <span class="zone-name">${escHtml(z.name)}</span>
      <span class="zone-thresh">${z.alert_seconds}s</span>
    </div>`).join('');
}


// ── CSV export ────────────────────────────────────────────────────────────────
function exportCSV() {
  if (allAlerts.length === 0) { alert('No alerts to export.'); return; }

  const header = 'Time,Person ID,Zone,Dwell (s),Threshold (s)\n';
  const rows = allAlerts.map(a => {
    const iso = new Date(a.timestamp * 1000).toISOString();
    return `${iso},${a.person_id},"${a.zone}",${a.dwell_seconds},${a.threshold}`;
  }).join('\n');

  const blob = new Blob([header + rows], { type: 'text/csv' });
  const url  = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement('a'), {
    href: url,
    download: `alerts_${new Date().toISOString().slice(0, 10)}.csv`,
  });
  link.click();
  URL.revokeObjectURL(url);
}


// ── Utilities ─────────────────────────────────────────────────────────────────
function formatDuration(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return [h, m, s].map(n => String(n).padStart(2, '0')).join(':');
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
