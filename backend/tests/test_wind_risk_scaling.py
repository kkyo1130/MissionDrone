import numpy as np

from app.core.physics import compute_risk_map


def test_risk_increases_with_wind_speed():
    occupancy = np.ones((7, 7, 4), dtype=np.int8)
    occupancy[3, 3, :2] = 0
    calm = compute_risk_map(occupancy, 2.0, 90.0)
    windy = compute_risk_map(occupancy, 15.0, 90.0)
    assert np.max(windy) > np.max(calm)
    assert np.all(windy >= calm)
    assert np.max(compute_risk_map(occupancy, 0.0, 90.0)) == 0.0


def test_building_risk_is_local_and_avoids_solid_cells():
    occupancy = np.ones((25, 25, 8), dtype=np.int8)
    occupancy[11:14, 11:14, :5] = 0
    risk = compute_risk_map(occupancy, 8.0, 270.0)
    assert np.max(risk) > 0
    assert np.all(risk[occupancy == 0] == 0)
    assert np.max(risk[:4, :4]) == 0
    assert np.max(compute_risk_map(np.ones_like(occupancy), 8.0, 270.0)) == 0


def test_wind_direction_changes_building_wake():
    occupancy = np.ones((19, 19, 6), dtype=np.int8)
    occupancy[8:11, 8:11, :4] = 0
    west_wind = compute_risk_map(occupancy, 8.0, 270.0)
    east_wind = compute_risk_map(occupancy, 8.0, 90.0)
    assert not np.allclose(west_wind, east_wind)
