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


def voxelize_osm_buildings(spec: GridSpec, origin: tuple[float, float, float],
                           buildings: list[dict]) -> np.ndarray:
    """Conservative occupancy from OSM footprint polygons and tagged/estimated height.

    Coordinates are WGS84 lon/lat, projected to a local east/north tangent
    approximation over this small (320 m) study area. Unknown heights are
    estimates, not surveyed measurements. Missing buildings remain unknown.
    """
    from math import cos, radians, floor, ceil
    grid = np.ones((spec.width, spec.depth, spec.height), dtype=np.int8)
    lon0, lat0, _ = origin
    east_scale = 111320.0 * cos(radians(lat0))
    north_scale = 111132.0

    def inside(px: float, py: float, polygon: list[tuple[float, float]]) -> bool:
        hit = False
        for i in range(len(polygon)):
            ax, ay = polygon[i]
            bx, by = polygon[(i + 1) % len(polygon)]
            if ((ay > py) != (by > py)) and (
                px < (bx - ax) * (py - ay) / (by - ay) + ax
            ):
                hit = not hit
        return hit

    def segments_cross(a, b, c, d) -> bool:
        def turn(p, q, t):
            return (q[0] - p[0]) * (t[1] - p[1]) - (q[1] - p[1]) * (t[0] - p[0])
        return turn(a, b, c) * turn(a, b, d) <= 0 and turn(c, d, a) * turn(c, d, b) <= 0

    for building in buildings:
        polygon = [((lon - lon0) * east_scale, (lat - lat0) * north_scale)
                   for lon, lat in building["coordinates"]]
        if len(polygon) < 3:
            continue
        h = float(building["height_m"])
        lower = max(0, min(spec.height, floor(float(building.get("min_height_m", 0)) / spec.voxel_size_m)))
        upper = max(0, min(spec.height, ceil(h / spec.voxel_size_m)))
        if upper <= lower:
            continue
        x0 = max(0, floor(min(p[0] for p in polygon) / spec.voxel_size_m))
        x1 = min(spec.width, ceil(max(p[0] for p in polygon) / spec.voxel_size_m))
        y0 = max(0, floor(min(p[1] for p in polygon) / spec.voxel_size_m))
        y1 = min(spec.depth, ceil(max(p[1] for p in polygon) / spec.voxel_size_m))
        for x in range(x0, x1):
            for y in range(y0, y1):
                a, b = x * spec.voxel_size_m, y * spec.voxel_size_m
                c, d = a + spec.voxel_size_m, b + spec.voxel_size_m
                corners = [(a, b), (c, b), (c, d), (a, d)]
                occupied = (
                    any(inside(px, py, polygon) for px, py in corners)
                    or any(a <= px <= c and b <= py <= d for px, py in polygon)
                    or any(segments_cross(corners[j], corners[(j + 1) % 4],
                                          polygon[k], polygon[(k + 1) % len(polygon)])
                           for j in range(4) for k in range(len(polygon)))
                )
                if occupied:
                    grid[x, y, lower:upper] = 0
    return grid
