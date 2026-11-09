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
