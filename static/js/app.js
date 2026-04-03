/**
 * app.js — AerialGuard v2 SOC Dashboard
 *
 * 5 pages:
 *   1. Live Airspace   — SSE-driven real-time feed + active track cards
 *   2. Tactical Map    — Canvas-based airspace overlay
 *   3. Incidents       — Incident timeline + detail view
 *   4. Flight Analytics — Per-object motion charts
 *   5. Alert Queue     — SOC-style prioritised alert list
 */

'use strict';

// ── Global state ──────────────────────────────────────────────────────────────
let _sse          = null;
let _activeAlerts = [];      // recent alerts (last 60 s)
let _incidents    = [];
let _trajChart    = null;
let _detailSpeedChart = null;
let _charts       = {};      // analytics page charts
let _chartTimer   = null;
let _tacTimer     = null;    // tactical map poll timer
let _alertTimer   = null;    // alert queue refresh timer
let _tacData      = null;    // latest tactical data

const THREAT_COLORS = {
  critical: '#cc44ff', high: '#ff2244', medium: '#ff8c00', low: '#00e87a'
};


// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  startClock();
  connectSSE();
  loadZones();
  pollObjects();
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


// ── Tab navigation ────────────────────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const page = btn.dataset.page;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const el = document.getElementById('page-' + page);
      if (el) el.classList.add('active');

      // Page-specific activation
      stopAnalytics(); stopTactical(); stopAlertTimer();
      if (page === 'incidents') loadIncidents();
      if (page === 'analytics') startAnalytics();
      if (page === 'tactical')  startTactical();
      if (page === 'alerts')    { loadAlertQueue(); startAlertTimer(); }
    });
  });
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
    if (msg.type === 'incident_start') { /* handled via object poll */ }
    if (msg.type === 'incident_end')   handleIncidentEnd(msg.data);
  };
}

function setStatus(state) {
  document.getElementById('status-dot').className  = 'status-dot ' + state;
  document.getElementById('status-text').textContent =
    state === 'live' ? 'LIVE' : state === 'error' ? 'OFFLINE' : 'CONNECTING';
}


// ── Status update ─────────────────────────────────────────────────────────────
function applyStatus(d) {
  set('sc-objects',   d.object_count ?? '—');
  set('sc-fps',       d.fps != null ? d.fps.toFixed(1) : '—');
  set('sc-inc-today', d.today_incidents ?? '—');
  set('sc-alerts-hr', d.hour_alerts     ?? '—');
  set('hdr-objects',  d.object_count ?? '—');

  const risk = d.risk_score ?? 0;
  set('hdr-risk', risk);
  const riskEl = document.getElementById('hdr-risk');
  if (riskEl) {
    riskEl.style.color = risk >= 75 ? 'var(--critical)' :
                         risk >= 50 ? 'var(--high)' :
                         risk >= 25 ? 'var(--medium)' : 'var(--low)';
  }

  // Risk bar
  const fill   = document.getElementById('risk-bar-fill');
  const valEl  = document.getElementById('risk-val');
  if (fill) {
    fill.style.width      = risk + '%';
    fill.style.background = risk >= 75 ? 'var(--critical)' :
                            risk >= 50 ? 'var(--high)' :
                            risk >= 25 ? 'var(--medium)' : 'var(--low)';
  }
  if (valEl) {
    valEl.textContent = risk;
    valEl.style.color = fill ? fill.style.background : '';
  }
}


// ── Object polling (Live page) ────────────────────────────────────────────────
function pollObjects() {
  setInterval(async () => {
    const livePage = document.getElementById('page-live');
    if (!livePage || !livePage.classList.contains('active')) return;
    try {
      const res  = await fetch('/api/objects');
      const objs = await res.json();
      renderActiveTracks(objs);
    } catch {}
  }, 1500);
}

function renderActiveTracks(objects) {
  const el    = document.getElementById('active-tracks');
  const badge = document.getElementById('tracks-badge');
  if (!el) return;

  badge && (badge.textContent = objects.length);

  if (!objects.length) {
    el.innerHTML = '<p class="empty">No objects detected</p>';
    return;
  }

  // Sort by threat score descending
  const sorted = [...objects].sort(
    (a, b) => (b.threat_score || 0) - (a.threat_score || 0)
  );

  el.innerHTML = sorted.map(o => {
    const level    = o.threat_level  || 'low';
    const color    = THREAT_COLORS[level] || '#00e87a';
    const beh      = o.behavior_display || o.behavior_label || '—';
    const score    = o.threat_score || 0;
    const pct      = score + '%';
    const zones    = (o.current_zones || []).join(', ') || '—';
    const flags    = [];
    if (o.hovering) flags.push('HOVER');
    if (o.circling) flags.push('CIRCLING');

    return `<div class="track-card ${level}">
      <div class="track-card-hdr">
        <span class="track-id-tag">T${o.track_id}</span>
        <span class="threat-badge ${level}">${level.toUpperCase()}</span>
        <span class="track-behavior">${escHtml(beh)}</span>
      </div>
      <div class="track-stats-grid">
        <div class="track-stat"><div class="tsk">SPEED</div><div class="tsv">${(o.speed||0).toFixed(1)} m/s</div></div>
        <div class="track-stat"><div class="tsk">AVG SPD</div><div class="tsv">${(o.avg_speed||0).toFixed(1)} m/s</div></div>
        <div class="track-stat"><div class="tsk">MAX SPD</div><div class="tsv">${(o.max_speed||0).toFixed(1)} m/s</div></div>
        <div class="track-stat"><div class="tsk">TIME</div><div class="tsv">${fmtDuration(o.time_in_frame||0)}</div></div>
        <div class="track-stat"><div class="tsk">CONF</div><div class="tsv">${((o.confidence||0)*100).toFixed(0)}%</div></div>
        <div class="track-stat"><div class="tsk">DIST</div><div class="tsv">~${(o.altitude_proxy||0).toFixed(0)}m</div></div>
        <div class="track-stat"><div class="tsk">APPROACH</div><div class="tsv">${(o.closest_approach||0).toFixed(0)}px</div></div>
        <div class="track-stat"><div class="tsk">HOVER</div><div class="tsv">${(o.hover_duration||0).toFixed(0)}s</div></div>
        <div class="track-stat"><div class="tsk">ZONES</div><div class="tsv">${escHtml(zones)}</div></div>
      </div>
      ${flags.length ? `<div style="display:flex;gap:4px;margin:3px 0">
        ${flags.map(f => `<span class="threat-badge medium" style="font-size:9px">${f}</span>`).join('')}
      </div>` : ''}
      <div class="track-card-footer">
        <span style="font-size:10px;color:var(--text-dim)">THREAT ${score}/100</span>
        <div class="track-score-bar">
          <div class="track-score-fill" style="width:${pct};background:${color}"></div>
        </div>
        <span style="font-size:9px;color:var(--text-dim)">${o.confidence_band||'—'}</span>
      </div>
    </div>`;
  }).join('');
}


// ── Zones ─────────────────────────────────────────────────────────────────────
async function loadZones() {
  try {
    const res   = await fetch('/api/zones');
    const zones = await res.json();
    renderZoneCards(zones);
  } catch {}
}

setInterval(async () => {
  if (!document.getElementById('page-live')?.classList.contains('active')) return;
  try {
    const res   = await fetch('/api/zones');
    const zones = await res.json();
    renderZoneCards(zones);
  } catch {}
}, 3000);

function renderZoneCards(zones) {
  const el = document.getElementById('zone-cards');
  if (!el) return;
  if (!zones.length) { el.innerHTML = '<p class="empty">No zones configured</p>'; return; }
  el.innerHTML = zones.map(z => {
    const breached = z.status === 'BREACH';
    return `<div class="zone-card ${breached ? 'breach' : ''}">
      <span class="zone-dot" style="background:${escHtml(z.color_hex || '#00e87a')}"></span>
      <span class="zone-name">${escHtml(z.name)}</span>
      <span class="zone-status-badge ${breached ? 'breach' : 'clear'}">${z.status}</span>
    </div>`;
  }).join('');
}


// ── Incoming alert ────────────────────────────────────────────────────────────
function handleAlert(a) {
  a._received = Date.now();
  _activeAlerts.unshift(a);
  _activeAlerts = _activeAlerts.filter(x => Date.now() - x._received < 60000);

  // Flash border
  document.body.classList.add('flash');
  setTimeout(() => document.body.classList.remove('flash'), 2200);

  // Alert banner
  const banner = document.getElementById('alert-banner');
  const text   = document.getElementById('alert-banner-text');
  if (banner && text) {
    text.textContent =
      `ALERT — Track T${a.track_id}  ${a.rule.replace('_', ' ').toUpperCase()}` +
      (a.zone ? `  [${a.zone}]` : '');
    banner.classList.remove('hidden');
    setTimeout(() => banner.classList.add('hidden'), 8000);
  }

  // Nav badge
  updateAlertBadge(_activeAlerts.length);

  // If alert queue is open, refresh it
  if (document.getElementById('page-alerts')?.classList.contains('active')) {
    loadAlertQueue();
  }
}

function dismissBanner() {
  document.getElementById('alert-banner')?.classList.add('hidden');
}

function updateAlertBadge(count) {
  const badge = document.getElementById('nav-alert-badge');
  if (!badge) return;
  if (count > 0) {
    badge.textContent = count;
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

function handleIncidentEnd(ev) {
  if (document.getElementById('page-incidents')?.classList.contains('active')) {
    loadIncidents();
  }
}


// ══════════════════════════════════════════════════ PAGE 2: TACTICAL MAP

function startTactical() {
  drawTactical();
  _tacTimer = setInterval(async () => {
    try {
      const res = await fetch('/api/tactical');
      _tacData  = await res.json();
      renderTactical(_tacData);
      renderTacLegend(_tacData.tracks || []);
      renderTacZones(_tacData.zones   || []);
    } catch {}
  }, 1000);
}

function stopTactical() {
  if (_tacTimer) { clearInterval(_tacTimer); _tacTimer = null; }
}

function drawTactical() {
  const canvas = document.getElementById('tactical-canvas');
  if (!canvas) return;
  const parent = canvas.parentElement;
  canvas.width  = parent.clientWidth;
  canvas.height = parent.clientHeight - 38; // subtract panel header
}

function renderTactical(data) {
  const canvas = document.getElementById('tactical-canvas');
  if (!canvas) return;

  const parent = canvas.parentElement;
  canvas.width  = parent.clientWidth;
  canvas.height = parent.clientHeight - 38;

  const ctx  = canvas.getContext('2d');
  const W    = canvas.width;
  const H    = canvas.height;
  const showTrails = document.getElementById('tac-trails')?.checked ?? true;
  const showRings  = document.getElementById('tac-rings')?.checked  ?? true;
  const showZones  = document.getElementById('tac-zones')?.checked  ?? true;

  // Background
  ctx.fillStyle = '#040810';
  ctx.fillRect(0, 0, W, H);

  // Grid
  ctx.strokeStyle = '#0f1e2e';
  ctx.lineWidth   = 0.5;
  const gs = 40;
  for (let x = 0; x < W; x += gs) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
  for (let y = 0; y < H; y += gs) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

  // Frame border
  ctx.strokeStyle = '#1a2d42';
  ctx.lineWidth   = 1;
  ctx.strokeRect(2, 2, W - 4, H - 4);

  // Facility centre marker
  const cx = W / 2, cy = H / 2;
  ctx.strokeStyle = '#1e3a55'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(cx - 12, cy); ctx.lineTo(cx + 12, cy); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx, cy - 12); ctx.lineTo(cx, cy + 12); ctx.stroke();
  ctx.strokeStyle = '#00d4c8'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.arc(cx, cy, 6, 0, Math.PI * 2); ctx.stroke();

  // Proximity rings
  if (showRings) {
    const maxR  = Math.min(W, H) * 0.45;
    const rings = [0.25, 0.5, 0.75];
    const ringLabels = ['CLOSE', 'MED', 'FAR'];
    rings.forEach((f, i) => {
      const r = maxR * f;
      ctx.strokeStyle = `rgba(0,212,200,${0.06 + i * 0.02})`;
      ctx.setLineDash([4, 6]);
      ctx.lineWidth = 0.7;
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(0,212,200,.3)';
      ctx.font      = '9px monospace';
      ctx.fillText(ringLabels[i], cx + r + 3, cy - 3);
    });
  }

  // Zones
  if (showZones && data.zones) {
    data.zones.forEach(z => {
      const pts = z.points || z.polygon;
      if (!pts || pts.length < 3) return;
      const hexColor = z.color_hex || '#00d4c8';
      const r = parseInt(hexColor.slice(1, 3), 16);
      const g = parseInt(hexColor.slice(3, 5), 16);
      const b = parseInt(hexColor.slice(5, 7), 16);
      ctx.fillStyle   = `rgba(${r},${g},${b},0.06)`;
      ctx.strokeStyle = `rgba(${r},${g},${b},0.4)`;
      ctx.lineWidth   = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      pts.forEach(([px, py], idx) => {
        // scale from frame coords to canvas
        const x = (px / 640) * W;
        const y = (py / 480) * H;
        idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.setLineDash([]);
    });
  }

  // Tracks
  if (!data.tracks || !data.tracks.length) return;

  data.tracks.forEach(track => {
    const rawCx = track.centroid[0];
    const rawCy = track.centroid[1];
    const tx    = (rawCx / 640) * W;
    const ty    = (rawCy / 480) * H;
    const color = track.threat_color || '#00e87a';

    // Trail
    if (showTrails && track.trail && track.trail.length > 1) {
      const trail = track.trail;
      for (let i = 1; i < trail.length; i++) {
        const alpha = i / trail.length;
        ctx.strokeStyle = color.replace(')', `,${alpha * 0.7})`).replace('rgb(', 'rgba(')
                             .replace('#', '');
        // simpler: just use globalAlpha
        ctx.globalAlpha = alpha * 0.7;
        ctx.strokeStyle = color;
        ctx.lineWidth   = 1.5;
        ctx.beginPath();
        ctx.moveTo((trail[i - 1][0] / 640) * W, (trail[i - 1][1] / 480) * H);
        ctx.lineTo((trail[i][0]     / 640) * W, (trail[i][1]     / 480) * H);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // Dot
    const r = track.threat_level === 'critical' ? 7 :
              track.threat_level === 'high'     ? 6 : 5;
    ctx.fillStyle   = color;
    ctx.strokeStyle = '#040810';
    ctx.lineWidth   = 1.5;
    ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(tx, ty, r, 0, Math.PI * 2);
    ctx.fill(); ctx.stroke();

    // Pulse ring for high/critical
    if (track.threat_level === 'high' || track.threat_level === 'critical') {
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.3;
      ctx.lineWidth   = 1;
      ctx.beginPath(); ctx.arc(tx, ty, r + 5, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // ID label
    ctx.fillStyle = color;
    ctx.font      = 'bold 10px monospace';
    ctx.fillText(`T${track.track_id}`, tx + r + 3, ty - 3);

    // Speed label
    ctx.fillStyle = 'rgba(208,232,240,.6)';
    ctx.font      = '9px monospace';
    ctx.fillText(`${(track.speed || 0).toFixed(1)}m/s`, tx + r + 3, ty + 9);

    // Hover indicator
    if (track.hovering) {
      ctx.strokeStyle = '#ff8c00';
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([2, 3]);
      ctx.lineWidth   = 1;
      ctx.beginPath(); ctx.arc(tx, ty, r + 10, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
  });

  // Timestamp
  ctx.fillStyle = 'rgba(74,112,144,.5)';
  ctx.font      = '9px monospace';
  ctx.fillText(new Date().toLocaleTimeString(), 8, H - 6);
}

function renderTacLegend(tracks) {
  const el = document.getElementById('tac-legend');
  if (!el) return;
  if (!tracks.length) { el.innerHTML = '<p class="empty">No active tracks</p>'; return; }
  el.innerHTML = tracks.map(t => {
    const color = t.threat_color || '#00e87a';
    return `<div class="tac-legend-item">
      <div class="tac-leg-dot" style="background:${color}"></div>
      <div class="tac-leg-info">
        <div class="tac-leg-id">T${t.track_id}</div>
        <div class="tac-leg-beh">${escHtml(t.behavior_display || '—')}</div>
      </div>
      <span class="tac-leg-threat" style="color:${color}">${t.threat_score || 0}</span>
    </div>`;
  }).join('');
}

function renderTacZones(zones) {
  const el = document.getElementById('tac-zones-list');
  if (!el) return;
  if (!zones.length) { el.innerHTML = '<p class="empty">No zones</p>'; return; }
  el.innerHTML = zones.map(z =>
    `<div class="tac-zone-item">
      <div class="zone-dot" style="background:${escHtml(z.color_hex||'#00d4c8')}"></div>
      <span style="color:var(--text-hi);flex:1">${escHtml(z.name)}</span>
      <span class="zone-status-badge ${z.status === 'BREACH' ? 'breach' : 'clear'}">${z.status||'CLEAR'}</span>
    </div>`
  ).join('');
}


// ══════════════════════════════════════════════════ PAGE 3: INCIDENTS

async function loadIncidents() {
  try {
    const res  = await fetch('/api/incidents?limit=60');
    _incidents = await res.json();
    renderIncidentGrid();
  } catch {
    const grid = document.getElementById('incident-grid');
    if (grid) grid.innerHTML = '<p class="empty">Failed to load incidents.</p>';
  }
}

function renderIncidentGrid() {
  const grid = document.getElementById('incident-grid');
  if (!grid) return;
  if (!_incidents.length) { grid.innerHTML = '<p class="empty">No incidents recorded yet.</p>'; return; }
  grid.innerHTML = _incidents.map(inc => incCard(inc)).join('');
}

function incCard(inc) {
  const start  = fmtDateTime(inc.start_time);
  const dur    = fmtDuration(inc.duration || 0);
  const rules  = JSON.parse(inc.triggered_rules || '[]');
  const zones  = JSON.parse(inc.zones_entered   || '[]');
  const level  = threatLevelFromScore(inc.threat_score || 0);
  const chips  = rules.map(r => `<span class="rule-chip ${r}">${r.replace('_',' ')}</span>`).join('');
  const thumb  = inc.has_thumb
    ? `<img src="/api/incidents/${inc.id}/thumb" alt="thumb"
           onerror="this.parentElement.innerHTML='<div class=inc-no-thumb>No preview</div>'">`
    : `<div class="inc-no-thumb">No preview</div>`;

  return `<div class="incident-card ${level}" onclick="openIncident(${inc.id})">
    <div class="inc-thumb">${thumb}
      <span class="inc-id-badge">INC-${String(inc.id).padStart(4,'0')}</span>
    </div>
    <div class="inc-body">
      <div class="inc-time">${start}</div>
      <div class="inc-stats">
        <div class="inc-stat">Duration <strong>${dur}</strong></div>
        <div class="inc-stat">Max spd <strong>${(inc.max_speed||0).toFixed(1)} m/s</strong></div>
        <div class="inc-stat">Hover <strong>${(inc.hover_duration||0).toFixed(0)}s</strong></div>
        <div class="inc-stat">Threat <strong style="color:${THREAT_COLORS[level]}">${inc.threat_score||0}</strong></div>
      </div>
      ${zones.length ? `<div class="inc-stat" style="font-size:11px;margin-bottom:6px">
        Zones: <strong>${escHtml(zones.join(', '))}</strong></div>` : ''}
      ${inc.behavior_tag ? `<div class="inc-stat" style="font-size:11px;margin-bottom:6px">
        Behavior: <strong>${escHtml(inc.behavior_tag.replace('_',' '))}</strong></div>` : ''}
      <div class="inc-rules">${chips || '<span class="empty" style="padding:0;font-size:10px">No alerts</span>'}</div>
      ${inc.summary ? `<div class="inc-summary-preview">${escHtml(inc.summary.slice(0,120))}…</div>` : ''}
    </div>
  </div>`;
}

async function openIncident(id) {
  document.getElementById('incidents-view').style.display  = 'none';
  document.getElementById('incident-detail').style.display = 'block';
  set('detail-title', `INC-${String(id).padStart(4,'0')}`);

  // Clear previous charts
  if (_trajChart)       { _trajChart.destroy();       _trajChart       = null; }
  if (_detailSpeedChart){ _detailSpeedChart.destroy(); _detailSpeedChart = null; }
  document.getElementById('detail-stats-tbl').innerHTML = '';
  document.getElementById('alert-timeline').innerHTML   = '<p class="empty">Loading…</p>';
  document.getElementById('detail-summary').textContent = '';

  try {
    const res = await fetch(`/api/incidents/${id}`);
    const inc = await res.json();

    const rules  = JSON.parse(inc.triggered_rules || '[]');
    const zones  = JSON.parse(inc.zones_entered   || '[]');
    const level  = threatLevelFromScore(inc.threat_score || 0);

    // Threat badge in header
    const badge = document.getElementById('detail-threat-badge');
    if (badge) {
      badge.className   = `detail-threat-badge threat-badge ${level}`;
      badge.textContent = `THREAT ${inc.threat_score || 0} — ${level.toUpperCase()}`;
    }

    // Stats table
    document.getElementById('detail-stats-tbl').innerHTML = rows([
      ['Track ID',          `T${inc.track_id}`],
      ['Start',             fmtDateTime(inc.start_time)],
      ['End',               inc.end_time ? fmtDateTime(inc.end_time) : 'Open'],
      ['Duration',          fmtDuration(inc.duration || 0)],
      ['Max speed',         `${(inc.max_speed||0).toFixed(2)} m/s`],
      ['Avg speed',         `${(inc.avg_speed||0).toFixed(2)} m/s`],
      ['Hover duration',    `${(inc.hover_duration||0).toFixed(1)}s`],
      ['Path length',       `${(inc.path_length||0).toFixed(0)} px`],
      ['Closest approach',  `${(inc.closest_approach||0).toFixed(0)} px`],
      ['Zone crossings',    inc.zone_crossings ?? 0],
      ['Behavior',          (inc.behavior_tag||'—').replace('_',' ')],
      ['Threat score',      `${inc.threat_score||0}/100`],
      ['Zones entered',     zones.join(', ') || 'None'],
      ['Triggered rules',   rules.join(', ') || 'None'],
      ['Frames',            inc.frame_count ?? '—'],
    ]);

    // Clip
    const vid    = document.getElementById('detail-video');
    const noClip = document.getElementById('no-clip-msg');
    if (inc.has_clip && vid && noClip) {
      vid.src = `/api/incidents/${id}/clip`;
      vid.style.display    = 'block';
      noClip.style.display = 'none';
    } else if (vid && noClip) {
      vid.style.display    = 'none';
      noClip.style.display = 'block';
    }

    // Alert timeline
    const tl = document.getElementById('alert-timeline');
    if (tl) {
      if (!inc.alerts?.length) {
        tl.innerHTML = '<p class="empty">No alerts for this incident.</p>';
      } else {
        tl.innerHTML = inc.alerts.map(a => {
          const t = new Date(a.timestamp * 1000).toLocaleTimeString('en-US', { hour12: false });
          return `<div class="tl-item">
            <div class="tl-dot ${a.rule}"></div>
            <span class="tl-time">${t}</span>
            <span class="tl-rule">${a.rule.replace('_',' ')}</span>
            <span class="tl-zone">${a.zone ? escHtml(a.zone) : ''}</span>
          </div>`;
        }).join('');
      }
    }

    // Summary
    set('detail-summary', inc.summary || 'No summary available.');

    // Trajectory chart
    if (inc.track_points?.length) {
      const pts = inc.track_points;
      const trajCtx = document.getElementById('traj-chart')?.getContext('2d');
      if (trajCtx) {
        _trajChart = new Chart(trajCtx, {
          type: 'scatter',
          data: { datasets: [{
            label: `Track T${inc.track_id}`,
            data: pts.map(p => ({ x: p.cx, y: p.cy })),
            borderColor: '#00d4c8', backgroundColor: 'rgba(0,212,200,.2)',
            pointRadius: 2, showLine: true, tension: 0.3,
          }]},
          options: {
            animation: false,
            plugins: { legend: { labels: { color: '#4a7090' } } },
            scales: {
              x: { title:{display:true,text:'X (px)',color:'#4a7090'}, ticks:{color:'#4a7090'}, grid:{color:'#0f1e2e'} },
              y: { title:{display:true,text:'Y (px)',color:'#4a7090'}, ticks:{color:'#4a7090'}, grid:{color:'#0f1e2e'}, reverse:true },
            }
          }
        });
      }

      // Speed-over-time chart
      const spdCtx = document.getElementById('detail-speed-chart')?.getContext('2d');
      if (spdCtx) {
        _detailSpeedChart = new Chart(spdCtx, {
          type: 'line',
          data: { datasets: [{
            label: 'Speed (m/s)',
            data: pts.map(p => ({ x: fmtTime(p.timestamp), y: p.speed })),
            borderColor: '#ff8c00', backgroundColor: 'rgba(255,140,0,.1)',
            pointRadius: 0, tension: 0.3, fill: true, borderWidth: 1.5,
          },{
            label: 'Avg Speed (m/s)',
            data: pts.map(p => ({ x: fmtTime(p.timestamp), y: p.avg_speed })),
            borderColor: '#00d4c8', pointRadius: 0, tension: 0.3, borderWidth: 1.2,
          }]},
          options: {
            animation: false,
            plugins: { legend: { labels: { color: '#4a7090', font:{size:10} } } },
            scales: {
              x: { ticks:{color:'#4a7090',maxTicksLimit:6}, grid:{color:'#0f1e2e'} },
              y: { ticks:{color:'#4a7090'}, grid:{color:'#0f1e2e'}, beginAtZero:true },
            }
          }
        });
      }
    }
  } catch (e) {
    console.error('Failed to load incident detail', e);
  }
}

function closeDetail() {
  document.getElementById('incident-detail').style.display = 'none';
  document.getElementById('incidents-view').style.display  = 'block';
  if (_trajChart)        { _trajChart.destroy();        _trajChart        = null; }
  if (_detailSpeedChart) { _detailSpeedChart.destroy();  _detailSpeedChart = null; }
}

function rows(pairs) {
  return pairs.map(([k, v]) =>
    `<tr><td>${escHtml(k)}</td><td>${escHtml(String(v ?? '—'))}</td></tr>`
  ).join('');
}


// ══════════════════════════════════════════════════ PAGE 4: FLIGHT ANALYTICS

const _CHART_OPTS = () => ({
  animation: false,
  plugins: { legend: { labels: { color: '#4a7090', font: { size: 10 } } } },
  scales: {
    x: { ticks: { color: '#4a7090', maxTicksLimit: 8 }, grid: { color: '#0f1e2e' } },
    y: { ticks: { color: '#4a7090' }, grid: { color: '#0f1e2e' }, beginAtZero: true },
  }
});

function startAnalytics() {
  initCharts();
  populateTrackSelector();
  refreshCharts();
  _chartTimer = setInterval(refreshCharts, 4000);
}

function stopAnalytics() {
  if (_chartTimer) { clearInterval(_chartTimer); _chartTimer = null; }
}

function initCharts() {
  if (_charts.speed) return;
  const mkLine = (id) => {
    const ctx = document.getElementById(id)?.getContext('2d');
    if (!ctx) return null;
    return new Chart(ctx, {
      type: 'line',
      data: { labels: [], datasets: [] },
      options: _CHART_OPTS(),
    });
  };
  _charts.speed   = mkLine('chart-speed');
  _charts.alt     = mkLine('chart-alt');
  _charts.accel   = mkLine('chart-accel');
  _charts.heading = mkLine('chart-heading');
  _charts.conf    = mkLine('chart-conf');
  _charts.count   = mkLine('chart-count');
}

async function populateTrackSelector() {
  const sel = document.getElementById('analytics-track-sel');
  if (!sel) return;
  try {
    const res  = await fetch('/api/objects');
    const objs = await res.json();
    const currentVal = sel.value;
    sel.innerHTML = '<option value="">— all tracks —</option>' +
      objs.map(o => `<option value="${o.track_id}">Track T${o.track_id}</option>`).join('');
    if (currentVal) sel.value = currentVal;
  } catch {}
}

async function onTrackSelected() {
  await refreshCharts();
}

async function refreshCharts() {
  const range   = parseInt(document.getElementById('analytics-range')?.value || '60');
  const since   = Date.now() / 1000 - range;
  const trackId = document.getElementById('analytics-track-sel')?.value;

  try {
    let points;
    if (trackId) {
      const res = await fetch(`/api/analytics/object/${trackId}?since=${since}&limit=600`);
      points = await res.json();
    } else {
      const res = await fetch(`/api/analytics/tracks?since=${since}&limit=800`);
      points = await res.json();
    }
    buildCharts(points, since);
    buildZoneTimeline(points);
  } catch {}
}

function buildCharts(points, since) {
  const byTrack = {};
  for (const p of points) {
    (byTrack[p.track_id] = byTrack[p.track_id] || []).push(p);
  }
  for (const pts of Object.values(byTrack)) pts.sort((a, b) => a.timestamp - b.timestamp);

  const COLORS = ['#00d4c8','#ff8c00','#4488ff','#00e87a','#ff2244','#cc44ff'];
  const tids   = Object.keys(byTrack).slice(0, 6);

  const mkDs = (key, tids, byTrack) =>
    tids.map((tid, i) => ({
      label: `T${tid}`,
      data: byTrack[tid].map(p => ({ x: fmtTime(p.timestamp), y: p[key] ?? 0 })),
      borderColor: COLORS[i % COLORS.length], backgroundColor: 'transparent',
      pointRadius: 0, tension: 0.3, borderWidth: 1.5,
    }));

  _updateChart(_charts.speed,   mkDs('speed',     tids, byTrack));
  _updateChart(_charts.alt,     mkDs('altitude_proxy', tids, byTrack));
  _updateChart(_charts.accel,   mkDs('acceleration',   tids, byTrack));
  _updateChart(_charts.heading, mkDs('heading_volatility', tids, byTrack)); // repurposed
  _updateChart(_charts.conf,    mkDs('confidence', tids, byTrack));

  // Object count per 5-second bucket
  const buckets = {};
  for (const p of points) {
    const b = Math.floor(p.timestamp / 5) * 5;
    buckets[b] = (buckets[b] || new Set()).add(p.track_id);
  }
  const sorted = Object.entries(buckets).sort((a, b) => +a[0] - +b[0]);
  if (_charts.count) {
    _charts.count.data.datasets = [{
      label: 'Objects', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
      borderColor: '#00e87a', backgroundColor: 'rgba(0,232,122,.10)',
      data: sorted.map(([ts, ids]) => ({ x: fmtTime(+ts), y: ids.size })),
    }];
    _charts.count.update('none');
  }
}

function _updateChart(chart, datasets) {
  if (!chart) return;
  chart.data.datasets = datasets;
  chart.update('none');
}

function buildZoneTimeline(points) {
  const el = document.getElementById('zone-timeline');
  if (!el) return;

  const byTrack = {};
  for (const p of points) (byTrack[p.track_id] = byTrack[p.track_id] || []).push(p);

  const entries = [];
  for (const [tid, pts] of Object.entries(byTrack)) {
    pts.sort((a, b) => a.timestamp - b.timestamp);
    let prev = [];
    for (const p of pts) {
      const zones = JSON.parse(p.in_zones || '[]');
      for (const z of zones) {
        if (!prev.includes(z)) entries.push({ tid, zone: z, timestamp: p.timestamp });
      }
      prev = zones;
    }
  }
  entries.sort((a, b) => b.timestamp - a.timestamp);

  if (!entries.length) { el.innerHTML = '<p class="empty">No zone activity.</p>'; return; }
  el.innerHTML = entries.slice(0, 30).map(e =>
    `<div class="zt-row">
      <div class="zt-dot"></div>
      <span class="zt-zone">${escHtml(e.zone)}</span>
      <span class="zt-track">T${e.tid}</span>
      <span class="zt-time">${fmtDateTime(e.timestamp)}</span>
    </div>`
  ).join('');
}


// ══════════════════════════════════════════════════ PAGE 5: ALERT QUEUE

function startAlertTimer() {
  _alertTimer = setInterval(loadAlertQueue, 10000);
}
function stopAlertTimer() {
  if (_alertTimer) { clearInterval(_alertTimer); _alertTimer = null; }
}

async function loadAlertQueue() {
  const filter = document.getElementById('alert-filter')?.value;
  const url    = '/api/alerts/queue?limit=100' + (filter ? `&status=${filter}` : '');
  try {
    const res   = await fetch(url);
    const queue = await res.json();
    renderAlertQueue(queue);
  } catch {
    const tbody = document.getElementById('alert-queue-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="9" class="empty">Failed to load alerts.</td></tr>';
  }
}

function renderAlertQueue(alerts) {
  const tbody = document.getElementById('alert-queue-body');
  if (!tbody) return;
  if (!alerts.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">No alerts found.</td></tr>';
    return;
  }
  tbody.innerHTML = alerts.map(a => {
    const ts      = fmtDateTime(a.timestamp);
    const sev     = a.severity || 'medium';
    const status  = a.status  || 'active';
    const incLink = a.incident_id
      ? `<span style="cursor:pointer;color:var(--cyan)" onclick="jumpToIncident(${a.incident_id})">INC-${String(a.incident_id).padStart(4,'0')}</span>`
      : '—';
    const details = (() => { try { return JSON.parse(a.details || '{}'); } catch { return {}; } })();

    return `<tr id="alert-row-${a.id}">
      <td><span class="aq-sev ${sev}"></span></td>
      <td style="color:var(--text-dim)">${a.id}</td>
      <td style="color:var(--text-dim);white-space:nowrap">${ts}</td>
      <td><span class="aq-rule ${a.rule}">${a.rule.replace('_',' ')}</span></td>
      <td style="color:var(--cyan)">T${a.track_id}</td>
      <td>${a.zone ? escHtml(a.zone) : '—'}</td>
      <td>${incLink}</td>
      <td class="aq-status">
        <select onchange="setAlertStatus(${a.id}, this.value)">
          ${['active','monitoring','escalated','resolved','archived'].map(s =>
            `<option value="${s}" ${s === status ? 'selected' : ''}>${s}</option>`
          ).join('')}
        </select>
      </td>
      <td class="aq-actions">
        <button class="aq-btn escalate" onclick="setAlertStatus(${a.id},'escalated')">Escalate</button>
        <button class="aq-btn resolve"  onclick="setAlertStatus(${a.id},'resolved')">Resolve</button>
      </td>
    </tr>`;
  }).join('');
}

async function setAlertStatus(alertId, status) {
  try {
    await fetch(`/api/alerts/${alertId}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    loadAlertQueue();
  } catch {}
}

function jumpToIncident(incId) {
  // Switch to incidents tab and open that incident
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const btn = document.querySelector('.tab-btn[data-page="incidents"]');
  const pg  = document.getElementById('page-incidents');
  if (btn) btn.classList.add('active');
  if (pg)  pg.classList.add('active');
  stopTactical(); stopAnalytics(); stopAlertTimer();
  loadIncidents().then(() => openIncident(incId));
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
  const s = Math.floor(secs || 0);
  const m = Math.floor(s / 60), rem = s % 60;
  const h = Math.floor(m / 60), min = m % 60;
  return h ? `${h}h ${min}m ${rem}s` : m ? `${m}m ${rem}s` : `${rem}s`;
}

function fmtDateTime(ts) {
  return new Date((ts || 0) * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

function fmtTime(ts) {
  return new Date((ts || 0) * 1000).toLocaleTimeString('en-US', { hour12: false });
}

function threatLevelFromScore(score) {
  if (score >= 75) return 'critical';
  if (score >= 50) return 'high';
  if (score >= 25) return 'medium';
  return 'low';
}
