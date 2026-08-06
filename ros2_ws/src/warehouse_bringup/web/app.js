import { command, setSetting, ackAlert, getJSON } from './js/api.js';
import { createStateFeed } from './js/state.js';
import { MapView } from './js/mapview.js';

const $ = (id) => document.getElementById(id);

let state = null;
let selectedRobot = null;
let activeTab = 'dashboard';
let feed = null;
let mapView = null;

const PRIO_LABEL = { 0: 'Low', 1: 'Normal', 2: 'High' };
const TASK_STATUS_TAG = {
  PENDING: 'info', ASSIGNED: 'info', WAITING: 'info', ACTIVE: 'accent',
  RUNNING: 'accent', COMPLETED: 'ok', CANCELLED: 'bad',
};

function tag(cls, text) { return `<span class="tag ${cls}">${text}</span>`; }

function toast(msg, err) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(t._t);
  t._t = setTimeout(() => { t.className = 'toast'; }, 2600);
}

async function doCommand(payload, okMsg) {
  const res = await command(payload);
  if (res && res.ok) toast(okMsg || 'OK');
  else toast((res && res.error) || 'Command failed', true);
  return res;
}

// ═══════════════════════════════════════════════════════════════
// Top bar + tab switching
// ═══════════════════════════════════════════════════════════════

function renderTop() {
  const f = state.fleet;
  const alerts = state.alerts ? state.alerts.active : [];
  const crit = alerts.filter((a) => a.severity === 'critical').length;
  const cc = state.control_center || {};
  const backendOk = cc.backend_url ? (cc.backend_ok ? 'ok' : 'bad') : '';
  const backendPill = cc.backend_url
    ? `<span class="pill"><span class="dot ${backendOk}"></span>Backend <b>${cc.backend_ok ? 'ok' : 'off'}</b></span>`
    : '';
  const pills = [
    ['Active', f.active, 'ok'], ['Idle', f.idle, 'idle'],
    ['Charging', f.charging, 'warn'], ['Offline', f.offline, 'bad'],
    ['Total', f.total, ''],
  ];
  $('top-status').innerHTML = pills.map(([l, v, c]) =>
    `<span class="pill"><span class="dot ${c}"></span>${l} <b>${v}</b></span>`).join('') +
    `<span class="pill" style="${crit ? 'border-color:var(--crit);color:var(--crit)' : ''}">Alerts <b>${alerts.length}</b></span>` +
    backendPill;
}

let backendCache = { tasks: null, events: null, alerts: null, monitoring: null, components: null };
async function refreshBackend() {
  const cc = state.control_center || {};
  if (!cc.backend_url) return;
  const kinds = ['tasks', 'events', 'alerts', 'monitoring', 'components'];
  for (const kind of kinds) {
    try {
      const r = await getJSON(`/api/backend/${kind}?limit=100`);
      if (r && r.ok) backendCache[kind] = r.data;
    } catch (_) { /* keep stale */ }
  }
}

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.panel').forEach((p) =>
    p.classList.toggle('active', p.id === `tab-${tab}`));
  if (state) {
    if (tab === 'map') renderMap();
    if (tab === 'robots') renderRobots();
    if (tab === 'tasks') renderTasks();
    if (tab === 'fleet') renderFleet();
    if (tab === 'monitoring') renderMonitoring();
    if (tab === 'events') renderEvents();
    if (tab === 'alerts') renderAlerts();
    if (tab === 'settings') renderSettings();
  }
}

// ═══════════════════════════════════════════════════════════════
// Dashboard tab
// ═══════════════════════════════════════════════════════════════

function renderDashboard() {
  const f = state.fleet;
  const t = state.tasks.counts;
  const a = state.analytics;
  const panel = $('tab-dashboard');

  const kpi = (v, l, cls) =>
    `<div class="kpi"><div class="v ${cls || ''}">${v === null || v === undefined ? '—' : v}</div><div class="l">${l}</div></div>`;

  const fleetKpis = [
    kpi(f.active, 'Active robots', 'ok'), kpi(f.idle, 'Idle robots', ''),
    kpi(f.charging, 'Charging robots', 'warn'), kpi(f.offline, 'Offline robots', 'bad'),
    kpi(f.total, 'Total robots', 'accent'), kpi(f.completed, 'Completed tasks', 'ok'),
    kpi(f.queue_length, 'Queued tasks', ''),
  ];
  const taskKpis = [
    kpi(t.pending, 'Pending tasks', 'info'), kpi(t.running, 'Running tasks', 'accent'),
    kpi(t.completed, 'Completed tasks', 'ok'), kpi(t.cancelled, 'Cancelled tasks', 'bad'),
  ];
  const anKpis = [
    kpi(a.avg_task_duration == null ? '—' : a.avg_task_duration + 's', 'Avg task duration'),
    kpi(a.avg_queue_wait == null ? '—' : a.avg_queue_wait + 's', 'Avg queue wait'),
    kpi(a.avg_reservation_wait == null ? '—' : a.avg_reservation_wait + 's', 'Avg reservation wait'),
    kpi(a.utilization == null ? '—' : (a.utilization * 100).toFixed(0) + '%', 'Robot utilization'),
    kpi(a.total_distance, 'Distance (m)'),
    kpi(a.total_battery_usage, 'Battery usage (%)'),
    kpi(a.throughput, 'Throughput (tasks)'),
  ];

  panel.innerHTML = `
    <h2 class="section">Fleet</h2><div class="kpi-row">${fleetKpis.join('')}</div>
    <h2 class="section">Tasks</h2><div class="kpi-row">${taskKpis.join('')}</div>
    <h2 class="section">Analytics</h2><div class="kpi-row">${anKpis.join('')}</div>
    <div class="grid grid-2">
      <div class="card">
        <h3>Robots <span class="hint">click to select</span></h3>
        <div class="table-wrap">${robotTable()}</div>
      </div>
      <div class="card">
        <h3>Reservations <span class="hint">segments / owner / wait</span></h3>
        ${reservationTable()}
      </div>
    </div>`;
}

function robotTable() {
  if (!state.robots.length) return '<div class="empty">No robots registered yet</div>';
  const rows = state.robots.map((r) => {
    const st = r.charging ? tag('warn', 'charging')
      : r.status === 'OFFLINE' ? tag('bad', 'OFFLINE')
      : r.estop ? tag('crit', 'ESTOP')
      : r.current_task ? tag('ok', 'busy')
      : tag('info', 'idle');
    const bat = r.battery == null ? '—' : `<div class="meter"><i style="width:${Math.max(0, r.battery)}%;background:${r.battery <= 30 ? 'var(--bad)' : 'var(--ok)'}"></i></div>${r.battery.toFixed(0)}%`;
    return `<tr class="clickable ${r.id === selectedRobot ? 'selected' : ''}" data-robot="${r.id}">
      <td><b>${r.id}</b></td>
      <td>${r.namespace || '/'}</td><td>${st}</td>
      <td>${r.current_task || '—'}</td><td>${bat}</td>
      <td>${r.x == null ? '—' : r.x.toFixed(2)}</td><td>${r.y == null ? '—' : r.y.toFixed(2)}</td>
      <td>${r.speed && r.speed.lin != null ? r.speed.lin.toFixed(2) + ' m/s' : '—'}</td>
      <td>${r.localization}</td></tr>`;
  }).join('');
  return `<table><thead><tr><th>Robot</th><th>NS</th><th>State</th><th>Task</th><th>Battery</th><th>X</th><th>Y</th><th>Speed</th><th>Local</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function reservationTable() {
  const res = state.reservations;
  const rows = res.list.map((r) => `<tr>
    <td>${r.robot_id}</td><td>${r.task_id}</td>
    <td>${r.segment_index + 1}/${r.total_segments}</td>
    <td>${(r.segments_reserved || []).length}</td>
    <td>${r.head_on ? tag('warn', 'head-on') : tag('ok', 'clear')}</td></tr>`).join('');
  const q = res.queue.map((q) => `<tr><td>${q.robot_id}</td><td>${q.task_id}</td></tr>`).join('');
  return `<table><thead><tr><th>Robot</th><th>Task</th><th>Seg</th><th>Reserved</th><th>Traffic</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="5" class="empty">No active reservations</td></tr>`}</tbody></table>
    <h3 style="margin-top:12px">Reservation queue <span class="hint">${res.pending_count} pending · ${res.retry_count} retry</span></h3>
    <table><thead><tr><th>Robot</th><th>Task</th></tr></thead>
    <tbody>${q || `<tr><td colspan="2" class="empty">Queue empty</td></tr>`}</tbody></table>`;
}

// ═══════════════════════════════════════════════════════════════
// Map tab
// ═══════════════════════════════════════════════════════════════

function renderMap() {
  const cv = $('mapview');
  if (!cv) return; // canvas element missing — nothing to draw
  if (!mapView) {
    mapView = new MapView(cv, (id) => { selectedRobot = id; refreshRobotControls(); });
  }
  mapView.setState(state);
  mapView.selected = selectedRobot;
}

// ═══════════════════════════════════════════════════════════════
// Robots tab (manual control)
// ═══════════════════════════════════════════════════════════════

function robotFor(id) {
  return state.robots.find((r) => r.id === id) || null;
}

function renderRobots() {
  const panel = $('tab-robots');
  const sel = robotFor(selectedRobot);
  panel.innerHTML = `
    <div class="grid grid-2eq">
      <div class="card">
        <h3>Robots</h3>${robotTable()}
      </div>
      <div class="card">
        <h3>Control — <span id="ctrl-name">${selectedRobot || 'none selected'}</span></h3>
        ${sel ? controlPanelHTML(sel) : '<div class="empty">Select a robot in the table to control it.</div>'}
      </div>
    </div>`;
}

function controlPanelHTML(r) {
  return `
    <div class="control-grid">
      <button class="btn" data-cmd='{"action":"move","direction":"forward"}'>▲ Forward</button>
      <button class="btn" data-cmd='{"action":"rotate","direction":"left"}'>⟲ Left</button>
      <button class="btn" data-cmd='{"action":"move","direction":"backward"}'>▼ Backward</button>
      <button class="btn" data-cmd='{"action":"rotate","direction":"right"}'>⟳ Right</button>
      <button class="btn" data-cmd='{"action":"stop"}'>■ Stop</button>
      <button class="btn" data-cmd='{"action":"estop"}'>✋ E-Stop</button>
      <button class="btn ok-btn" data-cmd='{"action":"resume"}'>▶ Resume</button>
      <button class="btn warn-btn" data-cmd='{"action":"home"}'>⌂ Home</button>
      <button class="btn warn-btn" data-cmd='{"action":"charger"}'>⚡ Charger</button>
    </div>
    <div class="field" style="margin-top:12px">
      <label>Send goal (x, y, yaw — metres / radians)</label>
      <div class="grid-2eq" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px">
        <input id="goal-x" type="number" step="0.1" value="2" placeholder="x">
        <input id="goal-y" type="number" step="0.1" value="0" placeholder="y">
        <input id="goal-yaw" type="number" step="0.1" value="0" placeholder="yaw">
      </div>
      <div class="bar-btn">
        <button class="btn ok-btn" id="send-goal">Send Goal</button>
        <button class="btn danger" id="cancel-goal">Cancel Goal</button>
      </div>
    </div>
    <div class="control-grid" style="margin-top:8px">
      <button class="btn warn-btn" id="btn-pause">⏸ Pause task</button>
      <button class="btn ok-btn" id="btn-resume-task">▶ Resume task</button>
      <button class="btn danger" id="btn-estop2">Emergency Stop</button>
    </div>
    <div class="event-list" style="margin-top:12px;font-size:12px;color:var(--muted)">
      <div>Speed: ${r.speed && r.speed.lin != null ? r.speed.lin.toFixed(2) + ' m/s · ' + r.speed.ang.toFixed(2) + ' rad/s' : '—'}</div>
      <div>Battery: ${r.battery == null ? '—' : r.battery.toFixed(0) + '% ' + (r.charging ? '(charging)' : '')}</div>
      <div>Obstacle: ${r.scan_min == null ? '—' : (r.obstacle ? `<span style="color:var(--bad)">${r.scan_min.toFixed(2)} m (STOP)</span>` : `${r.scan_min.toFixed(2)} m`)}</div>
      <div>State: ${r.estop ? 'ESTOP' : r.paused ? 'PAUSED' : r.disabled ? 'DISABLED' : r.status}</div>
    </div>`;
}

function refreshRobotControls() {
  renderRobots();
  wireControls();
}

// ═══════════════════════════════════════════════════════════════
// Tasks tab
// ═══════════════════════════════════════════════════════════════

function renderTasks() {
  const panel = $('tab-tasks');
  const t = state.tasks;
  const counts = t.counts;
  const rows = t.list.slice().reverse().map((task) => {
    const prio = tag(PRIO_LABEL[task.priority] ? (task.priority === 2 ? 'warn' : 'info') : 'muted', PRIO_LABEL[task.priority] || task.priority);
    const st = TASK_STATUS_TAG[task.status] || 'muted';
    return `<tr data-task="${task.id}">
      <td><b>${task.id}</b></td><td>${tag(st, task.status)}</td>
      <td>${prio}</td><td>${task.robot || '—'}</td>
      <td>${task.pickup ? task.pickup.map((v) => +v.toFixed(1)).join(', ') : '—'}</td>
      <td>${task.dropoff ? task.dropoff.map((v) => +v.toFixed(1)).join(', ') : '—'}</td>
      <td>
        <button class="btn" data-task-act="cancel">cancel</button>
        <button class="btn" data-task-act="delete">delete</button>
        <select class="prio-sel" data-task-id="${task.id}">
          <option value="0" ${task.priority === 0 ? 'selected' : ''}>Low</option>
          <option value="1" ${task.priority === 1 ? 'selected' : ''}>Normal</option>
          <option value="2" ${task.priority === 2 ? 'selected' : ''}>High</option>
        </select>
      </td></tr>`;
  }).join('');

  const assignOpts = state.robots.map((r) => `<option value="${r.id}">${r.id}</option>`).join('');

  panel.innerHTML = `
    <div class="grid grid-2">
      <div class="card">
        <h3>Create task <span class="hint">pickup → dropoff</span></h3>
        <div class="grid-2eq" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr 1fr;gap:6px">
          <div class="field"><label>Task ID (auto)</label><input id="task-id" placeholder="T123"></div>
          <div class="field"><label>Pickup X</label><input id="task-px" type="number" value="2"></div>
          <div class="field"><label>Pickup Y</label><input id="task-py" type="number" value="2"></div>
          <div class="field"><label>Dropoff X</label><input id="task-dx" type="number" value="-2"></div>
          <div class="field"><label>Dropoff Y</label><input id="task-dy" type="number" value="-2"></div>
          <div class="field"><label>Priority</label>
            <select id="task-priority"><option value="0">Low</option><option value="1" selected>Normal</option><option value="2">High</option></select>
          </div>
        </div>
        <div class="grid-2eq" style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
          <div class="field"><label>Required payload (kg)</label><input id="task-payload" type="number" value="0"></div>
          <div class="field" style="display:flex;align-items:flex-end">
            <button class="btn ok-btn wide" id="create-task" style="width:100%">Create Task</button>
          </div>
        </div>
      </div>
      <div class="card">
        <h3>Assign</h3>
        <div class="grid-2eq" style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
          <div class="field"><label>Task</label><input id="assign-task" placeholder="task id"></div>
          <div class="field"><label>Robot</label><select id="assign-robot">${assignOpts || '<option value="">—</option>'}</select></div>
        </div>
        <div class="bar-btn">
          <button class="btn ok-btn" id="assign-btn">Assign manually</button>
          <button class="btn" id="auto-btn">Return to automatic</button>
        </div>
      </div>
    </div>
    <h2 class="section">Task queue &amp; history
      <span class="hint" style="float:right">pending ${counts.pending} · running ${counts.running} · completed ${counts.completed} · cancelled ${counts.cancelled}</span>
    </h2>
    <div class="card"><table>
      <thead><tr><th>Task</th><th>Status</th><th>Priority</th><th>Robot</th><th>Pickup</th><th>Dropoff</th><th>Actions</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="empty">No tasks yet — create one above</td></tr>'}</tbody>
    </table></div>
    ${state.control_center && state.control_center.backend_url ? `
      <h2 class="section">Backend task history <span class="hint">persistent</span></h2>
      <div class="card"><table>
        <thead><tr><th>Task</th><th>Status</th><th>Robot</th><th>Pickup</th><th>Dropoff</th></tr></thead>
        <tbody>${(backendCache.tasks || []).map((t) => `<tr>
          <td><b>${escapeHtml(t.task_id)}</b></td><td>${tag(TASK_STATUS_TAG[t.status] || 'muted', t.status)}</td>
          <td>${t.robot_id || '—'}</td>
          <td>${t.pickup_x}, ${t.pickup_y}</td><td>${t.dropoff_x}, ${t.dropoff_y}</td></tr>`).join('')
          || '<tr><td colspan="5" class="empty">no backend tasks</td></tr>'}</tbody>
      </table></div>` : ''}`;
}

// ═══════════════════════════════════════════════════════════════
// Fleet tab
// ═══════════════════════════════════════════════════════════════

function renderFleet() {
  const panel = $('tab-fleet');
  const rows = state.robots.map((r) => {
    const hb = r.heartbeat_age == null ? '—' : r.heartbeat_age.toFixed(1) + 's';
    const res = r.reservation ? `${r.reservation.task_id} (seg ${r.reservation.segment_index + 1}/${r.reservation.total_segments})` : '—';
    return `<tr data-fleet="${r.id}">
      <td><b>${r.id}</b></td><td>${tag(r.status === 'OFFLINE' ? 'bad' : 'ok', r.status)}</td>
      <td>${r.charging ? tag('warn', 'charging') : '—'}</td>
      <td>${hb}</td><td>${r.current_task || '—'}</td>
      <td>${res}</td><td>${r.robot_type || '—'}</td>
      <td>${r.localization}</td><td>${r.payload_capacity == null ? '—' : r.payload_capacity + ' kg'}</td>
      <td>
        <button class="btn" data-fleet-act="enable">enable</button>
        <button class="btn danger" data-fleet-act="disable">disable</button>
        <button class="btn warn-btn" data-fleet-act="drain">drain</button>
        <button class="btn ok-btn" data-fleet-act="recharge">recharge</button>
        <button class="btn" data-fleet-act="restart">restart</button>
        <button class="btn" data-fleet-act="reconnect">reconnect</button>
      </td></tr>`;
  }).join('');
  panel.innerHTML = `
    <div class="card">
      <h3>Fleet management <span class="hint">disable/enable are bridge-level · drain/recharge are battery simulations · restart triggers a beacon reboot</span></h3>
      <table>
        <thead><tr><th>Robot</th><th>Status</th><th>Charging</th><th>Heartbeat</th><th>Task</th><th>Reservation</th><th>Type</th><th>Localization</th><th>Capacity</th><th>Actions</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="10" class="empty">No robots registered</td></tr>'}</tbody>
      </table>
    </div>`;
}

// ═══════════════════════════════════════════════════════════════
// Events tab
// ═══════════════════════════════════════════════════════════════

let eventFilter = { type: '', severity: '', q: '' };

async function renderEvents() {
  const panel = $('tab-events');
  const types = [...new Set(state.events.map((e) => e.type))].sort();
  panel.innerHTML = `
    <div class="filter-bar">
      <input id="ev-q" placeholder="search…" value="${eventFilter.q}" style="min-width:220px">
      <select id="ev-type"><option value="">all types</option>${types.map((t) => `<option ${eventFilter.type === t ? 'selected' : ''}>${t}</option>`).join('')}</select>
      <select id="ev-sev"><option value="">all severities</option>${['info', 'warning', 'high', 'critical'].map((s) => `<option value="${s}" ${eventFilter.severity === s ? 'selected' : ''}>${s}</option>`).join('')}</select>
      <button class="btn" id="ev-apply">Apply</button>
      <button class="btn ok-btn" id="ev-export-csv">Export CSV</button>
      <button class="btn" id="ev-export-json">Export JSON</button>
    </div>
    <div class="card"><div class="event-list" id="ev-list"></div></div>`;
  const list = $('ev-list');
  const rows = state.events.filter((e) => {
    if (eventFilter.type && e.type !== eventFilter.type) return false;
    if (eventFilter.severity && e.severity !== eventFilter.severity) return false;
    if (eventFilter.q && !(e.message + ' ' + e.type + ' ' + e.robot + ' ' + e.severity).toLowerCase().includes(eventFilter.q.toLowerCase())) return false;
    return true;
  });
  list.innerHTML = rows.map((e) =>
    `<div class="event-row"><span class="ts">${fmtTs(e.ts)}</span>${tag(e.severity, e.severity)}<span class="evtype">${e.type}</span><span class="msg">${escapeHtml(e.message)} ${e.robot ? '<span style="color:var(--muted)">· ' + escapeHtml(e.robot) + '</span>' : ''}</span></div>`
  ).join('') || '<div class="empty">No matching events</div>';

  const be = state.control_center && state.control_center.backend_url;
  if (be) {
    const hist = backendCache.events || [];
    const h = hist.map((e) =>
      `<div class="event-row"><span class="ts">${fmtTs(e.ts)}</span>${tag(e.severity, e.severity)}<span class="evtype">${e.type}</span><span class="msg">${escapeHtml(e.message)}</span></div>`).join('');
    const sec = document.createElement('div');
    sec.innerHTML = `<h2 class="section">Backend history <span class="hint">persistent · ${hist.length} records</span></h2>
      <div class="event-list">${h || '<div class="empty">no backend history</div>'}</div>`;
    list.parentElement.appendChild(sec);
  }
}

// ═══════════════════════════════════════════════════════════════
// Alerts tab
// ═══════════════════════════════════════════════════════════════

function renderAlerts() {
  const panel = $('tab-alerts');
  const active = state.alerts.active;
  const history = state.alerts.history;
  const item = (a, ack) => `
    <div class="alert-item sev-${a.severity}">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div><b>${escapeHtml(a.title)}</b> ${tag(a.severity, a.severity)} ${a.robot ? tag('info', a.robot) : ''}
          <div style="color:var(--muted);margin-top:2px">${escapeHtml(a.message)}</div>
        </div>
        <div style="text-align:right;color:var(--muted);font-size:11px">
          <div>${fmtTs(a.ts)}</div>${ack ? `<button class="btn" data-ack="${a.id}">acknowledge</button>` : `<div style="color:var(--ok)">cleared ${fmtTs(a.cleared_ts)}</div>`}
        </div>
      </div>
    </div>`;
  panel.innerHTML = `
    <h2 class="section">Active alerts (${active.length})</h2>
    ${active.map((a) => item(a, true)).join('') || '<div class="card empty">No active alerts</div>'}
    <h2 class="section">Alert history</h2>
    ${history.map((a) => item(a, false)).join('') || '<div class="empty">No alert history</div>'}
    ${state.control_center && state.control_center.backend_url ? `
      <h2 class="section">Backend alert history <span class="hint">persistent</span></h2>
      ${(backendCache.alerts || []).map((a) => item(a, false)).join('')
        || '<div class="empty">no backend alerts</div>'}` : ''}`;
}

// ═══════════════════════════════════════════════════════════════
// Settings tab
// ═══════════════════════════════════════════════════════════════

function renderMonitoring() {
  const panel = $('tab-monitoring');
  const cc = state.control_center || {};
  if (!cc.backend_url) {
    panel.innerHTML = '<div class="card empty">Monitoring is available when a production backend is configured ' +
      '(Settings → Production backend → set the backend URL).</div>';
    return;
  }
  const m = backendCache.monitoring || {};
  const live = m.live || {};
  const comp = backendCache.components || {};
  const kpi = (v, l) => `<div class="kpi"><div class="v accent">${v ?? '—'}</div><div class="l">${l}</div></div>`;
  const compRows = Object.entries(comp).map(([k, v]) =>
    `<tr><td><b>${k}</b></td><td>${v && v.ok !== false ? tag('ok', v.status || 'ok') : tag('bad', 'down')}</td>
     <td style="color:var(--muted)">${v && v.latest ? 'robots=' + v.latest.robot_count : (v && v.url) || ''}</td></tr>`).join('');
  panel.innerHTML = `
    <div class="kpi-row">
      ${kpi(live.cpu_percent != null ? live.cpu_percent + '%' : '—', 'API CPU')}
      ${kpi(live.rss_mb != null ? live.rss_mb + ' MB' : '—', 'API memory')}
      ${kpi(live.system_memory_percent != null ? live.system_memory_percent + '%' : '—', 'Host memory')}
      ${kpi(live.requests_total != null ? live.requests_total : '—', 'API requests')}
      ${kpi(live.ws_clients != null ? live.ws_clients : '—', 'WS clients')}
      ${kpi(live.threads != null ? live.threads : '—', 'API threads')}
    </div>
    <div class="grid grid-2eq">
      <div class="card"><h3>Components</h3><table>
        <thead><tr><th>component</th><th>status</th><th>detail</th></tr></thead>
        <tbody>${compRows || '<tr><td colspan="3" class="empty">no data</td></tr>'}</tbody></table></div>
      <div class="card"><h3>System</h3><div style="color:var(--muted);line-height:1.8">
        Dashboard refresh: ${(state.settings.dashboard.refresh_rate || 1)}s<br>
        Backend: <b>${cc.backend_ok ? 'connected' : 'offline'}</b> (${cc.backend_url})<br>
        Push period: ${cc.push_period}s · Uptime backend service reports live health.
      </div></div>
    </div>`;
}

function renderSettings() {
  const panel = $('tab-settings');
  const groups = state.settings;
  const labels = {
    planner_weights: 'Planner weights', traffic: 'Traffic', battery: 'Battery',
    fleet: 'Fleet', analytics: 'Analytics', alerts: 'Alerts', manual: 'Manual control',
    dashboard: 'Dashboard', stations: 'Charging stations', homes: 'Home positions',
    backend: 'Production backend',
  };
  const html = Object.keys(groups).map((g) => {
    const grp = groups[g];
    const rows = Object.keys(grp).filter((k) => k !== 'node').map((k) => {
      const v = grp[k];
      if (g === 'stations' || g === 'homes') {
        return `<div class="settings-row"><span class="key">${k}</span>
          <span><input class="set-vec" data-group="${g}" data-key="${k}" type="text" value="${v[0]}, ${v[1]}"></span>
          <button class="btn ok-btn" data-set="${g}|${k}">apply</button></div>`;
      }
      const input = typeof v === 'boolean'
        ? `<select class="set-in" data-group="${g}" data-key="${k}"><option value="true" ${v ? 'selected' : ''}>true</option><option value="false" ${v ? '' : 'selected'}>false</option></select>`
        : `<input class="set-in" data-group="${g}" data-key="${k}" type="${typeof v === 'number' ? 'number' : 'text'}" step="any" value="${v}">`;
      return `<div class="settings-row"><span class="key">${k}</span><span>${input}</span>
        <button class="btn ok-btn" data-set="${g}|${k}">apply</button></div>`;
    }).join('');
    return `<div class="settings-group"><h4>${labels[g] || g} <span style="float:right;font-weight:400;text-transform:none">→ ${grp.node}</span></h4>${rows}</div>`;
  }).join('');
  panel.innerHTML = `<div class="grid-2eq"><div class="card"><h3>Runtime settings <span class="hint">applied to live nodes</span></h3>${html}</div>
    <div class="card"><h3>About</h3>
      <div style="color:var(--muted);line-height:1.7">
        Control Center backend bridge observes the existing ROS2 graph and publishes operator
        commands only to existing control topics. Settings with node <b>fleet_manager</b> or
        <b>analytics</b> are applied live through the ROS parameter service.
      </div></div></div>`;
}

// ═══════════════════════════════════════════════════════════════
// Wire-up (delegated event handling)
// ═══════════════════════════════════════════════════════════════

function wireControls() {
  document.querySelectorAll('[data-cmd]').forEach((b) => {
    b.onclick = () => {
      const cmd = JSON.parse(b.dataset.cmd);
      cmd.robot = selectedRobot;
      doCommand(cmd);
    };
  });
  const goalBtn = $('send-goal');
  if (goalBtn) goalBtn.onclick = () => doCommand({
    action: 'goal', robot: selectedRobot,
    x: parseFloat($('goal-x').value || 0), y: parseFloat($('goal-y').value || 0),
    yaw: parseFloat($('goal-yaw').value || 0),
  }, 'Goal sent');
  const cg = $('cancel-goal');
  if (cg) cg.onclick = () => doCommand({ action: 'cancel_goal', robot: selectedRobot }, 'Goal cancelled');
  const p = $('btn-pause'); if (p) p.onclick = () => doCommand({ action: 'pause', robot: selectedRobot });
  const rt = $('btn-resume-task'); if (rt) rt.onclick = () => doCommand({ action: 'resume_task', robot: selectedRobot });
  const e2 = $('btn-estop2'); if (e2) e2.onclick = () => doCommand({ action: 'estop', robot: selectedRobot });
  // Map toolbar (guarded — canvas/buttons live in the Map tab).
  const zi = $('map-zoom-in'); if (zi) zi.onclick = () => mapView && mapView.zoomBy(1.2);
  const zo = $('map-zoom-out'); if (zo) zo.onclick = () => mapView && mapView.zoomBy(1 / 1.2);
  const ac = $('map-autocenter'); if (ac) ac.onclick = () => mapView && mapView.autoCenter();
}

function wireTasks() {
  const ct = $('create-task');
  if (ct) ct.onclick = async () => {
    const payload = {
      action: 'create_task',
      task_id: $('task-id').value || undefined,
      px: parseFloat($('task-px').value || 0), py: parseFloat($('task-py').value || 0),
      dx: parseFloat($('task-dx').value || 0), dy: parseFloat($('task-dy').value || 0),
      priority: parseInt($('task-priority').value, 10),
      required_payload: parseFloat($('task-payload').value || 0),
    };
    const res = await doCommand(payload, 'Task created');
    if (res && res.ok && res.task_id) $('task-id').value = res.task_id;
  };
  document.querySelectorAll('[data-task-act]').forEach((b) => {
    b.onclick = () => {
      const tr = b.closest('tr');
      const taskId = tr.dataset.task;
      doCommand({ action: b.dataset.taskAct === 'cancel' ? 'cancel_task' : 'delete_task', task_id: taskId });
    };
  });
  document.querySelectorAll('.prio-sel').forEach((s) => {
    s.onchange = () => doCommand({ action: 'set_priority', task_id: s.dataset.taskId, priority: parseInt(s.value, 10) });
  });
  const ab = $('assign-btn');
  if (ab) ab.onclick = () => doCommand({ action: 'assign_robot', task_id: $('assign-task').value, robot: $('assign-robot').value });
  const au = $('auto-btn');
  if (au) au.onclick = () => doCommand({ action: 'automatic' });
}

function wireFleet() {
  document.querySelectorAll('[data-fleet-act]').forEach((b) => {
    b.onclick = () => {
      const tr = b.closest('tr');
      const robot = tr.dataset.fleet;
      const act = b.dataset.fleetAct;
      doCommand({ action: act, robot });
    };
  });
}

function wireEvents() {
  const apply = $('ev-apply');
  if (apply) apply.onclick = () => {
    eventFilter = { type: $('ev-type').value, severity: $('ev-sev').value, q: $('ev-q').value };
    renderEvents();
  };
  const csv = $('ev-export-csv');
  if (csv) csv.onclick = () => {
    const params = new URLSearchParams({ format: 'csv', ...eventFilter });
    location.href = '/api/events/export?' + params.toString();
  };
  const jsonexp = $('ev-export-json');
  if (jsonexp) jsonexp.onclick = () => {
    const blob = new Blob([JSON.stringify(state.events, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'warehouse_events.json';
    a.click();
  };
}

function wireAlerts() {
  document.querySelectorAll('[data-ack]').forEach((b) => {
    b.onclick = async () => { await ackAlert(b.dataset.ack); toast('Alert acknowledged'); };
  });
}

function wireSettings() {
  document.querySelectorAll('[data-set]').forEach((b) => {
    b.onclick = async () => {
      const [group, key] = b.dataset.set.split('|');
      let value;
      if (group === 'stations' || group === 'homes') {
        value = document.querySelector(`.set-vec[data-key="${key}"]`).value.split(',').map((s) => parseFloat(s));
      } else {
        const inp = document.querySelector(`.set-in[data-group="${group}"][data-key="${key}"]`);
        value = inp.dataset.group === 'dashboard' && inp.type === 'select-one'
          ? inp.value === 'true'
          : (inp.type === 'number' ? parseFloat(inp.value) : inp.value);
        if (inp.tagName === 'SELECT' && !inp.type) value = inp.value === 'true';
      }
      const res = await setSetting(group, key, value);
      if (res && res.ok) toast(`Applied ${group}.${key} = ${JSON.stringify(value)}`);
      else toast((res && res.error) || 'Failed to apply setting', true);
    };
  });
}

// ═══════════════════════════════════════════════════════════════
// Render dispatch (preserving form values between updates)
// ═══════════════════════════════════════════════════════════════

let lastBackendRefresh = 0;
function render() {
  if (!state) return;
  renderTop();
  const now = Date.now();
  if (now - lastBackendRefresh > 5000) {
    lastBackendRefresh = now;
    refreshBackend();
  }
  const focusedId = document.activeElement && document.activeElement.id;
  const formState = {};
  document.querySelectorAll('input,select,textarea').forEach((el) => {
    if (el.id) formState[el.id] = el.value;
  });
  if (activeTab === 'dashboard') renderDashboard();
  if (activeTab === 'robots') renderRobots();
  if (activeTab === 'tasks') renderTasks();
  if (activeTab === 'fleet') renderFleet();
  if (activeTab === 'map') renderMap();
  if (activeTab === 'events') renderEvents();
  if (activeTab === 'alerts') renderAlerts();
  if (activeTab === 'monitoring') renderMonitoring();
  if (activeTab === 'settings') renderSettings();
  document.querySelectorAll('input,select,textarea').forEach((el) => {
    if (el.id && formState[el.id] !== undefined && el.id !== focusedId) el.value = formState[el.id];
  });
  wireControls();
  wireTasks();
  wireFleet();
  wireEvents();
  wireAlerts();
  wireSettings();
}

// ═══════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════

function fmtTs(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ═══════════════════════════════════════════════════════════════
// Boot
// ═══════════════════════════════════════════════════════════════

document.querySelectorAll('.tab').forEach((b) => {
  b.onclick = () => switchTab(b.dataset.tab);
});
document.addEventListener('click', (e) => {
  const row = e.target.closest('[data-robot]');
  if (row) {
    selectedRobot = row.dataset.robot;
    if (mapView) mapView.select(selectedRobot);
    if (activeTab === 'robots') renderRobots();
    toast('Selected robot ' + selectedRobot);
  }
});

async function boot() {
  // Initial fetch for ws port + immediate first paint.
  try {
    const initial = await getJSON('/api/state');
    state = initial;
    const wsPort = initial.control_center.ws_port;
    const refresh = (initial.settings.dashboard && initial.settings.dashboard.refresh_rate) || 1;
    feed = createStateFeed({
      wsPort,
      refreshRate: refresh,
      onState: (s) => { state = s; render(); },
      onConn: (kind, ok) => {
        const el = $('conn');
        el.textContent = ok ? `live (${kind})` : 'connecting…';
        el.className = 'conn ' + (ok ? 'live' : 'dead');
      },
    });
    feed.start();
  } catch (_) {
    $('conn').textContent = 'offline';
    $('conn').className = 'conn dead';
    setTimeout(boot, 2000);
  }
  render();
  setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString(); }, 1000);
}

boot();
