import numpy as np
from fastapi.testclient import TestClient

from app.main import OCCUPANCY, app

client = TestClient(app)


def test_health_and_dashboard():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "APP_GRID_SPEC" in dashboard.text
    assert "/static/js/map.js" in dashboard.text
    assert "originWgs84" in dashboard.text


def test_dashboard_serves_frontend_script():
    response = client.get("/static/js/map.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "loadRiskMap" in response.text
    assert "findRoute" in response.text


def test_environment_returns_blocked_voxel_map():
    response = client.get("/api/environment")

    assert response.status_code == 200
    result = response.json()
    assert result["grid_shape"] == list(OCCUPANCY.shape)
    assert result["voxel_size_m"] == 10
    assert result["blocked_voxels"] == np.argwhere(OCCUPANCY == 0).tolist()


def test_risk_map_returns_voxel_units_and_free_points():
    response = client.get("/api/risk-map", params={"threshold": 0.9})
    assert response.status_code == 200
    result = response.json()
    assert result["grid_shape"] == [32, 32, 12]
    assert result["voxel_size_m"] == 10
    assert all(0.9 <= point["risk"] <= 1.0 for point in result["points"])
    assert all(
        OCCUPANCY[point["x"], point["y"], point["z"]] == 1
        for point in result["points"]
    )


def test_route_returns_valid_path():
    response = client.post(
        "/api/route",
        json={"start": [2, 2, 6], "end": [28, 28, 6], "vehicle_type": "passenger"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["path"][0] == result["start"]
    assert result["path"][-1] == result["end"]
    assert result["path_length"] == len(result["path"])
    assert 0.0 <= result["average_risk"] <= 1.0
    assert all(OCCUPANCY[tuple(point)] == 1 for point in result["path"])


def test_route_rejects_invalid_or_blocked_points():
    out_of_bounds = client.post(
        "/api/route", json={"start": [32, 0, 0], "end": [28, 28, 6]}
    )
    assert out_of_bounds.status_code == 422

    blocked = client.post(
        "/api/route", json={"start": [6, 6, 0], "end": [28, 28, 6]}
    )
    assert blocked.status_code == 422


def test_invalid_weather_parameters_are_rejected():
    response = client.get("/api/risk-map", params={"wind_speed": -1})
    assert response.status_code == 422
