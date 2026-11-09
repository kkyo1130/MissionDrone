from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class GridSpec:
    width: int = 32
    depth: int = 32
    height: int = 12
    voxel_size_m: int = 10


def create_airspace_grid(spec: GridSpec) -> np.ndarray:
    """Create an occupancy grid where 1 is free airspace and 0 is blocked."""
    grid = np.ones((spec.width, spec.depth, spec.height), dtype=np.int8)

    buildings = [
        ((6, 6, 0), (11, 12, 6)),
        ((14, 4, 0), (20, 9, 8)),
        ((22, 16, 0), (27, 25, 7)),
        ((8, 20, 0), (13, 28, 5)),
    ]

    for (x1, y1, z1), (x2, y2, z2) in buildings:
        grid[x1:x2, y1:y2, z1:z2] = 0

    return grid
