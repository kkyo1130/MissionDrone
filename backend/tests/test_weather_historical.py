from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.ai import weather_api


def test_historical_noaa_uses_station_observation(monkeypatch, tmp_path):
    path = tmp_path / "station.csv"
    path.write_text('DATE,WND\n2024-07-01T12:00:00,"090,1,N,0050,1"\n', encoding="utf-8")
    monkeypatch.setenv("NOAA_STATION_CSV", str(path))
    monkeypatch.setenv("NOAA_STATION_ID", "test-station")
    weather_api._station_rows.cache_clear()
    response = TestClient(app).get("/api/weather/historical", params={
        "at": "2024-07-01T12:00:00Z", "source": "noaa"
    })
    assert response.status_code == 200
    assert response.json()["wind_speed_mps"] == 5
    assert response.json()["wind_from_deg"] == 90
    assert response.json()["data_kind"] == "station_observation"


def test_noaa_not_configured_does_not_silently_fallback(monkeypatch):
    monkeypatch.delenv("NOAA_STATION_CSV", raising=False)
    response = TestClient(app).get("/api/weather/historical", params={
        "at": "2024-07-01T12:00:00Z", "source": "noaa"
    })
    assert response.status_code == 503


def test_future_historical_rejected():
    response = TestClient(app).get("/api/weather/historical", params={
        "at": "2099-01-01T00:00:00Z", "source": "era5"
    })
    assert response.status_code == 422
