// Interactive occupancy-map renderer with zoom, pan and robot selection.

const ROBOT_COLORS = ['#38bdf8', '#a78bfa', '#34d399', '#fbbf24', '#f472b6'];

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

    this._bind();
  }

  // Convert browser (CSS) mouse coordinates into canvas pixel coordinates so
  // zoom/pan/hit-test stay correct when the canvas is CSS-scaled.
  _toCanvas(clientX, clientY) {
    const rect = this.cv.getBoundingClientRect();
    const kx = rect.width ? this.cv.width / rect.width : 1;
    const ky = rect.height ? this.cv.height / rect.height : 1;
    return [(clientX - rect.left) * kx, (clientY - rect.top) * ky];
  }

  _bind() {
    this.cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      const [cx, cy] = this._toCanvas(e.clientX, e.clientY);
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      const wx = (cx - this.ox) / this.scale;
      const wy = (cy - this.oy) / this.scale;
      this.scale = Math.min(120, Math.max(1.5, this.scale * factor));
      this.ox = cx - wx * this.scale;
      this.oy = cy - wy * this.scale;
      this.draw();
    }, { passive: false });

    this.cv.addEventListener('mousedown', (e) => {
      this.dragging = true;
      const [cx, cy] = this._toCanvas(e.clientX, e.clientY);
      this.lastX = cx;
      this.lastY = cy;
    });
    window.addEventListener('mousemove', (e) => {
      if (!this.dragging) return;
      const [cx, cy] = this._toCanvas(e.clientX, e.clientY);
      this.ox += cx - this.lastX;
      this.oy += cy - this.lastY;
      this.lastX = cx;
      this.lastY = cy;
      this.draw();
    });
    window.addEventListener('mouseup', () => { this.dragging = false; });
    this.cv.addEventListener('click', (e) => {
      if (this.dragging) return;
      const [cx, cy] = this._toCanvas(e.clientX, e.clientY);
      this._hitTest(cx, cy);
    });
  }

  setState(state) {
    this.state = state;
    if (this.fitDirty && state && state.map) { this.autoCenter(); }
    this.draw();
  }

  select(id) {
    this.selected = id;
    this.draw();
  }

  zoomBy(factor) {
    const cx = this.cv.width / 2;
    const cy = this.cv.height / 2;
    const wx = (cx - this.ox) / this.scale;
    const wy = (cy - this.oy) / this.scale;
    this.scale = Math.min(120, Math.max(1.5, this.scale * factor));
    this.ox = cx - wx * this.scale;
    this.oy = cy - wy * this.scale;
    this.draw();
  }

  autoCenter() {
    const m = this.state && this.state.map;
    if (!m) { this.scale = 10; this.ox = this.cv.width / 2; this.oy = this.cv.height / 2; this.draw(); return; }
    const w = m.width * m.resolution;
    const h = m.height * m.resolution;
    const margin = 40;
    this.scale = Math.min(
      (this.cv.width - margin) / w,
      (this.cv.height - margin) / h,
      25);
    const cx = m.origin[0] + w / 2;
    const cy = m.origin[1] + h / 2;
    this.ox = this.cv.width / 2 - cx * this.scale;
    this.oy = this.cv.height / 2 - cy * this.scale;
    this.draw();
  }

  _w2s(x, y) { return [this.ox + x * this.scale, this.oy + y * this.scale]; }

  _hitTest(px, py) {
    if (!this.state) return;
    let best = null;
    let bestD = 14;
    for (const r of this.state.robots) {
      if (r.x == null || r.y == null) continue;
      const [sx, sy] = this._w2s(r.x, r.y);
      const d = Math.hypot(sx - px, sy - py);
      if (d < bestD) { bestD = d; best = r.id; }
    }
    this.selected = best;
    this.onSelect(best);
    this.draw();
  }

  draw() {
    const cv = this.cv, ctx = this.ctx;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.fillStyle = '#0a0d12';
    ctx.fillRect(0, 0, cv.width, cv.height);
    if (!this.state) { this._empty(ctx, cv); return; }

    this._drawGrid(ctx);
    this._drawMap(ctx);
    this._drawReservations(ctx);
    this._drawStations(ctx);
    this._drawGoals(ctx);
    this._drawPaths(ctx);
    this._drawRobots(ctx);
  }

  _empty(ctx, cv) {
    ctx.fillStyle = '#3d4a5a';
    ctx.font = '15px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('waiting for map…', cv.width / 2, cv.height / 2);
  }

  _drawGrid(ctx) {
    const m = this.state.map;
    if (!m) return;
    const w = m.width * m.resolution, h = m.height * m.resolution;
    const [x0, y0] = this._w2s(m.origin[0], m.origin[1]);
    const [x1, y1] = this._w2s(m.origin[0] + w, m.origin[1] + h);
    ctx.strokeStyle = 'rgba(120,140,165,0.07)';
    ctx.lineWidth = 1;
    const step = 1 * this.scale;
    for (let x = x0; x <= x1; x += step) {
      ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
    }
    for (let y = y0; y <= y1; y += step) {
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    }
  }

  _drawMap(ctx) {
    const m = this.state.map;
    if (!m || !m.data) return;
    const ncols = Math.max(1, Math.ceil(m.width / m.step));
    const cell = m.resolution * this.scale;
    const ox = m.origin[0], oy = m.origin[1];
    ctx.fillStyle = '#151a22';
    for (let i = 0; i < m.data.length; i++) {
      const v = m.data[i];
      if (v === 0) continue;
      const gx = (i % ncols) * m.step;
      const gy = Math.floor(i / ncols) * m.step;
      const [sx, sy] = this._w2s(ox + gx * m.resolution, oy + gy * m.resolution);
      ctx.fillStyle = v === -1 ? '#202630' : '#3a4654';
      ctx.fillRect(sx, sy, cell + 0.5, cell + 0.5);
    }
  }

  _drawReservations(ctx) {
    const res = this.state.reservations;
    if (!res || !res.list) return;
    const idx = {};
    this.state.robots.forEach((r, i) => { idx[r.id] = i; });
    for (const r of res.list) {
      const color = ROBOT_COLORS[idx[r.robot_id] % ROBOT_COLORS.length] || '#38bdf8';
      ctx.fillStyle = color + '2e';
      ctx.strokeStyle = color + '66';
      ctx.lineWidth = 1;
      for (const [cx, cy] of (r.cells || [])) {
        const [sx, sy] = this._w2s(cx * 1.0, cy * 1.0);
        ctx.fillRect(sx, sy, this.scale, this.scale);
      }
    }
  }

  _drawStations(ctx) {
    for (const s of (this.state.charging_stations || [])) {
      const [sx, sy] = this._w2s(s.x, s.y);
      ctx.strokeStyle = '#34d399';
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(sx, sy, 12, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(sx - 6, sy); ctx.lineTo(sx + 6, sy);
      ctx.moveTo(sx, sy - 6); ctx.lineTo(sx, sy + 6); ctx.stroke();
      ctx.fillStyle = '#34d399'; ctx.font = '11px sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText('charge', sx + 15, sy + 4);
    }
  }

  _drawGoals(ctx) {
    for (const r of this.state.robots) {
      if (!r.goal) continue;
      const [gx, gy] = this._w2s(r.goal[0], r.goal[1]);
      ctx.strokeStyle = '#fbbf24';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(gx - 8, gy); ctx.lineTo(gx + 8, gy);
      ctx.moveTo(gx, gy - 8); ctx.lineTo(gx, gy + 8);
      ctx.stroke();
      ctx.beginPath(); ctx.arc(gx, gy, 5, 0, Math.PI * 2); ctx.stroke();
    }
  }

  _drawPaths(ctx) {
    const idx = {};
    this.state.robots.forEach((r, i) => { idx[r.id] = i; });
    for (const r of this.state.robots) {
      const pts = r.path || [];
      if (pts.length < 2) continue;
      const color = ROBOT_COLORS[idx[r.id] % ROBOT_COLORS.length] || '#38bdf8';
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      pts.forEach(([x, y], i) => {
        const [sx, sy] = this._w2s(x, y);
        i === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  _drawRobots(ctx) {
    const idx = {};
    this.state.robots.forEach((r, i) => { idx[r.id] = i; });
    for (const r of this.state.robots) {
      if (r.x == null || r.y == null) continue;
      const color = r.charging ? '#fbbf24'
        : r.status === 'OFFLINE' ? '#6b7280'
        : r.estop ? '#fb2576'
        : ROBOT_COLORS[idx[r.id] % ROBOT_COLORS.length];
      const [sx, sy] = this._w2s(r.x, r.y);
      const yaw = r.yaw || 0;
      const rad = 9;
      if (r.id === this.selected) {
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(sx, sy, rad + 4, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(sx, sy, rad, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.beginPath(); ctx.arc(sx, sy, rad, yaw - 0.55, yaw + 0.55); ctx.fill();
      // Heading line
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(sx + Math.cos(yaw) * (rad + 5), sy + Math.sin(yaw) * (rad + 5));
      ctx.stroke();
      ctx.fillStyle = '#dfe6ef';
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(r.id, sx, sy - rad - 6);
    }
  }
}
