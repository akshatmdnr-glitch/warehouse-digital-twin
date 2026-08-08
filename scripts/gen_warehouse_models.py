#!/usr/bin/env python3
"""Generate the realistic warehouse models, rack-ID sign textures, and the
navigation map for the Gazebo warehouse world.

Everything is created offline (no Gazebo Fuel / internet needed):

  * Box-based static models under ros2_ws/src/warehouse_bringup/models/:
      rack, rack_sign_A1..rack_sign_C3, pallet, crate, package,
      pickup_station, dropoff_station, loading_dock, charging_station
  * PIL-rendered rack ID textures (A1..C3)
  * maps/warehouse_world.pgm regenerated to exactly match the layout, plus a
    BFS connectivity check that every spawn/station/pickup/dropoff and every
    rack is reachable, and a preview PNG of the occupancy grid.

Usage:
    python3 scripts/gen_warehouse_models.py
"""

import os
import sys
from collections import deque

PIL_OK = True
try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - fallback to text-free plates
    PIL_OK = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "ros2_ws", "src", "warehouse_bringup")
MODELS_DIR = os.path.join(PKG, "models")
MAPS_DIR = os.path.join(PKG, "maps")

# ---------------------------------------------------------------------------
# Layout (world centered at origin, 20x20 m, walls at +/-10 m)
# ---------------------------------------------------------------------------
WORLD_HALF = 10.0

# 3 rows x 3 columns of racks, each 2.0 (x) x 1.0 (y) x 2.0 (h).
RACK_SIZE = (2.0, 1.0, 2.0)
RACK_ROWS = {"A": 3.0, "B": 0.0, "C": -3.0}   # row letter -> y
RACK_COLS = {"1": -4.0, "2": 0.0, "3": 4.0}   # col number -> x

SPAWNS = {"robot1": (0.0, 5.0), "robot2": (0.0, -5.0)}
STATIONS = {"robot1": (0.0, 8.0), "robot2": (0.0, -8.0)}
PICKUP = (-2.0, -7.0)
DROPOFF = (2.0, -7.0)

# Loading dock on the east wall: platform centre/size.
DOCK = {"x": 8.5, "y": 0.0, "w": 2.0, "d": 3.0, "h": 1.0}

# Static props placed off-lane (footprints approx 1.2 m or less).
PALLETS = [(7.0, 8.0), (-7.0, 8.0), (6.0, -8.0), (-6.0, -8.0)]
CRATES = [(7.0, 6.0), (-7.0, 6.0), (7.0, -6.0), (-7.0, -6.0)]
PACKAGES = [(3.0, -6.0), (-3.0, -6.0), (9.4, -5.0), (-9.4, -5.0)]

MAP_RES = 0.05
MAP_SIZE = int(2 * WORLD_HALF / MAP_RES)  # 400

_FONT = None
if PIL_OK:
    for _cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.isfile(_cand):
            _FONT = _cand
            break


def rack_poses():
    """All 9 rack poses with their IDs: ((x, y), id)."""
    out = []
    for row, ry in RACK_ROWS.items():
        for col, cx in RACK_COLS.items():
            out.append(((cx, ry), f"{row}{col}"))
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mat(amb, dif, spec=(0.1, 0.1, 0.1, 1)):
    return (f'<ambient>{amb[0]} {amb[1]} {amb[2]} 1</ambient>\n'
            f'<diffuse>{dif[0]} {dif[1]} {dif[2]} 1</diffuse>\n'
            f'<specular>{spec[0]} {spec[1]} {spec[2]} 1</specular>')


def _visual(name, pose, size, material, emissive=None):
    if emissive:
        mat = material + f'\n<emissive>{emissive[0]} {emissive[1]} {emissive[2]} 1</emissive>'
    else:
        mat = material
    return (f'<visual name="{name}">\n<pose>{pose}</pose>\n'
            f'<geometry><box><size>{size}</size></box></geometry>\n'
            f'<material>{mat}</material>\n</visual>')


def _write_model(name, sdf_body):
    d = os.path.join(MODELS_DIR, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "model.sdf"), "w") as f:
        f.write(f'<?xml version="1.0" ?>\n<sdf version="1.6">\n'
                f'<model name="{name}">\n{sdf_body}\n</model>\n</sdf>\n')
    with open(os.path.join(d, "model.config"), "w") as f:
        f.write(f'<?xml version="1.0"?>\n<model>\n  <name>{name}</name>\n'
                f'  <version>1.0</version>\n  <sdf version="1.6">model.sdf</sdf>\n'
                f'  <author><name>warehouse-digital-twin</name>'
                f'<email>akshat@example.com</email></author>\n'
                f'  <description>Box-based warehouse prop (generated).</description>\n'
                f'</model>\n')


# ---------------------------------------------------------------------------
# Rack (shelf unit)
# ---------------------------------------------------------------------------
def gen_rack():
    steel = _mat((0.15, 0.16, 0.2), (0.32, 0.34, 0.4), (0.25, 0.25, 0.25))
    shelf = _mat((0.25, 0.27, 0.3), (0.5, 0.53, 0.58), (0.3, 0.3, 0.3))
    # NB: shelf contents are NOT baked into the rack model — each package is a
    # separate visual entity spawned by the order-fulfillment visualization so
    # packages can be picked up, carried and delivered individually.
    body = ['  <static>true</static>', '  <link name="link">',
            # Single enclosing collision = the rack footprint.
            '    <collision name="footprint">',
            '      <pose>0 0 1.0 0 0 0</pose>',
            '      <geometry><box><size>1.96 0.96 2.0</size></box></geometry>',
            '    </collision>']
    body.append(_visual("post_nw", "-0.97 0.47 1.0 0 0 0", "0.06 0.06 2.0", steel))
    body.append(_visual("post_ne", " 0.97 0.47 1.0 0 0 0", "0.06 0.06 2.0", steel))
    body.append(_visual("post_sw", "-0.97 -0.47 1.0 0 0 0", "0.06 0.06 2.0", steel))
    body.append(_visual("post_se", " 0.97 -0.47 1.0 0 0 0", "0.06 0.06 2.0", steel))
    for i, z in enumerate((0.4, 1.0, 1.6)):
        body.append(_visual(f"shelf{i+1}", f"0 0 {z} 0 0 0", "1.9 0.94 0.05", shelf))
    body.append(_visual("back_panel", "0 -0.46 1.0 0 0 0", "1.9 0.04 2.0", steel))
    body.append('  </link>')
    _write_model("rack", "\n".join(body))


# ---------------------------------------------------------------------------
# Rack ID signs (textured panels)
# ---------------------------------------------------------------------------
def _sign_texture(rack_id):
    d = os.path.join(MODELS_DIR, f"rack_sign_{rack_id}", "textures")
    os.makedirs(d, exist_ok=True)
    png = os.path.join(d, f"{rack_id}.png")
    if not PIL_OK:
        return png
    w, h = 256, 96
    img = Image.new("RGB", (w, h), (23, 30, 42))
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 4, w - 5, h - 5], outline=(70, 120, 190), width=4)
    if _FONT:
        font = ImageFont.truetype(_FONT, 60)
    else:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), rack_id, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) / 2 - bbox[0], (h - th) / 2 - bbox[1]),
              rack_id, font=font, fill=(245, 245, 245))
    img.save(png)
    return png


def gen_rack_signs():
    for (_, _), rid in rack_poses():
        png = _sign_texture(rid)
        if PIL_OK and os.path.isfile(png):
            albedo = f'<pbr><metal><albedo_map>textures/{rid}.png</albedo_map><roughness>0.55</roughness><metalness>0.1</metalness></metal></pbr>'
            mat = (f'<ambient>1 1 1 1</ambient>\n<diffuse>1 1 1 1</diffuse>\n'
                   f'<specular>0.1 0.1 0.1 1</specular>\n{albedo}')
        else:
            mat = _mat((0.09, 0.12, 0.16), (0.16, 0.22, 0.3))
        body = ('  <static>true</static>\n  <link name="link">\n'
                + _visual("panel", "0 0 0.25 0 0 0", "1.6 0.08 0.5", mat)
                + "\n" + _visual("pole", "0 -0.0 0.04 0 0 0", "0.06 0.06 0.08",
                                 _mat((0.1, 0.1, 0.12), (0.25, 0.25, 0.3)))
                + '\n  </link>')
        _write_model(f"rack_sign_{rid}", body)


# ---------------------------------------------------------------------------
# Small static props
# ---------------------------------------------------------------------------
def gen_pallet():
    wood = _mat((0.35, 0.26, 0.15), (0.62, 0.46, 0.26), (0.15, 0.1, 0.05))
    wood_d = _mat((0.28, 0.2, 0.11), (0.5, 0.36, 0.2), (0.1, 0.08, 0.04))
    body = ['  <static>true</static>', '  <link name="link">',
            '    <collision name="footprint">',
            '      <pose>0 0 0.07 0 0 0</pose>',
            '      <geometry><box><size>1.2 1.2 0.14</size></box></geometry>',
            '    </collision>']
    for i, x in enumerate((-0.4, 0.0, 0.4)):
        body.append(_visual(f"top{i+1}", f"{x} 0 0.13 0 0 0", "0.36 1.2 0.03", wood))
    for i, y in enumerate((-0.45, 0.45)):
        body.append(_visual(f"stringer{i+1}", f"0 {y} 0.06 0 0 0", "1.2 0.18 0.1", wood_d))
    for i, x in enumerate((-0.4, 0.0, 0.4)):
        body.append(_visual(f"bottom{i+1}", f"{x} 0 0.02 0 0 0", "0.36 1.2 0.03", wood_d))
    body.append('  </link>')
    _write_model("pallet", "\n".join(body))


def gen_crate():
    wood = _mat((0.33, 0.24, 0.13), (0.6, 0.44, 0.24), (0.15, 0.1, 0.05))
    lid = _mat((0.68, 0.25, 0.16), (0.82, 0.32, 0.2), (0.3, 0.2, 0.1))
    body = ['  <static>true</static>', '  <link name="link">',
            '    <collision name="footprint">',
            '      <pose>0 0 0.25 0 0 0</pose>',
            '      <geometry><box><size>0.6 0.6 0.5</size></box></geometry>',
            '    </collision>',
            _visual("body", "0 0 0.25 0 0 0", "0.6 0.6 0.5", wood),
            _visual("lid", "0 0 0.54 0 0 0", "0.64 0.64 0.08", lid),
            '  </link>']
    _write_model("crate", "\n".join(body))


def gen_package():
    card = _mat((0.62, 0.5, 0.3), (0.78, 0.64, 0.4), (0.15, 0.12, 0.06))
    tape = _mat((0.85, 0.83, 0.78), (0.92, 0.9, 0.85), (0.2, 0.2, 0.2))
    body = ['  <static>true</static>', '  <link name="link">',
            '    <collision name="footprint">',
            '      <pose>0 0 0.15 0 0 0</pose>',
            '      <geometry><box><size>0.4 0.4 0.3</size></box></geometry>',
            '    </collision>',
            _visual("body", "0 0 0.15 0 0 0", "0.4 0.4 0.3", card),
            _visual("tape_x", "0 0 0.31 0 0 0", "0.4 0.06 0.02", tape),
            _visual("tape_y", "0 0 0.31 0 0 0", "0.06 0.4 0.02", tape),
            '  </link>']
    _write_model("package", "\n".join(body))


def gen_stations():
    body = ('  <static>true</static>\n  <link name="link">\n'
            + _visual("marker", "0 0 0.01 0 0 0", "1.4 1.4 0.02",
                      _mat((0.05, 0.25, 0.1), (0.1, 0.55, 0.22)))
            + "\n" + _visual("pole", "0.55 -0.55 0.5 0 0 0", "0.08 0.08 1.0",
                             _mat((0.05, 0.2, 0.08), (0.12, 0.5, 0.2)))
            + "\n" + _visual("light", "0.55 -0.55 1.05 0 0 0", "0.06 0.18 0.06",
                             _mat((0.1, 0.5, 0.2), (0.2, 0.8, 0.3)),
                             emissive=(0.1, 0.8, 0.3))
            + '\n  </link>')
    _write_model("pickup_station", body)
    body = ('  <static>true</static>\n  <link name="link">\n'
            + _visual("marker", "0 0 0.01 0 0 0", "1.4 1.4 0.02",
                      _mat((0.3, 0.16, 0.05), (0.7, 0.38, 0.12)))
            + "\n" + _visual("pole", "0.55 -0.55 0.5 0 0 0", "0.08 0.08 1.0",
                             _mat((0.26, 0.14, 0.04), (0.6, 0.32, 0.1)))
            + "\n" + _visual("light", "0.55 -0.55 1.05 0 0 0", "0.06 0.18 0.06",
                             _mat((0.5, 0.3, 0.1), (0.8, 0.5, 0.2)),
                             emissive=(0.9, 0.55, 0.15))
            + '\n  </link>')
    _write_model("dropoff_station", body)


def gen_dock():
    concrete = _mat((0.42, 0.42, 0.45), (0.58, 0.58, 0.62), (0.2, 0.2, 0.2))
    stripe = _mat((0.8, 0.65, 0.1), (0.95, 0.8, 0.15), (0.3, 0.25, 0.1))
    dark = _mat((0.15, 0.16, 0.18), (0.32, 0.34, 0.38), (0.2, 0.2, 0.2))
    body = ['  <static>true</static>', '  <link name="link">',
            '    <collision name="platform">',
            '      <pose>0 0 0.5 0 0 0</pose>',
            '      <geometry><box><size>2.0 3.0 1.0</size></box></geometry>',
            '    </collision>',
            _visual("platform_vis", "0 0 0.5 0 0 0", "2.0 3.0 1.0", concrete),
            _visual("platform_top", "0 0 1.0 0 0 0", "1.96 2.96 0.01",
                    _mat((0.3, 0.3, 0.32), (0.45, 0.45, 0.48))),
            _visual("stripe1", "0 -1.05 1.02 0 0 0", "1.9 0.16 0.02", stripe),
            _visual("stripe2", "0 1.05 1.02 0 0 0", "1.9 0.16 0.02", stripe),
            _visual("canopy", "0 0 2.6 0 0 0", "2.4 3.3 0.1", dark),
            _visual("doorpost_left", "0.8 -1.55 1.6 0 0 0", "0.14 0.14 2.2", dark),
            _visual("doorpost_right", "0.8 1.55 1.6 0 0 0", "0.14 0.14 2.2", dark),
            _visual("door_header", "0.8 0 2.85 0 0 0", "0.14 2.8 0.14", dark),
            _visual("dock_light", "0.8 -0.5 2.78 0 0 0", "0.3 0.5 0.1",
                    _mat((0.5, 0.3, 0.08), (0.8, 0.5, 0.15)), emissive=(0.95, 0.6, 0.2)),
            '  </link>']
    _write_model("loading_dock", "\n".join(body))


def gen_charging_station():
    pad = _mat((0.08, 0.12, 0.2), (0.12, 0.2, 0.35), (0.3, 0.3, 0.3))
    pole = _mat((0.12, 0.13, 0.16), (0.3, 0.32, 0.38), (0.2, 0.2, 0.2))
    body = ['  <static>true</static>', '  <link name="link">',
            '    <collision name="pole">',
            '      <pose>0.4 0 0.4 0 0 0</pose>',
            '      <geometry><box><size>0.1 0.1 0.8</size></box></geometry>',
            '    </collision>',
            _visual("pad", "0 0 0.01 0 0 0", "1.0 1.0 0.02", pad),
            _visual("pole_vis", "0.4 0 0.4 0 0 0", "0.1 0.1 0.8", pole),
            _visual("led", "0.4 0 0.85 0 0 0", "0.06 0.22 0.06",
                    _mat((0.1, 0.4, 0.15), (0.2, 0.8, 0.3)), emissive=(0.15, 0.9, 0.35)),
            _visual("cable", "0 0 0.05 0 0 0", "0.9 0.06 0.02",
                    _mat((0.1, 0.1, 0.1), (0.2, 0.2, 0.2))),
            '  </link>']
    _write_model("charging_station", "\n".join(body))


# ---------------------------------------------------------------------------
# Navigation map (matches the layout) + BFS connectivity check
# ---------------------------------------------------------------------------
def _free_array():
    free = [[True] * MAP_SIZE for _ in range(MAP_SIZE)]
    n = MAP_SIZE
    wall = int(0.15 / MAP_RES) + 1  # wall thickness in cells
    wall_m = wall * MAP_RES          # wall thickness in metres
    for i in range(n):
        for j in range(n):
            x = -WORLD_HALF + (j + 0.5) * MAP_RES
            y = -WORLD_HALF + (i + 0.5) * MAP_RES
            if abs(x) > WORLD_HALF - wall_m or abs(y) > WORLD_HALF - wall_m:
                free[i][j] = False
    return free


def _mark_box(free, cx, cy, w, d):
    """Mark cells overlapping [cx-w/2, cx+w/2] x [cy-d/2, cy+d/2] occupied."""
    x0 = int((cx - w / 2 + WORLD_HALF) / MAP_RES)
    x1 = int((cx + w / 2 + WORLD_HALF) / MAP_RES)
    y0 = int((cy - d / 2 + WORLD_HALF) / MAP_RES)
    y1 = int((cy + d / 2 + WORLD_HALF) / MAP_RES)
    for i in range(y0, min(y1, MAP_SIZE)):
        for j in range(x0, min(x1, MAP_SIZE)):
            if 0 <= i < MAP_SIZE and 0 <= j < MAP_SIZE:
                free[i][j] = False


def _build_occupancy():
    free = _free_array()
    for (cx, cy), _rid in rack_poses():
        _mark_box(free, cx, cy, RACK_SIZE[0], RACK_SIZE[1])
    # Loading dock platform (pallet/package/crate props are outside it).
    _mark_box(free, DOCK["x"], DOCK["y"], DOCK["w"], DOCK["d"])
    # Charging station poles (pads are traversable).
    for _rid, (sx, sy) in STATIONS.items():
        _mark_box(free, sx + 0.4, sy, 0.2, 0.2)
    for px, py in PALLETS:
        _mark_box(free, px, py, 1.25, 1.25)
    for cx, cy in CRATES:
        _mark_box(free, cx, cy, 0.65, 0.65)
    for px, py in PACKAGES:
        _mark_box(free, px, py, 0.45, 0.45)
    # Loading-dock pallets sit on the platform -> already covered by dock box.
    return free


def _to_idx(x, y):
    j = int((x + WORLD_HALF) / MAP_RES)
    i = int((y + WORLD_HALF) / MAP_RES)
    return i, j


def _bfs(free, start):
    si, sj = _to_idx(*start)
    if not (0 <= si < MAP_SIZE and 0 <= sj < MAP_SIZE) or not free[si][sj]:
        return None
    seen = {(si, sj)}
    q = deque([(si, sj)])
    while q:
        i, j = q.popleft()
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < MAP_SIZE and 0 <= nj < MAP_SIZE:
                if free[ni][nj] and (ni, nj) not in seen:
                    seen.add((ni, nj))
                    q.append((ni, nj))
    return seen


def _nearest_free(free, cx, cy):
    si, sj = _to_idx(cx, cy)
    for r in range(1, 40):
        for i in range(si - r, si + r + 1):
            for j in range(sj - r, sj + r + 1):
                if 0 <= i < MAP_SIZE and 0 <= j < MAP_SIZE and free[i][j]:
                    return i, j
    return None


def gen_map():
    free = _build_occupancy()
    component = _bfs(free, SPAWNS["robot1"])
    assert component, "robot1 spawn not free"

    def require_reachable(label, point):
        i, j = _to_idx(*point)
        ok = (i, j) in component if component is not None else False
        if not ok:
            nearest = _nearest_free(free, *point)
            ok = nearest in component if component is not None else False
        assert ok, f"{label} {point} not reachable"

    for rid, p in SPAWNS.items():
        require_reachable(f"spawn {rid}", p)
    for rid, p in STATIONS.items():
        require_reachable(f"station {rid}", p)
    require_reachable("pickup", PICKUP)
    require_reachable("dropoff", DROPOFF)
    require_reachable("dock front", (7.3, 0.0))
    for (cx, cy), rid in rack_poses():
        near = _nearest_free(free, cx, cy)
        assert near in component, f"rack {rid} ({cx},{cy}) not accessible"

    # Write PGM. Convention used by this repo's map_loader/planner:
    # dark (<=25) = free, light (>=65) = occupied.
    pgm = os.path.join(MAPS_DIR, "warehouse_world.pgm")
    with open(pgm, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (MAP_SIZE, MAP_SIZE))
        for i in range(MAP_SIZE):
            row = bytearray(0 if free[i][j] else 100 for j in range(MAP_SIZE))
            f.write(bytes(row))

    free_cells = sum(sum(r) for r in free)
    print(f"[map] wrote {pgm}: {MAP_SIZE}x{MAP_SIZE} @ {MAP_RES} m "
          f"({free_cells}/{MAP_SIZE*MAP_SIZE} free)")
    print(f"[map] BFS: {len(component)} cells in main component "
          f"- all spawns/stations/pickup/dropoff/racks reachable")

    _write_preview(free)
    return pgm


def _write_preview(free):
    import math
    scale = 1
    w = MAP_SIZE // scale
    img = Image.new("RGB", (w, w), (245, 245, 245))
    px = img.load()
    for i in range(0, MAP_SIZE, scale):
        for j in range(0, MAP_SIZE, scale):
            if not free[i][j]:
                px[j // scale, i // scale] = (40, 40, 40)
    comp = _bfs(free, SPAWNS["robot1"])
    if comp:
        for i in range(0, MAP_SIZE, scale):
            for j in range(0, MAP_SIZE, scale):
                if free[i][j] and (i, j) not in comp:
                    px[j // scale, i // scale] = (200, 60, 60)

    def dot(x, y, rgb):
        i, j = _to_idx(x, y)
        for di in range(-1, 2):
            for dj in range(-1, 2):
                pi, pj = i // scale + di, j // scale + dj
                if 0 <= pi < w and 0 <= pj < w:
                    px[pj, pi] = rgb

    for rid, p in SPAWNS.items():
        dot(*p, (30, 144, 255))
    for rid, p in STATIONS.items():
        dot(*p, (0, 200, 120))
    dot(*PICKUP, (0, 180, 60))
    dot(*DROPOFF, (255, 120, 20))
    dot(DOCK["x"], DOCK["y"], (120, 120, 160))
    preview = os.path.join(MAPS_DIR, "warehouse_layout_preview.png")
    img.save(preview)
    print(f"[map] preview written to {preview}")


def main():
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(MAPS_DIR, exist_ok=True)
    gen_rack()
    gen_rack_signs()
    gen_pallet()
    gen_crate()
    gen_package()
    gen_stations()
    gen_dock()
    gen_charging_station()
    print(f"[models] written to {MODELS_DIR}")
    gen_map()
    print("[ok] warehouse models + map generated")


if __name__ == "__main__":
    sys.exit(main())
