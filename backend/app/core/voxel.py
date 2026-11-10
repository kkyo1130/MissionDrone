from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class GridSpec:
    width: int = 32
    depth: int = 32
    height: int = 36
    voxel_size_m: int = 10


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
        # Collinear but separated edges must not block unrelated voxels.
        if (max(a[0], b[0]) < min(c[0], d[0]) or
                max(c[0], d[0]) < min(a[0], b[0]) or
                max(a[1], b[1]) < min(c[1], d[1]) or
                max(c[1], d[1]) < min(a[1], b[1])):
            return False
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
