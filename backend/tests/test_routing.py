import math

from fastapi.testclient import TestClient

from app.core.pathfinding import find_path_weighted_astar
from app.core.routing import _mission_penalty_map
from app.main import OCCUPANCY, GRID_SPEC, app
from app.schemas.missions import DeliveryMission

client = TestClient(app)


def _create_delivery_mission(battery_budget_wh=180):
    response = client.post(
        "/api/missions",
        json={
            "mission_type": "delivery",
            "name": "F03 route test",
            "start": [2, 2, 8],
            "end": [28, 28, 8],
            "waypoints": [[12, 12, 8]],
            "required_waypoints": [[12, 12, 8]],
            "constraints": {
                "min_altitude_m": 10,
                "max_altitude_m": 100,
                "speed_mps": 12,
                "climb_rate_mps": 3,
                "battery_budget_wh": battery_budget_wh,
            },
            "payload_kg": 2,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_generate_and_retrieve_distinct_safe_candidates():
    mission_id = _create_delivery_mission()

    response = client.post(
        f"/api/missions/{mission_id}/routes",
        json={"count": 3, "wind_speed": 5, "wind_direction": 90},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["generated_count"] == 3
    assert result["reason"] is None
    assert len({tuple(map(tuple, route["points"])) for route in result["routes"]}) == 3

    for route in result["routes"]:
        points = route["points"]
        assert points[0] == [2, 2, 8]
        assert points[-1] == [28, 28, 8]
        assert [12, 12, 8] in points
        assert all(OCCUPANCY[tuple(point)] == 1 for point in points)
        assert route["distance_m"] > 0
        assert route["estimated_time_s"] > 0
        assert route["estimated_energy_wh"] < 180
        assert 0 <= route["average_risk"] <= 1
        # No known obstacle in the isolated CI grid means clearance is
        # undefined, not zero; OSM-loaded grids report a numeric distance.
        clearance = route["minimum_clearance_m"]
        assert clearance is None or clearance > 5
        assert len(route["coordinates"]) == len(points)
        assert route["coordinates"][0]["wgs84"]["latitude"] > 0

    retrieved = client.get(f"/api/missions/{mission_id}/routes")
    assert retrieved.status_code == 200
    assert retrieved.json() == result


def test_no_fly_zones_and_unavailable_route_reason():
    mission_id = _create_delivery_mission()
    response = client.post(
        f"/api/missions/{mission_id}/routes",
        json={
            "count": 3,
            "no_fly_zones": [
                {"minimum": [18, 18, 8], "maximum": [21, 21, 8]}
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["generated_count"] == 3
    assert all(
        not (18 <= x <= 21 and 18 <= y <= 21 and z == 8)
        for route in response.json()["routes"]
        for x, y, z in route["points"]
    )

    blocked = client.post(
        f"/api/missions/{mission_id}/routes",
        json={
            "count": 3,
            "no_fly_zones": [
                {"minimum": [0, 0, 0], "maximum": [GRID_SPEC.width - 1, GRID_SPEC.depth - 1, GRID_SPEC.height - 1]}
            ],
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["generated_count"] == 0
    assert blocked.json()["reason"]


def test_route_generation_rejects_bad_request_and_respects_battery():
    mission_id = _create_delivery_mission(battery_budget_wh=0.001)
    invalid_count = client.post(
        f"/api/missions/{mission_id}/routes", json={"count": 2}
    )
    assert invalid_count.status_code == 422

    exhausted = client.post(
        f"/api/missions/{mission_id}/routes", json={"count": 3}
    )
    assert exhausted.status_code == 200
    assert exhausted.json()["generated_count"] == 0
    assert exhausted.json()["reason"]

    invalid_zone = client.post(
        f"/api/missions/{mission_id}/routes",
        json={"no_fly_zones": [{"minimum": [GRID_SPEC.width, 0, 0], "maximum": [GRID_SPEC.width, 1, 1]}]},
    )
    assert invalid_zone.status_code == 422


def test_route_retrieval_handles_unknown_and_not_yet_planned_missions():
    assert client.get("/api/missions/missing/routes").status_code == 404

    mission_id = _create_delivery_mission()
    result = client.get(f"/api/missions/{mission_id}/routes")
    assert result.status_code == 200
    assert result.json()["generated_count"] == 0
    assert result.json()["routes"] == []


def test_astar_uses_shallow_segments_for_climb_rate_limits():
    import numpy as np

    grid = np.ones((12, 3, 4), dtype=np.int8)
    path = find_path_weighted_astar(
        grid,
        np.zeros_like(grid, dtype=np.float32),
        (1, 1, 1),
        (10, 1, 2),
        previous=None,
        mission_penalty_map=np.zeros_like(grid, dtype=np.float32),
        mission_penalty_factor=0.25,
        distance_time_cost_factor=0.5,
        energy_cost_factor=0.25,
        energy_base_power_w=828,
        risk_cost_factor=0.25,
        overlap_penalty=np.zeros_like(grid, dtype=np.float32),
        overlap_penalty_factor=2,
        clearance_voxels=0.5,
        max_turn_deg=135,
        max_climb_fraction=0.25,
        min_altitude_index=0,
        max_altitude_index=3,
        transition_cache={},
    )

    assert path is not None
    assert path[0] == (1, 1, 1)
    assert path[-1] == (10, 1, 2)
    assert all(
        abs(end[2] - start[2]) / math.dist(start, end) <= 0.25 + 1e-9
        for start, end in zip(path, path[1:])
    )


def test_optional_waypoints_are_soft_mission_objectives():
    mission = DeliveryMission.model_validate(
        {
            "mission_type": "delivery",
            "name": "soft waypoint",
            "start": [0, 0, 1],
            "end": [7, 2, 1],
            "waypoints": [[5, 1, 1]],
            "constraints": {
                "min_altitude_m": 0,
                "max_altitude_m": 100,
                "speed_mps": 10,
                "climb_rate_mps": 3,
                "battery_budget_wh": 100,
            },
            "payload_kg": 0,
        }
    )

    penalties = _mission_penalty_map(mission, (8, 3, 3), 10)

    assert penalties[5, 1, 1] == 0
    assert penalties[0, 1, 1] > penalties[5, 1, 1]


def test_timeout_returns_completed_candidates(monkeypatch):
    import numpy as np
    from app.core import routing

    mission = DeliveryMission.model_validate({
        'mission_type': 'delivery', 'name': 'bounded search',
        'start': [1, 1, 1], 'end': [3, 1, 1], 'payload_kg': 1,
        'constraints': {'min_altitude_m': 0, 'max_altitude_m': 40,
                        'speed_mps': 10, 'climb_rate_mps': 3,
                        'battery_budget_wh': 100},
    })
    grid = np.ones((5, 5, 4), dtype=np.int8)
    calls = []

    def search(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return [(1, 1, 1), (2, 1, 1), (3, 1, 1)]
        raise TimeoutError

    monkeypatch.setattr(routing, 'find_path_weighted_astar', search)
    routes, reason = routing.generate_route_candidates(
        mission, grid, np.zeros_like(grid, dtype=float),
        voxel_size_m=10, origin_wgs84=(126.62, 37.37, 0),
        count=3, no_fly_zones=[], time_budget_s=10,
    )
    assert len(routes) == 1
    assert '제한 시간' in reason
    assert calls[0]['deadline'] == calls[1]['deadline']
    assert calls[0]['segment_cache'] is calls[1]['segment_cache']

    routes, reason = routing.generate_route_candidates(
        mission, grid, np.zeros_like(grid, dtype=float),
        voxel_size_m=10, origin_wgs84=(126.62, 37.37, 0),
        count=3, no_fly_zones=[], time_budget_s=0,
    )
    assert routes == []
    assert '제한 시간' in reason
    assert len(calls) == 2


def test_extended_search_returns_five_real_safe_routes():
    import numpy as np
    from app.core import routing
    mission = DeliveryMission.model_validate({
        'mission_type': 'delivery', 'name': 'five alternatives',
        'start': [1, 1, 1], 'end': [10, 10, 1], 'payload_kg': 0,
        'constraints': {'min_altitude_m': 0, 'max_altitude_m': 40,
                        'speed_mps': 10, 'climb_rate_mps': 10,
                        'clearance_m': 0, 'drone_radius_m': 0.1,
                        'position_error_m': 0, 'max_turn_deg': 180,
                        'battery_budget_wh': 100},
    })
    grid = np.ones((12, 12, 3), dtype=np.int8)
    routes, reason = routing.generate_route_candidates(
        mission, grid, np.zeros_like(grid, dtype=float), count=5,
        voxel_size_m=10, origin_wgs84=(126.62, 37.37, 0), no_fly_zones=[])
    assert len(routes) == 5
    assert reason is None
    assert len({tuple(map(tuple, r['points'])) for r in routes}) == 5
    assert all(r['estimated_energy_wh'] <= 100 for r in routes)


def test_duplicate_search_results_are_penalized_without_fabricating_candidates(monkeypatch):
    import numpy as np
    from app.core import routing
    mission = DeliveryMission.model_validate({
        'mission_type': 'delivery', 'name': 'single corridor',
        'start': [1, 1, 1], 'end': [3, 1, 1], 'payload_kg': 0,
        'constraints': {'min_altitude_m': 0, 'max_altitude_m': 40,
                        'speed_mps': 10, 'climb_rate_mps': 3,
                        'battery_budget_wh': 100},
    })
    penalties = []
    def search(*args, **kwargs):
        assert kwargs['deadline'] is None
        penalties.append(float(kwargs['overlap_penalty'][2, 1, 1]))
        return [(1, 1, 1), (2, 1, 1), (3, 1, 1)]
    monkeypatch.setattr(routing, 'find_path_weighted_astar', search)
    grid = np.ones((5, 5, 4), dtype=np.int8)
    routes, reason = routing.generate_route_candidates(
        mission, grid, np.zeros_like(grid, dtype=float), count=3,
        voxel_size_m=10, origin_wgs84=(126.62, 37.37, 0), no_fly_zones=[])
    assert len(routes) == 1
    assert reason
    assert penalties[-1] > penalties[0]
    assert len(penalties) > 3 * len(routing._RISK_VARIANTS)
