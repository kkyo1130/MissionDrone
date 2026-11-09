"""Integration tests for synthetic weather, ranking, persistence, replay and exports."""
from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from app.main import app
import app.main as main
from app.store import Store

MISSION = {
    "mission_type": "delivery",
    "name": "Weather integration",
    "start": [2, 2, 8],
    "end": [28, 28, 8],
    "waypoints": [[12, 12, 8]],
    "required_waypoints": [[12, 12, 8]],
    "constraints": {
        "min_altitude_m": 10, "max_altitude_m": 100,
        "speed_mps": 12, "climb_rate_mps": 3, "battery_budget_wh": 180,
    },
    "payload_kg": 2,
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_store", Store("sqlite:///" + str(tmp_path / "test.db")))
    main.MISSIONS.clear()
    main.ROUTE_RESULTS.clear()
    yield TestClient(app)
    main.MISSIONS.clear()
    main.ROUTE_RESULTS.clear()


def test_experiment_end_to_end_and_replay(client):
    created = client.post("/api/missions", json=deepcopy(MISSION))
    assert created.status_code == 201, created.text
    mission_id = created.json()["id"]
    routes = client.post(f"/api/missions/{mission_id}/routes", json={"count": 3})
    assert routes.status_code == 200, routes.text
    assert routes.json()["generated_count"] == 3

    experiment = client.post(f"/api/missions/{mission_id}/experiments", json={
        "weather": [
            {"name": "calm", "wind_speed_mps": 2, "wind_from_deg": 90},
            {"name": "strong", "wind_speed_mps": 50, "wind_from_deg": 90},
        ],
        "repeats": 2, "seed": 123,
    })
    assert experiment.status_code == 201, experiment.text
    body = experiment.json()
    assert len(body["runs"]) == 12
    assert body["evaluation"]["recommended_route_id"] is None
    assert len(body["input_hash_sha256"]) == 64
    assert client.get(f"/api/experiments/{body['id']}/replay").json()["matches"] is True
    assert client.get(f"/api/experiments/{body['id']}/export?format=csv").status_code == 200
    assert client.get(f"/api/experiments/{body['id']}/export?format=json").json() == body
    assert len(client.get(f"/api/missions/{mission_id}/experiments").json()) == 1

    main.MISSIONS.clear()
    main.ROUTE_RESULTS.clear()
    assert client.get(f"/api/missions/{mission_id}").json() == created.json()
    assert client.get(f"/api/missions/{mission_id}/routes").json() == routes.json()
    assert client.get(f"/api/experiments/{body['id']}").json() == body


def test_experiment_validation_and_missing_routes(client):
    assert client.post("/api/missions/missing/experiments", json={
        "weather": [{"name": "calm", "wind_speed_mps": 2, "wind_from_deg": 90}]
    }).status_code == 404
    mission_id = client.post("/api/missions", json=MISSION).json()["id"]
    assert client.post(f"/api/missions/{mission_id}/experiments", json={
        "weather": [{"name": "calm", "wind_speed_mps": 2, "wind_from_deg": 90}]
    }).status_code == 409
    assert client.post(f"/api/missions/{mission_id}/experiments", json={
        "weather": [{"name": "bad", "wind_speed_mps": -1, "wind_from_deg": 90}]
    }).status_code == 422
    assert client.get("/api/experiments/missing").status_code == 404
