"""Order-fulfillment domain logic for the warehouse digital twin.

Pure, framework-free module: no ROS, no Gazebo imports.  It describes the
package inventory (which lives on the racks as spawned visuals) and maps an
order's pickup coordinate to a rack and a concrete package so the
visualization can highlight / carry / deliver the right object.

Inventory convention (must match worlds/warehouse.world.sdf):
  * 9 racks A1..C3 at the grid below, each 2.0 (x) x 1.0 (y) x 2.0 (h).
  * 3 shelf levels at z = 0.4, 1.0, 1.6; two package slots per shelf at
    x = rack_x +/- 0.5, y = rack_y.
  * Packages P01..P54 (6 per rack), box size 0.3 m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

RACK_IDS = ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "C3"]

# rack id -> (x, y) world coordinates (rack centre)
RACK_POS: Dict[str, Tuple[float, float]] = {
    "A1": (-4.0, 3.0),
    "A2": (0.0, 3.0),
    "A3": (4.0, 3.0),
    "B1": (-4.0, 0.0),
    "B2": (0.0, 0.0),
    "B3": (4.0, 0.0),
    "C1": (-4.0, -3.0),
    "C2": (0.0, -3.0),
    "C3": (4.0, -3.0),
}

# shelf levels (board height) and slot offsets relative to rack centre
SHELF_Z = [0.4, 1.0, 1.6]
SLOT_X = [-0.5, 0.5]

PACKAGE_SIZE = 0.3
PACKAGE_LIFT = 0.42  # height above a robot base while being carried
DELIVERY_Z = 0.15  # resting height at the destination

# bright package colours, cycled so every rack looks varied
PACKAGE_COLORS = [
    (0.86, 0.20, 0.20),  # red
    (0.95, 0.60, 0.10),  # orange
    (0.95, 0.85, 0.15),  # yellow
    (0.20, 0.70, 0.35),  # green
    (0.20, 0.45, 0.90),  # blue
    (0.60, 0.30, 0.80),  # purple
]

# robot -> marker / path colour (distinct, easy to follow)
ROBOT_COLORS = {
    "robot1": (0.25, 0.55, 1.00),
    "robot2": (1.00, 0.55, 0.10),
}


@dataclass
class Package:
    package_id: str
    rack: str
    shelf: int
    slot: int
    x: float
    y: float
    z: float
    color: Tuple[float, float, float]
    status: str = "free"  # free | reserved | carried | delivered


@dataclass
class Task:
    task_id: str
    pickup: Tuple[float, float]
    dropoff: Tuple[float, float]
    package: Optional[Package] = None
    robot: str = ""
    state: str = "CREATED"  # CREATED | ASSIGNED | ACTIVE | DONE | CANCELLED


@dataclass
class Inventory:
    packages: List[Package] = field(default_factory=list)

    def by_id(self, pid: str) -> Optional[Package]:
        for p in self.packages:
            if p.package_id == pid:
                return p
        return None

    def free_on_rack(self, rack: str) -> Optional[Package]:
        for p in self.packages:
            if p.rack == rack and p.status == "free":
                return p
        return None

    def nearest_free(self, px: float, py: float, max_dist: float = 8.0):
        """Best free package for an order pickup near (px, py)."""
        best, best_d = None, max_dist
        for p in self.packages:
            if p.status != "free":
                continue
            d = math.hypot(p.x - px, p.y - py)
            if d < best_d:
                best, best_d = p, d
        return best


def build_inventory() -> Inventory:
    inv = Inventory()
    idx = 1
    for rack in RACK_IDS:
        rx, ry = RACK_POS[rack]
        for shelf in range(len(SHELF_Z)):
            for slot in range(len(SLOT_X)):
                color = PACKAGE_COLORS[idx % len(PACKAGE_COLORS)]
                inv.packages.append(
                    Package(
                        package_id=f"P{idx:02d}",
                        rack=rack,
                        shelf=shelf,
                        slot=slot,
                        x=rx + SLOT_X[slot],
                        y=ry,
                        z=SHELF_Z[shelf] + PACKAGE_SIZE / 2.0,
                        color=color,
                    )
                )
                idx += 1
    return inv


def nearest_rack(px: float, py: float) -> Optional[str]:
    best, best_d = None, float("inf")
    for rid, (rx, ry) in RACK_POS.items():
        d = math.hypot(rx - px, ry - py)
        if d < best_d:
            best, best_d = rid, d
    return best


def rack_pickup_point(rack: str) -> Tuple[float, float]:
    """A convenient pickup pose at the front of a rack (aisle side)."""
    rx, ry = RACK_POS[rack]
    if ry > 0:  # rows A / B face south
        return (rx, ry - 0.8)
    return (rx, ry + 0.8)


def task_to_package(
    inventory: Inventory,
    pickup: Tuple[float, float],
    dropoff: Tuple[float, float],
    task_id: str,
) -> Task:
    """Map an order to a package + rack, using the nearest rack to the pickup."""
    rack = nearest_rack(*pickup)
    package = inventory.nearest_free(*pickup) if rack else None
    task = Task(
        task_id=task_id,
        pickup=pickup,
        dropoff=dropoff,
        package=package,
    )
    if package is not None:
        package.status = "reserved"
    return task
