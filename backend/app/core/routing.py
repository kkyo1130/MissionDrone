from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.core.pathfinding import (
    _segment_average,
    _segment_box_distance,
    _turn_allowed,
    find_path_weighted_astar,
)
from app.schemas.missions import (
    DeliveryMission,
    DisasterMission,
    InspectionMission,
    MissionPayload,
    NoFlyZone,
)

Point3D = Tuple[int, int, int]

_RISK_VARIANTS = (1.0, 2.0, 0.5, 4.0, 0.25, 8.0, 0.125)
_OVERLAP_LIMIT = 0.85
_MISSION_PENALTY_FACTOR = 0.25


def _mission_penalty_map(
    mission: MissionPayload, shape: Tuple[int, int, int], voxel_size_m: float
) -> np.ndarray:
    objectives = [
        (point, 0.0)
        for point in mission.waypoints
        if point not in mission.required_waypoints
    ]
    if isinstance(mission, InspectionMission):
        objectives.extend(
            (point, mission.view_distance_m / voxel_size_m)
            for point in mission.target_points
        )
    elif isinstance(mission, DisasterMission):
        objectives.extend((point, 1.0) for point in mission.search_area)
        objectives.extend((point, 1.0) for point in mission.priority_points)

    penalties = np.ones(shape, dtype=np.float32)
    if not objectives:
        return np.zeros(shape, dtype=np.float32)

    coordinates = np.indices(shape, dtype=np.float32)
    last_index = tuple(limit - 1 for limit in shape)
    normalization = max(math.dist((0, 0, 0), last_index), 1.0)
    for point, radius in objectives:
        distance = np.sqrt(
            sum((coordinates[axis] - point[axis]) ** 2 for axis in range(3))
        )
        objective_penalty = np.maximum(distance - radius, 0.0) / normalization
        np.minimum(penalties, objective_penalty, out=penalties)
    return penalties


def _route_is_flightworthy(
    path: List[Point3D], mission: MissionPayload, voxel_size_m: float
) -> Tuple[bool, float, float, float]:
    constraints = mission.constraints
    distance_m = 0.0
    flight_time_s = 0.0
    energy_wh = 0.0

    for previous, current, following in zip(path, path[1:], path[2:]):
        if not _turn_allowed(previous, current, following, constraints.max_turn_deg):
            return False, distance_m, flight_time_s, energy_wh

    payload_kg = mission.payload_kg if isinstance(mission, DeliveryMission) else 0.0
    for start, end in zip(path, path[1:]):
        segment_distance_voxels = math.dist(start, end)
        segment_distance_m = segment_distance_voxels * voxel_size_m
        segment_time_s = segment_distance_m / constraints.speed_mps
        climb_rate_mps = (
            abs(end[2] - start[2]) * voxel_size_m / segment_time_s
        )
        if climb_rate_mps > constraints.climb_rate_mps + 1e-9:
            return False, distance_m, flight_time_s, energy_wh

        direction_z = (
            (end[2] - start[2]) / segment_distance_voxels
            if segment_distance_voxels
            else 0.0
        )
        power_w = (
            180.0
            + 25.0 * payload_kg
            + 4.0 * constraints.speed_mps**2
            + max(0.0, direction_z) * 100.0
        )
        distance_m += segment_distance_m
        flight_time_s += segment_time_s
        energy_wh += power_w * segment_time_s / 3600.0

    feasible = energy_wh <= constraints.battery_budget_wh
    if isinstance(mission, DisasterMission):
        feasible = feasible and flight_time_s <= mission.time_limit_s
    return feasible, distance_m, flight_time_s, energy_wh


def _geodetic(
    east_m: float, north_m: float, up_m: float, origin_wgs84: Tuple[float, float, float]
) -> Dict[str, float]:
    semi_major_axis = 6378137.0
    eccentricity_squared = 6.69437999014e-3
    origin_lon, origin_lat, origin_alt = origin_wgs84
    latitude = math.radians(origin_lat)
    longitude = math.radians(origin_lon)
    sin_lat, cos_lat = math.sin(latitude), math.cos(latitude)
    sin_lon, cos_lon = math.sin(longitude), math.cos(longitude)
    prime_vertical = semi_major_axis / math.sqrt(
        1.0 - eccentricity_squared * sin_lat**2
    )

    x = (
        (prime_vertical + origin_alt) * cos_lat * cos_lon
        - sin_lon * east_m
        - sin_lat * cos_lon * north_m
        + cos_lat * cos_lon * up_m
    )
    y = (
        (prime_vertical + origin_alt) * cos_lat * sin_lon
        + cos_lon * east_m
        - sin_lat * sin_lon * north_m
        + cos_lat * sin_lon * up_m
    )
    z = (
        (prime_vertical * (1.0 - eccentricity_squared) + origin_alt) * sin_lat
        + cos_lat * north_m
        + sin_lat * up_m
    )

    radius = math.hypot(x, y)
    result_lat = math.atan2(z, radius * (1.0 - eccentricity_squared))
    for _ in range(10):
        prime_vertical = semi_major_axis / math.sqrt(
            1.0 - eccentricity_squared * math.sin(result_lat) ** 2
        )
        result_lat = math.atan2(
            z + eccentricity_squared * prime_vertical * math.sin(result_lat),
            radius,
        )
    prime_vertical = semi_major_axis / math.sqrt(
        1.0 - eccentricity_squared * math.sin(result_lat) ** 2
    )
    altitude = radius / math.cos(result_lat) - prime_vertical
    return {
        "longitude": math.degrees(math.atan2(y, x)),
        "latitude": math.degrees(result_lat),
        "altitude": altitude,
    }


def _minimum_clearance_m(
    path: List[Point3D], occupancy: np.ndarray, voxel_size_m: float
) -> Optional[float]:
    blocked = np.argwhere(occupancy == 0)
    if not len(blocked):
        return None

    minimum_distance = float("inf")
    for start, end in zip(path, path[1:]):
        for x, y, z in blocked:
            distance = _segment_box_distance(
                start,
                end,
                (x - 0.5, y - 0.5, z - 0.5),
                (x + 0.5, y + 0.5, z + 0.5),
            )
            minimum_distance = min(minimum_distance, distance)
    return minimum_distance * voxel_size_m


def _make_route(
    path: List[Point3D],
    mission: MissionPayload,
    risk_map: np.ndarray,
    occupancy: np.ndarray,
    voxel_size_m: float,
    origin_wgs84: Tuple[float, float, float],
) -> Dict[str, Any]:
    _, distance_m, flight_time_s, energy_wh = _route_is_flightworthy(
        path, mission, voxel_size_m
    )
    segment_distances = [
        math.dist(start, end) * voxel_size_m
        for start, end in zip(path, path[1:])
    ]
    risk_exposure = sum(
        _segment_average(risk_map, start, end) * distance
        for start, end, distance in zip(path, path[1:], segment_distances)
    )
    average_risk = risk_exposure / distance_m if distance_m else 0.0
    points = []
    for point in path:
        east, north, up = ((index + 0.5) * voxel_size_m for index in point)
        points.append(
            {
                "voxel": list(point),
                "enu_m": [east, north, up],
                "wgs84": _geodetic(east, north, up, origin_wgs84),
            }
        )
    return {
        "points": [list(point) for point in path],
        "coordinates": points,
        "distance_m": distance_m,
        "estimated_time_s": flight_time_s,
        "estimated_energy_wh": energy_wh,
        "average_risk": average_risk,
        "risk_exposure": risk_exposure,
        "minimum_clearance_m": _minimum_clearance_m(path, occupancy, voxel_size_m),
    }


def generate_route_candidates(
    mission: MissionPayload,
    occupancy: np.ndarray,
    risk_map: np.ndarray,
    *,
    voxel_size_m: float,
    origin_wgs84: Tuple[float, float, float],
    count: int,
    no_fly_zones: List[NoFlyZone],
    time_budget_s: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    deadline = None if time_budget_s is None else time.monotonic() + time_budget_s
    timed_out = False
    planning_grid = occupancy.copy()
    for zone in no_fly_zones:
        if any(
            lower < 0 or upper >= limit
            for lower, upper, limit in zip(zone.minimum, zone.maximum, planning_grid.shape)
        ):
            raise ValueError("no-fly zone is outside the environment grid")
        slices = tuple(
            slice(lower, upper + 1) for lower, upper in zip(zone.minimum, zone.maximum)
        )
        planning_grid[slices] = 0

    anchors = [mission.start, *mission.required_waypoints, mission.end]
    clearance_voxels = (
        mission.constraints.clearance_m
        + mission.constraints.drone_radius_m
        + mission.constraints.position_error_m
    ) / voxel_size_m
    minimum_altitude_index = math.ceil(
        mission.constraints.min_altitude_m / voxel_size_m - 0.5
    )
    maximum_altitude_index = math.floor(
        mission.constraints.max_altitude_m / voxel_size_m - 0.5
    )
    if any(
        planning_grid[anchor] == 0
        or not minimum_altitude_index <= anchor[2] <= maximum_altitude_index
        or not _transition_is_clear(planning_grid, anchor, clearance_voxels)
        for anchor in anchors
    ):
        return [], (
            "A start, end, or required waypoint violates obstacle clearance, "
            "a no-fly zone, or the altitude limits."
        )
    weights = mission.cost_weights
    mission_penalties = _mission_penalty_map(mission, risk_map.shape, voxel_size_m)
    payload_kg = mission.payload_kg if isinstance(mission, DeliveryMission) else 0.0
    energy_base_power_w = (
        180.0 + 25.0 * payload_kg + 4.0 * mission.constraints.speed_mps**2
    )
    minimum_energy_wh = sum(
        math.dist(start, end)
        * voxel_size_m
        / mission.constraints.speed_mps
        * energy_base_power_w
        / 3600.0
        for start, end in zip(anchors, anchors[1:])
    )
    if minimum_energy_wh > mission.constraints.battery_budget_wh:
        return [], "Battery budget is insufficient even for the direct waypoint route."
    minimum_time_s = sum(
        math.dist(start, end)
        * voxel_size_m
        / mission.constraints.speed_mps
        for start, end in zip(anchors, anchors[1:])
    )
    if isinstance(mission, DisasterMission) and minimum_time_s > mission.time_limit_s:
        return [], "Time limit is insufficient even for the direct waypoint route."

    routes: List[Dict[str, Any]] = []
    overlap_penalty = np.zeros_like(risk_map, dtype=np.float32)
    anchor_set = set(anchors)
    transition_cache = {}
    segment_cache = {}
    # Search progressively: first prefer separated routes, then accept similar
    # but genuinely different safe routes. Never duplicate an identical path.
    for variant in range(count * len(_RISK_VARIANTS) * 4):
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            break
        risk_factor = weights.risk * _RISK_VARIANTS[variant % len(_RISK_VARIANTS)]
        path = [anchors[0]]
        for start, end in zip(anchors, anchors[1:]):
            previous = path[-2] if len(path) > 1 else None
            try:
                leg = find_path_weighted_astar(
                    planning_grid,
                    risk_map,
                    start,
                    end,
                    previous=previous,
                    mission_penalty_map=mission_penalties,
                    mission_penalty_factor=_MISSION_PENALTY_FACTOR,
                    distance_time_cost_factor=weights.distance + weights.time,
                    energy_cost_factor=weights.energy,
                    energy_base_power_w=energy_base_power_w,
                    risk_cost_factor=risk_factor,
                    overlap_penalty=overlap_penalty,
                    overlap_penalty_factor=2.0 + variant // len(_RISK_VARIANTS),
                    clearance_voxels=clearance_voxels,
                    max_turn_deg=mission.constraints.max_turn_deg,
                    max_climb_fraction=(
                        mission.constraints.climb_rate_mps / mission.constraints.speed_mps
                    ),
                    min_altitude_index=minimum_altitude_index,
                    max_altitude_index=maximum_altitude_index,
                    transition_cache=transition_cache,
                    deadline=deadline,
                    segment_cache=segment_cache,
                )
            except TimeoutError:
                timed_out = True
                path = []
                break
            if leg is None:
                path = []
                break
            path.extend(leg[1:])

        if timed_out:
            break
        if len(path) < 2:
            continue
        flightworthy, _, _, _ = _route_is_flightworthy(
            path, mission, voxel_size_m
        )
        # Penalize repeated search results as well as accepted routes so that
        # a rejected near-duplicate does not consume every remaining attempt.
        for point in path[1:-1]:
            if point not in anchor_set:
                overlap_penalty[point] += 1.0
        if not flightworthy:
            continue

        path_set = set(path[1:-1]) - anchor_set
        is_duplicate = False
        for route in routes:
            existing_path = tuple(map(tuple, route["points"]))
            if tuple(path) == existing_path:
                is_duplicate = True
                break
            existing_set = set(existing_path[1:-1]) - anchor_set
            union = path_set | existing_set
            similarity = len(path_set & existing_set) / max(1, len(union))
            round_index = variant // (count * len(_RISK_VARIANTS))
            overlap_limit = _OVERLAP_LIMIT if round_index == 0 else 0.97 if round_index == 1 else 1.0
            if similarity > overlap_limit:
                is_duplicate = True
                break
        if is_duplicate:
            continue

        route = _make_route(
            path, mission, risk_map, planning_grid, voxel_size_m, origin_wgs84
        )
        routes.append(route)
        if len(routes) == count:
            break

    if len(routes) == count:
        return routes, None
    if timed_out:
        return routes, (
            f"탐색 제한 시간({time_budget_s:g}초)에 도달하여 "
            f"확보한 후보 {len(routes)}개를 반환했습니다. 요청: {count}개."
        )
    if not routes:
        reason = (
            "No route satisfies obstacle clearance, altitude, turn, climb-rate, "
            "and battery constraints."
        )
    else:
        reason = (
            f"안전 조건을 만족하는 서로 다른 후보 {len(routes)}개를 확보했습니다. "
            f"요청: {count}개. 추가 탐색에서도 후보를 확보하지 못했습니다."
        )
    return routes, reason


def _transition_is_clear(
    occupancy: np.ndarray, point: Point3D, clearance_voxels: float
) -> bool:
    for x, y, z in np.argwhere(occupancy == 0):
        distance = _segment_box_distance(
            point,
            point,
            (x - 0.5, y - 0.5, z - 0.5),
            (x + 0.5, y + 0.5, z + 0.5),
        )
        if distance <= clearance_voxels + 1e-9:
            return False
    return True
