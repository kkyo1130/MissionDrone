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


def test_osm_building_footprints_match_voxel_grid(monkeypatch):
    """Use a deterministic OSM fixture; no live Overpass dependency in tests."""
    from app import main as module
    from app.core.voxel import voxelize_osm_buildings

    origin = (126.645, 37.385, 0.0)
    dx = 10 / (111320 * np.cos(np.radians(origin[1])))
    dy = 10 / 111132
    building = {
        "coordinates": [[origin[0], origin[1]],
                        [origin[0] + dx, origin[1]],
                        [origin[0] + dx, origin[1] + dy],
                        [origin[0], origin[1] + dy]],
        "height_m": 25.0, "min_height_m": 0.0,
    }
    grid = voxelize_osm_buildings(module.GRID_SPEC, origin, [building])
    assert grid[0, 0, 0] == 0
    assert grid[0, 0, 2] == 0
    assert grid[0, 0, 3] == 1
    assert grid[3, 3, 0] == 1


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


def test_route_rejects_invalid_or_blocked_points(monkeypatch):
    out_of_bounds = client.post(
        "/api/route", json={"start": [32, 0, 0], "end": [28, 28, 6]}
    )
    assert out_of_bounds.status_code == 422

    # Deterministic fixture: an explicitly occupied voxel, independent of
    # the OSM network response or the selected Songdo study area.
    from app import main as module
    occupied_grid = module.OCCUPANCY.copy()
    occupied_grid[6, 6, 0] = 0
    monkeypatch.setattr(module, "OCCUPANCY", occupied_grid)
    blocked = client.post(
        "/api/route", json={"start": [6, 6, 0], "end": [28, 28, 6]}
    )
    assert blocked.status_code == 422


def test_invalid_weather_parameters_are_rejected():
    response = client.get("/api/risk-map", params={"wind_speed": -1})
    assert response.status_code == 422


def test_osm_footprint_voxelization_matches_height_and_polygon():
    from app.core.voxel import GridSpec, voxelize_osm_buildings

    # A 10 x 10 m building at the local origin, 25 m tall.
    origin = (126.645, 37.385, 0.0)
    lon_step = 10 / (111320 * np.cos(np.radians(origin[1])))
    lat_step = 10 / 111132
    building = {
        "coordinates": [
            [origin[0], origin[1]],
            [origin[0] + lon_step, origin[1]],
            [origin[0] + lon_step, origin[1] + lat_step],
            [origin[0], origin[1] + lat_step],
        ],
        "height_m": 25.0,
        "min_height_m": 0.0,
    }
    grid = voxelize_osm_buildings(GridSpec(4, 4, 5, 10), origin, [building])
    assert grid[0, 0, 0] == 0
    assert grid[0, 0, 2] == 0
    assert grid[0, 0, 3] == 1
    assert grid[3, 3, 0] == 1


def test_osm_height_provenance():
    from app.main import _osm_height

    assert _osm_height({"height": "42 m"}) == (42.0, 0.0, "tagged_height")
    assert _osm_height({"building:levels": "5"}) == (16.0, 0.0, "estimated_from_levels")
    assert _osm_height({}) == (12.0, 0.0, "estimated_default")
