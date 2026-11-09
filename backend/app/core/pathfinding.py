from __future__ import annotations

import heapq
from typing import Dict, List, Optional, Tuple

import numpy as np

Point3D = Tuple[int, int, int]


def _neighbors_26(node: Point3D, shape: Tuple[int, int, int]):
    x, y, z = node
    max_x, max_y, max_z = shape
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx == 0 and dy == 0 and dz == 0:
                    continue
                neighbor = (x + dx, y + dy, z + dz)
                if all(0 <= value < limit for value, limit in zip(neighbor, shape)):
                    yield neighbor


def _distance(a: Point3D, b: Point3D) -> float:
    return float(np.linalg.norm(np.asarray(a, dtype=np.float32) - b))


def find_path_astar(
    occupancy: np.ndarray,
    risk_map: np.ndarray,
    start: Point3D,
    end: Point3D,
    alpha: float,
) -> Optional[List[Point3D]]:
    """Find a 26-direction 3D path with risk-weighted traversal cost."""
    if occupancy[start] == 0 or occupancy[end] == 0:
        return None

    frontier: List[Tuple[float, Point3D]] = [(0.0, start)]
    came_from: Dict[Point3D, Optional[Point3D]] = {start: None}
    cost_so_far: Dict[Point3D, float] = {start: 0.0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == end:
            break

        for neighbor in _neighbors_26(current, occupancy.shape):
            if occupancy[neighbor] == 0:
                continue

            step_cost = _distance(current, neighbor)
            move_cost = step_cost + float(risk_map[neighbor]) * alpha
            new_cost = cost_so_far[current] + move_cost
            if neighbor not in cost_so_far or new_cost < cost_so_far[neighbor]:
                cost_so_far[neighbor] = new_cost
                priority = new_cost + _distance(neighbor, end)
                heapq.heappush(frontier, (priority, neighbor))
                came_from[neighbor] = current

    if end not in came_from:
        return None

    path: List[Point3D] = []
    current: Optional[Point3D] = end
    while current is not None:
        path.append(current)
        current = came_from[current]
    path.reverse()
    return path
