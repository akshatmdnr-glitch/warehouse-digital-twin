"""Builders for the visual entities the warehouse visualization spawns in Gazebo.

Generates SDF model XML (visual-only, static) for packages, highlights, paths,
robot markers and text panels, plus PIL-rendered label textures.  Pure helpers,
no ROS / Gazebo runtime required.
"""

from __future__ import annotations

import math
import os
from typing import List, Tuple

_TEXTURES_DIR = os.path.join("/tmp", "wdt_viz")
_TEX_CACHE: dict = {}

_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _fmt(v):
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _material(rgb, emissive=None, ambient=1.0, transparency=None, texture: str = ""):
    r, g, b = rgb
    # gz/SDFormat requires color channels in [0, 1]
    ar = min(1.0, r * ambient)
    ag = min(1.0, g * ambient)
    ab = min(1.0, b * ambient)
    parts = [
        f"<ambient>{_fmt(ar)} {_fmt(ag)} {_fmt(ab)} 1</ambient>",
        f"<diffuse>{_fmt(r)} {_fmt(g)} {_fmt(b)} 1</diffuse>",
        "<specular>0.1 0.1 0.1 1</specular>",
    ]
    if emissive:
        parts.append(
            f"<emissive>{_fmt(min(1.0, emissive[0]))} "
            f"{_fmt(min(1.0, emissive[1]))} "
            f"{_fmt(min(1.0, emissive[2]))} 1</emissive>"
        )
    if transparency is not None:
        parts.append(f"<transparency>{_fmt(transparency)}</transparency>")
    if texture:
        parts.append(
            f"<pbr><metal><albedo_map>{texture}</albedo_map>"
            f"<roughness>0.7</roughness><metalness>0.0</metalness></metal></pbr>"
        )
    return "<material>" + "".join(parts) + "</material>"


def box_visual(
    name: str,
    size,
    pose,
    rgb,
    emissive=None,
    ambient=1.0,
    transparency=None,
    texture: str = "",
):
    s = f"{_fmt(size[0])} {_fmt(size[1])} {_fmt(size[2])}"
    p = (
        f"{_fmt(pose[0])} {_fmt(pose[1])} {_fmt(pose[2])} "
        f"{_fmt(pose[3])} {_fmt(pose[4])} {_fmt(pose[5])}"
    )
    mat = _material(rgb, emissive, ambient, transparency, texture)
    return (
        f'<visual name="{name}"><pose>{p}</pose>'
        f"<geometry><box><size>{s}</size></box></geometry>{mat}</visual>"
    )


def cylinder_visual(
    name: str, radius, length, pose, rgb, emissive=None, ambient=1.0, transparency=None
):
    p = (
        f"{_fmt(pose[0])} {_fmt(pose[1])} {_fmt(pose[2])} "
        f"{_fmt(pose[3])} {_fmt(pose[4])} {_fmt(pose[5])}"
    )
    mat = _material(rgb, emissive, ambient, transparency)
    return (
        f'<visual name="{name}"><pose>{p}</pose>'
        f"<geometry><cylinder><radius>{_fmt(radius)}</radius>"
        f"<length>{_fmt(length)}</length></cylinder></geometry>{mat}</visual>"
    )


def sphere_visual(name: str, radius, pose, rgb, emissive=None, ambient=1.0):
    p = (
        f"{_fmt(pose[0])} {_fmt(pose[1])} {_fmt(pose[2])} "
        f"{_fmt(pose[3])} {_fmt(pose[4])} {_fmt(pose[5])}"
    )
    mat = _material(rgb, emissive, ambient)
    return (
        f'<visual name="{name}"><pose>{p}</pose>'
        f"<geometry><sphere><radius>{_fmt(radius)}</radius>"
        f"</sphere></geometry>{mat}</visual>"
    )


def _model(name: str, visuals: str) -> str:
    return (
        f'<sdf version="1.6"><model name="{name}"><static>true</static>'
        f'<link name="link">{visuals}</link></model></sdf>'
    )


# ---------------------------------------------------------------------------
# Entity builders
# ---------------------------------------------------------------------------


def package_model(pkg_id: str, color) -> str:
    vis = box_visual(
        "package",
        (0.3, 0.3, 0.3),
        (0, 0, 0.15, 0, 0, 0),
        color,
        emissive=(color[0] * 0.35, color[1] * 0.35, color[2] * 0.35),
        ambient=1.2,
    )
    return _model(pkg_id, vis)


def pickup_highlight_model(
    label: str, radius: float = 0.45, height: float = 5.0
) -> str:
    green = (0.05, 0.95, 0.35)
    vis = cylinder_visual(
        "column",
        radius,
        height,
        (0, 0, height / 2, 0, 0, 0),
        green,
        emissive=(0.0, 0.9, 0.3),
        ambient=2.0,
    ) + cylinder_visual(
        "base_ring",
        radius + 0.35,
        0.05,
        (0, 0, 0.02, 0, 0, 0),
        green,
        emissive=(0.0, 0.9, 0.3),
        ambient=2.0,
    )
    return _model(label, vis)


def dropoff_highlight_model(
    label: str, radius: float = 0.45, height: float = 5.0
) -> str:
    blue = (0.15, 0.5, 1.0)
    vis = cylinder_visual(
        "column",
        radius,
        height,
        (0, 0, height / 2, 0, 0, 0),
        blue,
        emissive=(0.1, 0.4, 0.95),
        ambient=2.0,
    ) + cylinder_visual(
        "base_ring",
        radius + 0.35,
        0.05,
        (0, 0, 0.02, 0, 0, 0),
        blue,
        emissive=(0.1, 0.4, 0.95),
        ambient=2.0,
    )
    return _model(label, vis)


def sphere_model(name: str, radius: float, rgb, emissive=None, ambient=1.0) -> str:
    vis = sphere_visual(
        "sphere", radius, (0, 0, 0, 0, 0, 0), rgb, emissive=emissive, ambient=ambient
    )
    return _model(name, vis)


def robot_glow_model(label: str, color) -> str:
    r, g, b = color
    vis = cylinder_visual(
        "glow",
        0.38,
        1.4,
        (0, 0, 0.7, 0, 0, 0),
        color,
        emissive=(r * 0.8, g * 0.8, b * 0.8),
        ambient=2.0,
        transparency=0.45,
    )
    return _model(label, vis)


def path_model(
    label: str, color, waypoints: List[Tuple[float, float]], width: float = 0.12
) -> str:
    """Flat ribbon along the waypoints: thin boxes between consecutive points."""
    if len(waypoints) < 2:
        return None
    r, g, b = color
    emissive = (r * 0.5, g * 0.5, b * 0.5)
    vis_parts = []
    for i in range(len(waypoints) - 1):
        x0, y0 = waypoints[i]
        x1, y1 = waypoints[i + 1]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 0.02:
            continue
        yaw = math.atan2(y1 - y0, x1 - x0)
        vis_parts.append(
            box_visual(
                f"seg{i}",
                (length, width, 0.02),
                (mx, my, 0.02, 0, 0, yaw),
                color,
                emissive=emissive,
                ambient=1.6,
            )
        )
    if not vis_parts:
        return None
    return _model(label, "".join(vis_parts))


def floor_square_model(label: str, rgb, size: float = 1.4, z: float = 0.02) -> str:
    """A flat colored square on the floor (pickup / dropoff marker base)."""
    vis = box_visual(
        "square",
        (size, size, 0.02),
        (0, 0, z / 2, 0, 0, 0),
        rgb,
        emissive=(rgb[0] * 0.8, rgb[1] * 0.8, rgb[2] * 0.8),
        ambient=1.8,
    )
    return _model(label, vis)


def text_model(label: str, texture_path: str, width: float, height: float) -> str:
    # emissive keeps the panel bright regardless of scene lighting
    vis = box_visual(
        "panel",
        (width, height, 0.02),
        (0, 0, 0, 0, 0, 0),
        (1, 1, 1),
        ambient=1.0,
        emissive=(0.45, 0.45, 0.5),
        texture=texture_path,
    )
    # crossed second panel so the text is readable from the sides too
    vis += box_visual(
        "panel2",
        (height, width, 0.02),
        (0, 0, 0, 0, 0, math.pi / 2),
        (1, 1, 1),
        ambient=1.0,
        emissive=(0.45, 0.45, 0.5),
        texture=texture_path,
    )
    return _model(label, vis)


# ---------------------------------------------------------------------------
# Text textures (PIL)
# ---------------------------------------------------------------------------


def _text_size(text: str, font_px: int):
    from PIL import ImageFont

    font = ImageFont.truetype(_FONT, font_px)
    lines = text.split("\n")
    widths, heights = [], []
    for ln in lines:
        bbox = font.getbbox(ln)
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])
    return font, max(widths), sum(heights) + (len(lines) - 1) * font_px // 2


def make_text_texture(
    text: str, name: str, fg=(255, 220, 60), bg=(18, 26, 48), font_px: int = 96
) -> Tuple[str, float, float]:
    """Render text to a PNG; returns (path, world_width, world_height).

    The returned world dimensions keep labels at a readable size in the 3D
    scene (~0.0018 m per texture pixel).
    """
    from PIL import Image, ImageDraw

    os.makedirs(_TEXTURES_DIR, exist_ok=True)
    path = os.path.join(_TEXTURES_DIR, name + ".png")
    if path in _TEX_CACHE:
        return _TEX_CACHE[path]

    font, tw, th = _text_size(text, font_px)
    pad_x, pad_y = font_px // 2, font_px // 3
    w, h = tw + 2 * pad_x, th + 2 * pad_y
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, w - 5, h - 5], outline=fg, width=max(4, font_px // 20))
    lines = text.split("\n")
    y = pad_y
    for ln in lines:
        d.text((pad_x, y), ln, font=font, fill=fg)
        y += (th // len(lines)) + font_px // 2
    img.save(path)

    scale = 0.0018
    world_w = w * scale
    world_h = h * scale
    _TEX_CACHE[path] = (path, world_w, world_h)
    return _TEX_CACHE[path]
