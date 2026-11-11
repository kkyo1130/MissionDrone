from __future__ import annotations

import heapq
import itertools
import math
import time
from typing import Dict, List, MutableMapping, Optional, Tuple

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
    return math.dist(a, b)


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


def _segment_box_distance(
    start: Tuple[float, float, float],
    end: Tuple[float, float, float],
    minimum: Tuple[float, float, float],
    maximum: Tuple[float, float, float],
) -> float:
    delta = tuple(b - a for a, b in zip(start, end))
    breaks = {0.0, 1.0}
    for axis in range(3):
        if delta[axis] == 0:
            continue
        for boundary in (minimum[axis], maximum[axis]):
            time = (boundary - start[axis]) / delta[axis]
            if 0 < time < 1:
                breaks.add(time)

    times = sorted(breaks)
    candidates = list(times)
    for lower, upper in zip(times, times[1:]):
        midpoint = (lower + upper) / 2
        quadratic = 0.0
        linear = 0.0
        for axis in range(3):
            value = start[axis] + midpoint * delta[axis]
            boundary = (
                minimum[axis]
                if value < minimum[axis]
                else maximum[axis]
                if value > maximum[axis]
                else None
            )
            if boundary is not None:
                quadratic += delta[axis] ** 2
                linear += delta[axis] * (start[axis] - boundary)
        if quadratic:
            candidates.append(max(lower, min(upper, -linear / quadratic)))

    return min(
        math.sqrt(
            sum(
                (
                    max(
                        minimum[axis] - value,
                        0.0,
                        value - maximum[axis],
                    )
                )
                ** 2
                for axis, value in enumerate(
                    start[axis] + time * delta[axis] for axis in range(3)
                )
            )
        )
        for time in candidates
    )


def _transition_clear(
    occupancy: np.ndarray,
    start: Point3D,
    end: Point3D,
    clearance_voxels: float,
) -> bool:
    delta = tuple(b - a for a, b in zip(start, end))
    changed_axes = [axis for axis, value in enumerate(delta) if value]

    # Check every cell touched by a diagonal transition to prevent corner cutting.
    if all(abs(value) <= 1 for value in delta):
        for mask in range(1, (1 << len(changed_axes)) - 1):
            cell = list(start)
            for bit, axis in enumerate(changed_axes):
                if mask & (1 << bit):
                    cell[axis] += delta[axis]
            if occupancy[tuple(cell)] == 0:
                return False

    shape = occupancy.shape
    ranges = []
    for axis in range(3):
        lower = math.floor(min(start[axis], end[axis]) - clearance_voxels - 0.5)
        upper = math.ceil(max(start[axis], end[axis]) + clearance_voxels + 0.5)
        ranges.append(range(max(0, lower), min(shape[axis] - 1, upper) + 1))

    bounds = tuple(slice(axis[0], axis[-1] + 1) for axis in ranges)
    nearby_occupancy = occupancy[bounds]
    if np.all(nearby_occupancy != 0):
        return True
    nearby_blocked = np.argwhere(nearby_occupancy == 0)
    offsets = tuple(axis[0] for axis in ranges)
    for cell in nearby_blocked:
        x, y, z = (int(value + offset) for value, offset in zip(cell, offsets))
        distance = _segment_box_distance(
            start,
            end,
            (x - 0.5, y - 0.5, z - 0.5),
            (x + 0.5, y + 0.5, z + 0.5),
        )
        if distance <= clearance_voxels + 1e-9:
            return False
    return True


def _turn_allowed(
    previous: Point3D, current: Point3D, following: Point3D, max_turn_deg: float
) -> bool:
    incoming = tuple(b - a for a, b in zip(previous, current))
    outgoing = tuple(b - a for a, b in zip(current, following))
    denominator = math.sqrt(
        sum(value * value for value in incoming)
        * sum(value * value for value in outgoing)
    )
    if denominator == 0:
        return True
    cosine = sum(a * b for a, b in zip(incoming, outgoing)) / denominator
    return cosine >= math.cos(math.radians(max_turn_deg)) - 1e-9


def _flight_neighbors(
    node: Point3D,
    shape: Tuple[int, int, int],
    max_climb_fraction: float,
):
    x, y, z = node
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx or dy:
                neighbor = (x + dx, y + dy, z)
                if all(0 <= value < limit for value, limit in zip(neighbor, shape)):
                    yield neighbor

    if max_climb_fraction <= 0:
        return
    max_horizontal_step = math.ceil(
        math.sqrt(max(0.0, 1.0 / max_climb_fraction**2 - 1.0))
    )
    for dz in (-1, 1):
        for dx in range(-max_horizontal_step, max_horizontal_step + 1):
            for dy in range(-max_horizontal_step, max_horizontal_step + 1):
                distance = math.sqrt(dx * dx + dy * dy + 1)
                if 1.0 / distance > max_climb_fraction + 1e-9:
                    continue
                neighbor = (x + dx, y + dy, z + dz)
                if all(0 <= value < limit for value, limit in zip(neighbor, shape)):
                    yield neighbor


def _segment_average(
    values: np.ndarray, start: Point3D, end: Point3D
) -> float:
    if _distance(start, end) <= math.sqrt(3.0):
        return (float(values[start]) + float(values[end])) / 2.0
    midpoint = tuple(int(round((a + b) / 2.0)) for a, b in zip(start, end))
    return (
        float(values[start])
        + 2.0 * float(values[midpoint])
        + float(values[end])
    ) / 4.0


def find_path_weighted_astar(
    occupancy: np.ndarray,
    risk_map: np.ndarray,
    start: Point3D,
    end: Point3D,
    *,
    previous: Optional[Point3D],
    mission_penalty_map: np.ndarray,
    mission_penalty_factor: float,
    distance_time_cost_factor: float,
    energy_cost_factor: float,
    energy_base_power_w: float,
    risk_cost_factor: float,
    overlap_penalty: np.ndarray,
    overlap_penalty_factor: float,
    clearance_voxels: float,
    max_turn_deg: float,
    max_climb_fraction: float,
    min_altitude_index: int,
    max_altitude_index: int,
    transition_cache: MutableMapping[
        Tuple[Point3D, Point3D], bool
    ],
    deadline: Optional[float] = None,
    segment_cache: Optional[dict] = None,
) -> Optional[List[Point3D]]:
    """Find a clearance-aware route while accounting for risk and prior routes."""
    if (
        occupancy[start] == 0
        or occupancy[end] == 0
        or not min_altitude_index <= start[2] <= max_altitude_index
        or not min_altitude_index <= end[2] <= max_altitude_index
    ):
        return None

    if segment_cache is None:
        segment_cache = {}
    initial = (start, previous)
    serial = itertools.count()
    minimum_movement_cost = distance_time_cost_factor + energy_cost_factor
    frontier = [
        (
            _distance(start, end) * minimum_movement_cost,
            0.0,
            next(serial),
            initial,
        )
    ]
    came_from = {}
    cost_so_far = {initial: 0.0}

    while frontier:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Route search time budget exceeded")
        _, current_cost, _, state = heapq.heappop(frontier)
        if current_cost != cost_so_far.get(state):
            continue

        current, previous = state
        if current == end:
            path = []
            while state in came_from:
                path.append(state[0])
                state = came_from[state]
            path.append(start)
            path.reverse()
            return path

        for neighbor in _flight_neighbors(
            current, occupancy.shape, max_climb_fraction
        ):
            if not min_altitude_index <= neighbor[2] <= max_altitude_index:
                continue
            transition = tuple(sorted((current, neighbor)))
            if transition not in transition_cache:
                transition_cache[transition] = _transition_clear(
                    occupancy, current, neighbor, clearance_voxels
                )
            if not transition_cache[transition]:
                continue
            if previous is not None and not _turn_allowed(
                previous, current, neighbor, max_turn_deg
            ):
                continue

            if transition not in segment_cache:
                segment_cache[transition] = (
                    _distance(current, neighbor),
                    max(0.0, _segment_average(risk_map, current, neighbor)),
                    max(0.0, _segment_average(mission_penalty_map, current, neighbor)),
                )
            step_distance, risk, mission_penalty = segment_cache[transition]
            if abs(neighbor[2] - current[2]) / step_distance > (
                max_climb_fraction + 1e-9
            ):
                continue
            overlap = max(
                0.0, _segment_average(overlap_penalty, current, neighbor)
            )
            climb_fraction = max(0, neighbor[2] - current[2]) / step_distance
            energy_factor = 1.0 + climb_fraction * 100.0 / energy_base_power_w
            step_cost = step_distance * (
                distance_time_cost_factor
                + energy_cost_factor * energy_factor
                + risk_cost_factor * risk
                + overlap_penalty_factor * overlap
                + mission_penalty_factor * mission_penalty
            )
            new_cost = current_cost + step_cost
            next_state = (neighbor, current)
            if new_cost >= cost_so_far.get(next_state, float("inf")):
                continue

            cost_so_far[next_state] = new_cost
            came_from[next_state] = state
            priority = new_cost + _distance(neighbor, end) * minimum_movement_cost
            heapq.heappush(
                frontier, (priority, new_cost, next(serial), next_state)
            )

    return None
