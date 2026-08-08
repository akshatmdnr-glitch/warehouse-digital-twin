"""Unit tests for the order-fulfillment domain logic + SDF builders."""

import math

import pytest

from warehouse_bringup.order_fulfillment import (
    PACKAGE_COLORS,
    RACK_POS,
    SHELF_Z,
    SLOT_X,
    build_inventory,
    nearest_rack,
    task_to_package,
)
from warehouse_bringup import viz_sdf


def test_inventory_count_and_ids():
    inv = build_inventory()
    assert len(inv.packages) == 54
    ids = [p.package_id for p in inv.packages]
    assert ids[0] == "P01"
    assert ids[-1] == "P54"
    assert len(set(ids)) == 54


def test_inventory_racks_per_rack():
    inv = build_inventory()
    per_rack = {}
    for p in inv.packages:
        per_rack.setdefault(p.rack, 0)
        per_rack[p.rack] += 1
    assert set(per_rack) == set(RACK_POS)
    assert all(n == 6 for n in per_rack.values())


def test_package_positions_on_rack():
    inv = build_inventory()
    for p in inv.packages:
        rx, ry = RACK_POS[p.rack]
        assert math.isclose(p.x, rx + SLOT_X[p.slot])
        assert math.isclose(p.y, ry)
        assert math.isclose(p.z, SHELF_Z[p.shelf] + 0.15)
        # packages must stay within the rack footprint (2.0 x 1.0 m)
        assert abs(p.x - rx) <= 0.9
        assert abs(p.y - ry) <= 0.4


def test_package_colors():
    assert len(PACKAGE_COLORS) >= 4
    for r, g, b in PACKAGE_COLORS:
        assert 0 <= r <= 1 and 0 <= g <= 1 and 0 <= b <= 1


def test_nearest_rack():
    assert nearest_rack(-4.0, 2.2) == "A1"
    assert nearest_rack(0.0, 0.0) == "B2"
    assert nearest_rack(4.0, -3.0) == "C3"


def test_task_to_package_selects_rack_package():
    inv = build_inventory()
    task = task_to_package(inv, (-4.0, 2.2), (2.0, -7.0), "T1")
    assert task.package is not None
    assert task.package.rack == "A1"
    assert task.package.status == "reserved"


def test_task_to_package_no_double_pick():
    inv = build_inventory()
    t1 = task_to_package(inv, (-4.0, 2.2), (2.0, -7.0), "T1")
    t2 = task_to_package(inv, (-4.0, 2.2), (2.0, -7.0), "T2")
    assert t1.package is not None and t2.package is not None
    assert t1.package.package_id != t2.package.package_id


def test_task_to_package_remote_pickup():
    # a pickup far from every rack (beyond nearest_free max_dist) -> no package
    inv = build_inventory()
    task = task_to_package(inv, (-9.5, 9.5), (2.0, -7.0), "T3")
    assert task.package is None


# ---------------------------------------------------------------------------
# SDF builders
# ---------------------------------------------------------------------------

def test_package_model_sdf():
    sdf = viz_sdf.package_model("P01", (1.0, 0.0, 0.0))
    assert "model name=" in sdf and "box" in sdf and "0.3" in sdf


def test_path_model_geometry():
    sdf = viz_sdf.path_model("p", (0, 0, 1), [(0, 0), (2, 0), (2, 2)])
    assert sdf and sdf.count("<visual") == 2


def test_path_model_too_short():
    assert viz_sdf.path_model("p", (0, 0, 1), [(0, 0)]) is None


def test_text_model_crossed_panels():
    sdf = viz_sdf.text_model("t", "/tmp/x.png", 1.0, 0.4)
    assert sdf and sdf.count("<visual") == 2


def test_make_text_texture(tmp_path, monkeypatch):
    monkeypatch.setattr(viz_sdf, "_TEXTURES_DIR", str(tmp_path))
    path, w, h = viz_sdf.make_text_texture("PICKUP P15", "t1")
    import os
    assert os.path.exists(path)
    assert w > 0.2 and h > 0.1
