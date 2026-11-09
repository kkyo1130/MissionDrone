from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

COMMON_MISSION = {
    "name": "Demo mission",
    "start": [2, 2, 6],
    "end": [28, 28, 6],
    "waypoints": [[10, 10, 6]],
    "required_waypoints": [[10, 10, 6]],
    "constraints": {
        "min_altitude_m": 10,
        "max_altitude_m": 100,
        "speed_mps": 12,
        "climb_rate_mps": 3,
        "battery_budget_wh": 180,
    },
}

MISSION_CASES = [
    {
        **COMMON_MISSION,
        "mission_type": "inspection",
        "target_points": [[6, 6, 2]],
        "view_distance_m": 15,
    },
    {
        **COMMON_MISSION,
        "mission_type": "disaster",
        "search_area": [[10, 10, 6], [12, 12, 6]],
        "priority_points": [[12, 12, 6]],
        "time_limit_s": 900,
    },
    {
        **COMMON_MISSION,
        "mission_type": "delivery",
        "payload_kg": 2.5,
    },
]


@pytest.mark.parametrize("payload", MISSION_CASES)
def test_create_and_get_each_mission_type(payload):
    response = client.post("/api/missions", json=payload)

    assert response.status_code == 201
    mission = response.json()
    assert mission["mission_type"] == payload["mission_type"]
    assert mission["required_waypoints"] == payload["required_waypoints"]
    assert sum(mission["cost_weights"].values()) == pytest.approx(1.0)

    retrieved = client.get(f"/api/missions/{mission['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == mission


def test_mission_validation_rejects_invalid_constraints_and_weights():
    payload = deepcopy(MISSION_CASES[2])
    payload["constraints"]["climb_rate_mps"] = 13
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["constraints"]["speed_mps"] = 0
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["constraints"]["battery_budget_wh"] = 0
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["constraints"]["min_altitude_m"] = 80
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["constraints"]["max_altitude_m"] = 130
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["required_waypoints"] = [[11, 11, 6]]
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["cost_weights"] = {
        "distance": 0.1,
        "risk": 0.2,
        "time": 0.3,
        "energy": 0.2,
    }
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["start"] = [32, 2, 6]
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["start"] = [6, 6, 0]
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["unexpected"] = True
    assert client.post("/api/missions", json=payload).status_code == 422

    payload = deepcopy(MISSION_CASES[2])
    payload["constraints"]["battery_budget_wh"] = "Infinity"
    assert client.post("/api/missions", json=payload).status_code == 422


def test_get_unknown_mission_returns_not_found():
    response = client.get("/api/missions/not-a-saved-mission")

    assert response.status_code == 404
    assert response.json()["detail"] == "Mission not found"
