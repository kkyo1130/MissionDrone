from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class GridSpec:
    width: int = 32
    depth: int = 32
    height: int = 12
    voxel_size_m: int = 10


# Synthetic Songdo-like district based on the rectangular obstacle volumes
# in kjyook/makerthon_2026. The same bounds power Cesium buildings,
# occupancy/collision tests, wind risk and A* route planning.
# Bounds are half-open voxel indices [min, max), not measured Songdo GIS.
BUILDING_VOLUMES = (
    {"id": "tower-a", "name": "가상 도시 A동", "min": (6, 6, 0), "max": (11, 12, 6)},
    {"id": "tower-b", "name": "가상 도시 B동", "min": (14, 4, 0), "max": (20, 9, 8)},
    {"id": "tower-c", "name": "가상 도시 C동", "min": (22, 16, 0), "max": (27, 25, 7)},
    {"id": "tower-d", "name": "가상 도시 D동", "min": (8, 20, 0), "max": (13, 28, 5)},
)


def create_airspace_grid(spec: GridSpec) -> np.ndarray:
    """Create an occupancy grid: 1=flyable, 0=building.

    Building volumes are also returned by /api/environment, ensuring the 3D
    building facade is backed by the exact same collision geometry.
    """
    grid = np.ones((spec.width, spec.depth, spec.height), dtype=np.int8)
    for building in BUILDING_VOLUMES:
        (x1, y1, z1), (x2, y2, z2) = building["min"], building["max"]
        if not (
            0 <= x1 < x2 <= spec.width
            and 0 <= y1 < y2 <= spec.depth
            and 0 <= z1 < z2 <= spec.height
        ):
            raise ValueError(f"Building {building['id']} lies outside the voxel grid")
        grid[x1:x2, y1:y2, z1:z2] = 0
    return grid
