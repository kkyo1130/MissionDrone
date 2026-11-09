from app.ai.wind_correction import feature_vector, join_observations, parse_noaa_csv, train
import numpy as np
import pytest


def test_noaa_wind_quality_and_missing():
    text = ("DATE,WND\n"
            "2025-01-01T00:10:00,090,1,N,0030,1\n")
    # Quoted CSV field is required because NOAA WND contains commas.
    text = 'DATE,WND\n2025-01-01T00:10:00,"090,1,N,0030,1"\n2025-01-01T01:10:00,"999,9,N,9999,9"\n'
    assert parse_noaa_csv(text) == {"2025-01-01T00:00": 3.0}


def test_join_hourly_era5():
    observed = {"2025-01-01T00:00": 3.0}
    era5 = {"hourly": {"time": ["2025-01-01T00:00"], "wind_speed_10m": [4.0], "wind_direction_10m": [90]}}
    x, y, ts = join_observations(observed, era5)
    assert x.shape == (1, 5)
    assert y.tolist() == [3.0]
    assert ts == ["2025-01-01T00:00"]


def test_train_rejects_tiny_dataset():
    with pytest.raises(ValueError, match="100"):
        train(np.zeros((10, 5)), np.ones(10), ["2025-01-01T00:00"] * 10, "test")
