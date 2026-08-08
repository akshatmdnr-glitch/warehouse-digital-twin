// High-fidelity 2D Digital Twin of the Gazebo warehouse.
//
// This is a true top-down map of the warehouse that exists in Gazebo
// (worlds/warehouse.world.sdf). It uses the SAME world coordinate system as
// Gazebo (metres, origin at the centre of the 20 m x 20 m floor). The static
// warehouse geometry (racks A1..C3, walls, aisles, stations, pallets, crates,
// loose packages, loading dock) is rendered from the world file, and live ROS
// state (robot poses, planned paths, tasks, packages) is overlaid on top.
//
// The renderer only DRAWS the state it receives — it never simulates,
// teleports or replaces anything. Robot positions are the live ROS/Gazebo
// poses with smooth interpolation between updates.

const ROBOT_COLORS = ['#f2a65a', '#c96a4a', '#d4a24f', '#f5c542', '#a08c74'];
const RACK_ROWS = ['A', 'B', 'C'];
const RACK_COLS = [1, 2, 3];

// ─────────────────────────────────────────────────────────────────────────
// Static warehouse geometry — mirrored from worlds/warehouse.world.sdf.
// Units are metres in the Gazebo world frame (x east, y north).
// ─────────────────────────────────────────────────────────────────────────

const WORLD = {
  size: 20,               // 20 m x 20 m floor, walls at +/- 10 m
  // Dark operational canvas — robots, paths, reservations and overlays read
  // best against a dark background (the UI outside the map is cream).
  floor: '#161d26',       // dark slate floor
  aisle: '#1a222c',       // slightly lighter aisle
  wall: '#39424e',        // concrete wall
  lane: '#c9a83a',        // amber floor lane markings

  racks: (() => {
    const list = [];
    // Rack footprint 1.96 x 0.96, centred at the grid positions from the world.
    // Row A is north (y=+3), row C is south (y=-3).
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 3; c++) {
        const x = (c - 1) * 4;   // -4, 0, 4
        const y = (1 - r) * 3;   // 3, 0, -3
        list.push({
          id: `${RACK_ROWS[r]}${RACK_COLS[c]}`,
          x, y, w: 1.96, h: 0.96,
        });
      }
    }
    return list;
  })(),

  // loading dock + dock pallets / crate (east wall)
  dock: { x: 8.5, y: 0, w: 2.0, h: 3.0 },
  dock_pallets: [{ x: 8.5, y: -0.7 }, { x: 8.5, y: 0.7 }],
  dock_crate: { x: 8.5, y: 0 },

  // permanent logistics stations (south zone) — delivery only, no pickup area
  dropoff_station: { x: 2, y: -7, size: 1.4, label: 'SHIPPING DOCK' },

  // charging pads
  charging: [
    { id: 'charging_north', x: 0, y: 8 },
    { id: 'charging_south', x: 0, y: -8 },
  ],

  // off-lane static props
  pallets: [
    { x: 7, y: 8 }, { x: -7, y: 8 }, { x: 6, y: -8 }, { x: -6, y: -8 },
  ],
  crates: [
    { x: 7, y: 6 }, { x: -7, y: 6 }, { x: 7, y: -6 }, { x: -7, y: -6 },
  ],
  packages: [
    { x: 3, y: -6 }, { x: -3, y: -6 }, { x: 9.4, y: -5 }, { x: -9.4, y: -5 },
  ],
};

// Robot state labels for the status chip — rendered from the backend's
// authoritative execution state (exec_state). The frontend never invents a
// state: it only formats what the execution engine reported.
const ROBOT_STATUS_LABEL = (r) => {
  const es = r.exec_state || '';
  if (es) return es;
  // Fallback (only until the first beacon registration carries exec_state).
  if (r.charging) return 'CHARGING';
  if (r.status === 'OFFLINE') return 'OFFLINE';
  if (r.estop) return 'ESTOP';
  if (!r.current_task) return 'IDLE';
  return 'UNKNOWN';
};

// Body color per execution state — warm operational palette.
const ROBOT_STATE_COLOR = (r) => {
  const es = r.exec_state || '';
  switch (es) {
    case 'CHARGING': return '#7fc97f';        // olive green (physically on pad)
    case 'OFFLINE': return '#d98a8a';         // soft red
    case 'IDLE': return '#c8b494';            // muted beige
    case 'MOVING_TO_PICKUP':
    case 'CARRYING':
    case 'MOVING_TO_DROPOFF': return '#f2a65a'; // amber (driving)
    case 'PICKING':
    case 'DROPPING': return '#c96a4a';        // terracotta (handling)
    case 'PLANNING': return '#e0b46a';        // pale amber (computing path)
    case 'ASSIGNED': return '#d4a24f';        // golden (task queued)
    case 'RETURNING': return '#d4a24f';       // golden (heading back)
    default: break;
  }
  if (r.estop) return '#e8484f';              // deep warm red (estop)
  if (r.status === 'OFFLINE') return '#d98a8a';
  if (r.charging) return '#7fc97f';
  return ROBOT_COLORS[0];
};

export class MapView {
  constructor(canvas, onSelect) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.onSelect = onSelect || (() => {});
    this.scale = 10;          // pixels per metre
    this.ox = canvas.width / 2;
    this.oy = canvas.height / 2;
    this.state = null;
    this.selected = null;
    this.dragging = false;
    this.lastX = 0;
    this.lastY = 0;
    this.fitDirty = true;

    // smooth-animation state
    this._anim = { x: this.ox, y: this.oy, scale: this.scale };
    this._raf = null;
    this._lastFrame = 0;
    this._smooth = {};        // robot_id -> {x,y,yaw} interpolated pose
    this._pkg = {};           // task_id -> package phase state

    // Static warehouse layer — painted once and only repainted when the view
    // transform changes (pan / zoom / fit) or the canvas is resized. It is
    // NEVER repainted per frame or per state update.
    this._world = null;
    this._worldDirty = true;

    this._bind();
    this._tick();
  }

  // ── Canvas / coordinate helpers ───────────────────────────────────────

  _toCanvas(clientX, clientY) {
    const rect = this.cv.getBoundingClientRect();
    const kx = rect.width ? this.cv.width / rect.width : 1;
    const ky = rect.height ? this.cv.height / rect.height : 1;
    return [(clientX - rect.left) * kx, (clientY - rect.top) * ky];
  }

  _w2s(x, y) { return [this.ox + x * this.scale, this.oy + y * this.scale]; }

  _worldTransform() {
    return { scale: this.scale, ox: this.ox, oy: this.oy };
  }

  _worldKey() {
    // Canvas size only — the offscreen is repainted when the view changes.
    return `${Math.round(this.cv.width)}x${Math.round(this.cv.height)}`;
  }

  _ensureWorld() {
    // Repaint the static layer only when it is missing, the canvas was
    // resized, or the view transform changed (flagged by pan/zoom/fit).
    const key = this._worldKey();
    if (!this._world) {
      this._world = document.createElement('canvas');
      this._world.width = this.cv.width;
      this._world.height = this.cv.height;
      this._worldKeyUsed = null;
    }
    if (this._worldDirty || this._worldKeyUsed !== key) {
      this._worldKeyUsed = key;
      this._worldDirty = false;
      this._paintWorld(this._world.getContext('2d'));
    }
    return this._world;
  }

  _invalidateWorld() {
    this._worldDirty = true;
  }

  _paintWorld(wctx) {
    // Draw the whole static warehouse under the current view transform.
    wctx.clearRect(0, 0, this._world.width, this._world.height);
    const half = WORLD.size / 2;
    const [x0, y0] = this._w2s(-half, -half);
    const [x1, y1] = this._w2s(half, half);

    // floor
    wctx.fillStyle = WORLD.floor;
    wctx.fillRect(x0, y0, x1 - x0, y1 - y0);

    // subtle metre grid (aisle guides)
    wctx.strokeStyle = 'rgba(120,110,90,0.12)';
    wctx.lineWidth = 1;
    for (let g = -9; g <= 9; g++) {
      const [gx, gy] = this._w2s(g, -half);
      wctx.beginPath(); wctx.moveTo(gx, gy); wctx.lineTo(gx, y1); wctx.stroke();
      const [hx, hy] = this._w2s(-half, g);
      wctx.beginPath(); wctx.moveTo(hx, hy); wctx.lineTo(x1, hy); wctx.stroke();
    }

    // yellow lane markings (from floor_markings)
    wctx.fillStyle = WORLD.lane;
    const laneW = 0.08;
    const mark = (x, y, w, h) => {
      const [sx, sy] = this._w2s(x, y);
      wctx.fillRect(sx - w * this.scale / 2, sy - h * this.scale / 2, w * this.scale, h * this.scale);
    };
    mark(0, 2.5, 19.5, laneW);
    mark(0, -2.5, 19.5, laneW);
    mark(7.5, 0, laneW, 20);
    mark(-7.5, 0, laneW, 20);

    this._drawWalls(wctx);
    this._drawRacks(wctx);
    this._drawDock(wctx);
    this._drawStations(wctx);
    this._drawProps(wctx);
  }

  _renderWorld(ctx) {
    // Draw the (cached) static layer onto the visible canvas.
    ctx.drawImage(this._ensureWorld(), 0, 0);
  }

  _drawWalls(ctx) {
    const [x0, y0] = this._w2s(-10, -10);
    const [x1, y1] = this._w2s(10, 10);
    ctx.fillStyle = WORLD.wall;
    ctx.fillRect(x0, y0, x1 - x0, Math.max(2, 0.2 * this.scale));   // north
    ctx.fillRect(x0, y1 - Math.max(2, 0.2 * this.scale), x1 - x0, Math.max(2, 0.2 * this.scale)); // south
    ctx.fillRect(x0, y0, Math.max(2, 0.2 * this.scale), y1 - y0);   // west
    ctx.fillRect(x1 - Math.max(2, 0.2 * this.scale), y0, Math.max(2, 0.2 * this.scale), y1 - y0); // east
  }

  _drawRacks(ctx) {
    for (const rack of WORLD.racks) {
      const [sx, sy] = this._w2s(rack.x, rack.y);
      const w = rack.w * this.scale, h = rack.h * this.scale;
      ctx.fillStyle = '#2c3642';
      ctx.strokeStyle = '#46525f';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.rect(sx - w / 2, sy - h / 2, w, h); ctx.fill(); ctx.stroke();
      // shelf slot ticks
      ctx.fillStyle = '#5a6673';
      for (let s = -0.45; s <= 0.45; s += 0.45) {
        const ty = sy + s * this.scale;
        ctx.fillRect(sx - w / 2 + 2, ty - 1, w - 4, 2);
      }
      // label
      ctx.fillStyle = '#c9c4b8';
      ctx.font = `${Math.max(10, Math.min(16, 11 * (this.scale / 10)))}px sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(rack.id, sx, sy);
    }
  }

  _drawDock(ctx) {
    const d = WORLD.dock;
    const [sx, sy] = this._w2s(d.x, d.y);
    const w = d.w * this.scale, h = d.h * this.scale;
    ctx.fillStyle = '#3a4350';
    ctx.strokeStyle = '#4f5b69';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.rect(sx - w / 2, sy - h / 2, w, h); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#c9c4b8'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('LOADING DOCK', sx, sy);
    // dock pallets + crate
    for (const p of WORLD.dock_pallets) {
      const [px, py] = this._w2s(p.x, p.y);
      ctx.fillStyle = '#7a6a48';
      ctx.fillRect(px - 0.5 * this.scale, py - 0.5 * this.scale, 1.0 * this.scale, 1.0 * this.scale);
    }
    const [cx, cy] = this._w2s(WORLD.dock_crate.x, WORLD.dock_crate.y);
    ctx.fillStyle = '#b07a4a';
    ctx.fillRect(cx - 0.28 * this.scale, cy - 0.28 * this.scale, 0.56 * this.scale, 0.56 * this.scale);
  }

  _drawStations(ctx) {
    // Fixed delivery stations only — there is NO permanent pickup area.
    // Inventory lives on the racks; pickup markers appear only when a task
    // is created (see _drawTaskMarkers).
    const stations = [
      { id: 'Shipping Dock', x: 2, y: -7 },
      { id: 'Packing Station', x: -2, y: -7 },
      { id: 'Loading Dock', x: 7, y: 0 },
    ];
    for (const st of stations) {
      const [sx, sy] = this._w2s(st.x, st.y);
      const r = (WORLD.dropoff_station.size / 2) * this.scale;
      ctx.fillStyle = 'rgba(201,154,63,0.10)';       // warm delivery tint
      ctx.strokeStyle = '#b98a5a';                    // warm border
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.rect(sx - r, sy - r, r * 2, r * 2); ctx.fill(); ctx.stroke();
      // Label rendered inside the station box so adjacent stations never
      // overlap. Font auto-fits the square width.
      const maxChars = 13;
      const short = st.id.length > maxChars ? st.id.slice(0, maxChars - 1) + '…' : st.id;
      const fit = Math.max(8, Math.min(13, (r * 2) / Math.max(short.length * 0.62, 1)));
      ctx.fillStyle = '#e0c9a0';
      ctx.font = `${fit}px sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(short, sx, sy);
    }
    // charging pads (olive green — part of the warm palette)
    for (const c of WORLD.charging) {
      const [sx, sy] = this._w2s(c.x, c.y);
      const r = 0.5 * this.scale;
      ctx.fillStyle = 'rgba(125,154,106,0.16)';
      ctx.strokeStyle = '#7d9a6a';
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#a3c091'; ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('⚡', sx, sy + 3);
    }
  }

  _drawProps(ctx) {
    for (const p of WORLD.pallets) {
      const [sx, sy] = this._w2s(p.x, p.y);
      ctx.fillStyle = '#8a7a58';
      ctx.strokeStyle = '#6b5e42';
      ctx.fillRect(sx - 0.6 * this.scale, sy - 0.6 * this.scale, 1.2 * this.scale, 1.2 * this.scale);
      ctx.strokeRect(sx - 0.6 * this.scale, sy - 0.6 * this.scale, 1.2 * this.scale, 1.2 * this.scale);
    }
    for (const c of WORLD.crates) {
      const [sx, sy] = this._w2s(c.x, c.y);
      ctx.fillStyle = '#b07a4a';
      ctx.fillRect(sx - 0.3 * this.scale, sy - 0.3 * this.scale, 0.6 * this.scale, 0.6 * this.scale);
    }
    for (const p of WORLD.packages) {
      const [sx, sy] = this._w2s(p.x, p.y);
      ctx.fillStyle = '#d97706';
      ctx.fillRect(sx - 0.2 * this.scale, sy - 0.2 * this.scale, 0.4 * this.scale, 0.4 * this.scale);
    }
  }

  // ── Input / interaction (Google-Maps style zoom & pan) ────────────────

  _bind() {
    this.cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      const [cx, cy] = this._toCanvas(e.clientX, e.clientY);
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      this._zoomAt(cx, cy, factor);
    }, { passive: false });

    this.cv.addEventListener('mousedown', (e) => {
      this.dragging = true;
      const [cx, cy] = this._toCanvas(e.clientX, e.clientY);
      this.lastX = cx; this.lastY = cy;
      this.cv.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', (e) => {
      if (!this.dragging) return;
      const [cx, cy] = this._toCanvas(e.clientX, e.clientY);
      this.ox += cx - this.lastX;
      this.oy += cy - this.lastY;
      this._anim.x = this.ox; this._anim.y = this.oy;
      this.lastX = cx; this.lastY = cy;
      this._invalidateWorld();   // rAF repaints once — no per-event redraw
    });
    window.addEventListener('mouseup', () => {
      if (this.dragging) { this.dragging = false; this.cv.style.cursor = 'grab'; }
    });
    this.cv.style.cursor = 'grab';
    this.cv.addEventListener('click', (e) => {
      if (this.dragging) return;
      const [cx, cy] = this._toCanvas(e.clientX, e.clientY);
      this._hitTest(cx, cy);
    });
  }

  _zoomAt(cx, cy, factor) {
    const wx = (cx - this.ox) / this.scale;
    const wy = (cy - this.oy) / this.scale;
    const ns = Math.min(120, Math.max(1.5, this.scale * factor));
    this.ox = cx - wx * ns;
    this.oy = cy - wy * ns;
    this.scale = ns;
    this._anim.x = this.ox; this._anim.y = this.oy; this._anim.scale = this.scale;
    this._invalidateWorld();
  }

  setState(state) {
    this.state = state;
    if (this.fitDirty && state) this.autoCenter();
    else this._invalidateWorld();
  }

  select(id) {
    this.selected = id;
  }

  zoomBy(factor) {
    this._zoomAt(this.cv.width / 2, this.cv.height / 2, factor);
  }

  autoCenter() {
    this.fitDirty = false;
    const m = this.state && this.state.map;
    let span = 20;
    if (m && m.width && m.resolution) {
      span = Math.max(m.width * m.resolution, m.height * m.resolution, 20);
    }
    const margin = 48;
    this.scale = Math.min(
      (this.cv.width - margin) / span,
      (this.cv.height - margin) / span,
      40);
    this.ox = this.cv.width / 2;
    this.oy = this.cv.height / 2;
    this._anim.x = this.ox; this._anim.y = this.oy; this._anim.scale = this.scale;
    this._invalidateWorld();
  }

  // fit the full warehouse into view (used by toolbar)
  fitWarehouse() {
    this.fitDirty = false;
    const margin = 48;
    this.scale = Math.min(
      (this.cv.width - margin) / WORLD.size,
      (this.cv.height - margin) / WORLD.size,
      40);
    this.ox = this.cv.width / 2;
    this.oy = this.cv.height / 2;
    this._anim.x = this.ox; this._anim.y = this.oy; this._anim.scale = this.scale;
    this._invalidateWorld();
  }

  _hitTest(px, py) {
    if (!this.state) return;
    let best = null, bestD = 18;
    for (const r of this.state.robots) {
      if (r.x == null || r.y == null) continue;
      const [sx, sy] = this._w2s(r.x, r.y);
      const d = Math.hypot(sx - px, sy - py);
      if (d < bestD) { bestD = d; best = r.id; }
    }
    this.selected = best;
    this.onSelect(best);
  }

  // ── Per-frame animation loop (smooth interpolation + draw) ────────────

  _tick() {
    this._raf = requestAnimationFrame(() => {
      const now = performance.now();
      const dt = Math.min(50, now - (this._lastFrame || now)) / 1000;
      this._lastFrame = now;
      this._updateSmooth(dt);
      this._drawDynamic();
      this._tick();
    });
  }

  _updateSmooth(dt) {
    if (!this.state || !this.state.robots) return;
    const k = Math.min(1, dt * 8);      // exponential smoothing factor
    for (const r of this.state.robots) {
      if (r.x == null || r.y == null) continue;
      const key = r.id;
      const cur = this._smooth[key];
      if (!cur) {
        this._smooth[key] = { x: r.x, y: r.y, yaw: r.yaw || 0 };
        continue;
      }
      cur.x += (r.x - cur.x) * k;
      cur.y += (r.y - cur.y) * k;
      // shortest-path angle interpolation
      let dy = (r.yaw || 0) - cur.yaw;
      while (dy > Math.PI) dy -= Math.PI * 2;
      while (dy < -Math.PI) dy += Math.PI * 2;
      cur.yaw += dy * k;
    }
  }

  _drawDynamic() {
    const ctx = this.ctx;
    // Clear first so nothing from a previous frame accumulates (no ghosting /
    // duplicated scene). Only then paint the cached world + live dynamics.
    ctx.clearRect(0, 0, this.cv.width, this.cv.height);
    ctx.save();
    this._renderWorld(ctx);
    if (this.state) {
      this._drawReservations(ctx);
      this._drawTaskMarkers(ctx);
      this._drawPaths(ctx);
      this._drawPackages(ctx);
      this._drawRobots(ctx);
    } else {
      ctx.fillStyle = '#3d4a5a';
      ctx.font = '15px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('waiting for state…', this.cv.width / 2, this.cv.height / 2);
    }
    ctx.restore();
  }

  _drawReservations(ctx) {
    const res = this.state.reservations;
    if (!res || !res.list) return;
    const idx = {};
    this.state.robots.forEach((r, i) => { idx[r.id] = i; });
    for (const r of res.list) {
      const color = ROBOT_COLORS[idx[r.robot_id] % ROBOT_COLORS.length] || '#f2a65a';
      ctx.fillStyle = color + '2e';
      for (const [cx, cy] of (r.cells || [])) {
        const [sx, sy] = this._w2s(cx, cy);
        ctx.fillRect(sx, sy, this.scale, this.scale);
      }
    }
  }

  // Map a task pickup/dropoff coordinate to a named warehouse feature.
  // Pickups resolve to the SHELF (rack centre); drops to a delivery station.
  _nearestShelf(x, y) {
    let best = null, bestD = Infinity;
    for (const r of WORLD.racks) {
      const d = Math.hypot(r.x - x, r.y - y);
      if (d < bestD) { bestD = d; best = r; }
    }
    return best;
  }

  _nearestStation(x, y) {
    let best = null, bestD = Infinity;
    // Well-separated fixed delivery stations — never overlap each other.
    const stations = [
      { id: 'Shipping Dock', x: 2, y: -7 },
      { id: 'Packing Station', x: -2, y: -7 },
      { id: 'Loading Dock', x: 7, y: 0 },
    ];
    for (const s of stations) {
      const d = Math.hypot(s.x - x, s.y - y);
      if (d < bestD) { bestD = d; best = s; }
    }
    return best;
  }

  _drawTaskMarkers(ctx) {
    const tasks = (this.state.tasks && this.state.tasks.list) || [];
    for (const t of tasks) {
      if (t.status === 'COMPLETED' || t.status === 'CANCELLED') continue;
      const p = t.pickup, d = t.dropoff;
      // Pickup: only while the package is still on the shelf (i.e. the robot
      // has NOT yet picked it up). Once carried, the marker disappears.
      if (p) {
        const phase = this._pkgPhase(t);
        const onShelf = phase && phase.kind === 'pickup';
        if (onShelf) {
          const shelf = this._nearestShelf(p[0], p[1]);
          const mx = shelf ? shelf.x : p[0];
          const my = shelf ? shelf.y : p[1];
          this._drawShelfPickup(ctx, mx, my, shelf ? shelf.id : 'PICKUP');
        }
      }
      // Drop: only at a predefined delivery station.
      if (d) {
        const st = this._nearestStation(d[0], d[1]);
        if (!st) continue;               // only predefined delivery stations
        this._drawMarker(ctx, st.x, st.y, '#f2a65a', 'D', `DROPOFF · ${st.id}`);
      }
    }
  }

  // Draw the pickup highlight directly ON a shelf: a package icon + a small
  // "Pickup" badge anchored to the shelf — never floating in open floor.
  _drawShelfPickup(ctx, x, y, label) {
    const [sx, sy] = this._w2s(x, y);
    const s = Math.max(7, 0.4 * this.scale);   // package box size
    // package icon
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,0.18)';
    ctx.fillRect(sx - s / 2 + 1, sy - s / 2 + 1, s, s);
    ctx.fillStyle = '#f2a65a';
    ctx.strokeStyle = '#a86a2f';
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.rect(sx - s / 2, sy - s / 2, s, s); ctx.fill(); ctx.stroke();
    ctx.strokeStyle = 'rgba(168,106,47,0.8)';
    ctx.beginPath();
    ctx.moveTo(sx - s / 2, sy - s / 2); ctx.lineTo(sx + s / 2, sy + s / 2);
    ctx.moveTo(sx + s / 2, sy - s / 2); ctx.lineTo(sx - s / 2, sy + s / 2);
    ctx.stroke();
    // "Pickup" badge below the package
    const text = `Pickup · ${label}`;
    ctx.font = `bold ${Math.max(8, Math.min(12, 9 * (this.scale / 10)))}px sans-serif`;
    const tw = ctx.measureText(text).width + 10;
    ctx.fillStyle = 'rgba(242,166,90,0.15)';
    ctx.strokeStyle = '#f2a65a';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.roundRect(sx - tw / 2, sy + s / 2 + 2, tw, 15, 4); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#f2a65a';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, sx, sy + s / 2 + 9);
    ctx.restore();
  }

  _drawMarker(ctx, x, y, color, ch, label) {
    const [sx, sy] = this._w2s(x, y);
    ctx.save();
    ctx.fillStyle = color;
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1;
    const r = Math.max(5, 0.5 * this.scale);
    ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.font = `bold ${Math.max(8, Math.min(13, 10 * (this.scale / 10)))}px sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(ch, sx, sy + 0.5);
    if (label) {
      ctx.font = `bold ${Math.max(8, Math.min(12, 9 * (this.scale / 10)))}px sans-serif`;
      const tw = ctx.measureText(label).width + 8;
      ctx.fillStyle = 'rgba(16,20,26,0.8)';
      ctx.beginPath(); ctx.roundRect(sx - tw / 2, sy + r + 2, tw, 14, 3); ctx.fill();
      ctx.fillStyle = color;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(label, sx, sy + r + 9);
    }
    ctx.restore();
  }

  _drawPaths(ctx) {
    const idx = {};
    this.state.robots.forEach((r, i) => { idx[r.id] = i; });
    for (const r of this.state.robots) {
      const pts = r.path || [];
      if (pts.length < 2) continue;
      const color = ROBOT_COLORS[idx[r.id] % ROBOT_COLORS.length] || '#f2a65a';
      const smooth = this._smooth[r.id];
      const px = smooth ? smooth.x : r.x;
      const py = smooth ? smooth.y : r.y;
      // split at the nearest point to the robot: completed -> faded, remaining -> bright
      let nearest = 0, bestD = Infinity;
      for (let i = 0; i < pts.length; i++) {
        const d = Math.hypot(pts[i][0] - px, pts[i][1] - py);
        if (d < bestD) { bestD = d; nearest = i; }
      }
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      // completed (behind robot) — faded
      if (nearest > 1) {
        ctx.strokeStyle = color + '33';
        ctx.lineWidth = 3;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        pts.slice(0, nearest + 1).forEach(([x, y], i) => {
          const [sx, sy] = this._w2s(x, y);
          i === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
        });
        ctx.stroke();
        ctx.setLineDash([]);
      }
      // remaining (ahead) — highlighted
      ctx.strokeStyle = color;
      ctx.lineWidth = 3;
      ctx.beginPath();
      pts.slice(nearest).forEach(([x, y], i) => {
        const [sx, sy] = this._w2s(x, y);
        i === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
      });
      ctx.stroke();
    }
  }

  // ── Package lifecycle (derived purely from task + robot state) ────────

  _pkgPhase(task) {
    // returns {kind: 'pickup'|'carried'|'dropoff', pos:[x,y]}
    const p = task.pickup, d = task.dropoff;
    if (!p || !d) return null;
    // the package sits on the shelf before pickup, and at the drop station after
    const shelf = this._nearestShelf(p[0], p[1]);
    const shelfPos = shelf ? [shelf.x, shelf.y] : p;
    const station = this._nearestStation(d[0], d[1]);
    const dropPos = station ? [station.x, station.y] : d;

    if (task.status === 'COMPLETED' || task.status === 'CANCELLED') {
      return { kind: 'dropoff', pos: dropPos };
    }
    const robot = this.state.robots.find((r) => r.id === task.robot)
      || this.state.robots.find((r) => r.current_task === task.id);
    if (task.status === 'PENDING' || task.status === 'WAITING' || !robot) {
      return { kind: 'pickup', pos: shelfPos };
    }
    const rx = robot.x, ry = robot.y;
    const dp = Math.hypot(p[0] - rx, p[1] - ry);
    const dd = Math.hypot(d[0] - rx, d[1] - ry);
    if (dd < 0.9) return { kind: 'dropoff', pos: dropPos };
    if (dp < 0.9) return { kind: 'carried', pos: [rx, ry] };
    // on the way — carried once the robot has passed the pickup
    const key = task.id;
    const st = this._pkg[key] || 'pickup';
    if (st === 'carried') return { kind: 'carried', pos: [rx, ry] };
    if (dp < 3.0) { this._pkg[key] = 'carried'; return { kind: 'carried', pos: [rx, ry] }; }
    // still en route to the shelf — package stays ON the shelf
    return { kind: 'pickup', pos: shelfPos };
  }

  _drawPackages(ctx) {
    const tasks = (this.state.tasks && this.state.tasks.list) || [];
    for (const t of tasks) {
      const phase = this._pkgPhase(t);
      if (!phase || !phase.pos) continue;
      // The on-shelf package is drawn as part of the pickup highlight
      // (_drawShelfPickup); here we only render carried / delivered packages.
      if (phase.kind === 'pickup') continue;
      const [sx, sy] = this._w2s(phase.pos[0], phase.pos[1]);
      const s = Math.max(6, 0.35 * this.scale);
      // package: amber box with a subtle shadow
      ctx.fillStyle = 'rgba(0,0,0,0.18)';
      ctx.fillRect(sx - s / 2 + 1, sy - s / 2 + 1, s, s);
      ctx.fillStyle = '#f2a65a';
      ctx.strokeStyle = '#a86a2f';
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.rect(sx - s / 2, sy - s / 2, s, s); ctx.fill(); ctx.stroke();
      ctx.strokeStyle = 'rgba(168,106,47,0.8)';
      ctx.beginPath();
      ctx.moveTo(sx - s / 2, sy - s / 2); ctx.lineTo(sx + s / 2, sy + s / 2);
      ctx.moveTo(sx + s / 2, sy - s / 2); ctx.lineTo(sx - s / 2, sy + s / 2);
      ctx.stroke();
    }
  }

  _drawRobots(ctx) {
    const idx = {};
    this.state.robots.forEach((r, i) => { idx[r.id] = i; });
    for (const r of this.state.robots) {
      if (r.x == null || r.y == null) continue;
      const color = ROBOT_STATE_COLOR(r);
      const smooth = this._smooth[r.id];
      const x = smooth ? smooth.x : r.x;
      const y = smooth ? smooth.y : r.y;
      const yaw = smooth ? smooth.yaw : (r.yaw || 0);
      const [sx, sy] = this._w2s(x, y);
      const rad = Math.max(6, Math.min(14, 9 * (this.scale / 10)));

      if (r.id === this.selected) {
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(sx, sy, rad + 4, 0, Math.PI * 2); ctx.stroke();
      }

      ctx.save();
      ctx.translate(sx, sy);
      ctx.rotate(yaw);
      // body
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(0, 0, rad, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,0.35)'; ctx.lineWidth = 1;
      ctx.stroke();
      // heading notch (points forward in +x after rotate, matching Gazebo yaw)
      ctx.fillStyle = '#fff';
      ctx.beginPath();
      ctx.moveTo(rad + 1, 0);
      ctx.lineTo(rad * 0.45, -rad * 0.45);
      ctx.lineTo(rad * 0.45, rad * 0.45);
      ctx.closePath();
      ctx.fill();
      ctx.restore();

      // status chip
      const label = ROBOT_STATUS_LABEL(r);
      ctx.fillStyle = 'rgba(16,20,26,0.72)';
      const tw = ctx.measureText(label).width + 8;
      ctx.beginPath();
      ctx.roundRect(sx - tw / 2, sy - rad - 22, tw, 14, 3);
      ctx.fill();
      ctx.fillStyle = color;
      ctx.font = 'bold 9px sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(label, sx, sy - rad - 15);

      // name below
      ctx.fillStyle = '#a08c74';
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillText(r.id, sx, sy + rad + 3);
    }
  }
}
