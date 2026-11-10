"""Historical wind scenario source: NOAA observed station CSV or ERA5 reanalysis.

No silent fallback from observations to reanalysis. UTC hour and provenance
are returned to the UI for reproducibility.
"""
from __future__ import annotations

import csv
import os
import pickle
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.ai.wind_correction import fetch_era5, predict_wind
from app.ai.api import MODEL_PATH

router = APIRouter(prefix="/api/weather", tags=["historical weather"])
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=4)
def _station_rows(path: str) -> dict[str, tuple[float, float]]:
    result = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            try:
                parts = row["WND"].split(",")
                direction, direction_qc, _, speed, speed_qc = parts
                if speed == "9999" or direction == "999":
                    continue
                if speed_qc not in ("1", "5") or direction_qc not in ("1", "5"):
                    continue
                dt = datetime.fromisoformat(row["DATE"].replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                key = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
                result[key] = (int(speed) / 10.0, float(direction))
            except (KeyError, ValueError, AttributeError):
                continue
    return result


@router.get("/historical")
def historical_weather(
    at: datetime = Query(..., description="Historical UTC timestamp, e.g. 2024-07-01T12:00:00Z"),
    source: str = Query("noaa", pattern="^(noaa|era5)$"),
) -> dict:
    if at.tzinfo is None:
        raise HTTPException(422, "Timestamp must include timezone")
    at = at.astimezone(timezone.utc)
    if at > datetime.now(timezone.utc):
        raise HTTPException(422, "Historical weather cannot be in the future")
    key = at.strftime("%Y-%m-%dT%H:00")
    if source == "noaa":
        path = os.getenv("NOAA_STATION_CSV")
        if not path:
            raise HTTPException(503, "Set NOAA_STATION_CSV to a downloaded NOAA Global Hourly CSV")
        csv_path = Path(path).expanduser()
        if not csv_path.is_absolute():
            csv_path = REPOSITORY_ROOT / csv_path
        path = str(csv_path)
        if not Path(path).is_file():
            raise HTTPException(503, "Configured NOAA_STATION_CSV file does not exist")
        try:
            wind = _station_rows(path).get(key)
        except (OSError, UnicodeError):
            raise HTTPException(503, "Could not read configured NOAA station CSV")
        if wind is None:
            raise HTTPException(404, "No quality-checked NOAA observation for this UTC hour")
        speed, direction = wind
        if not 0 <= speed <= 60 or not 0 <= direction < 360:
            raise HTTPException(422, "Station observation outside supported simulation range")
        return {
            "source": "NOAA Global Hourly", "data_kind": "station_observation",
            "station": os.getenv("NOAA_STATION_ID", "configured_station"),
            "timestamp_utc": key + ":00Z", "wind_speed_mps": speed,
            "wind_from_deg": direction,
            "limitation": "Station wind is not a measured 3D urban-canyon voxel wind.",
        }
    try:
        latitude = float(os.getenv("WEATHER_LATITUDE", "37.37"))
        longitude = float(os.getenv("WEATHER_LONGITUDE", "126.62"))
        data = fetch_era5(latitude, longitude, at.date().isoformat(), at.date().isoformat())
        hours = data["hourly"]
        i = hours["time"].index(key)
        speed, direction = hours["wind_speed_10m"][i], hours["wind_direction_10m"][i]
        if speed is None or direction is None:
            raise ValueError("missing ERA5 wind data")
        if not 0 <= float(speed) <= 60:
            raise ValueError("wind outside supported range")
    except (ValueError, KeyError, IndexError, OSError, TimeoutError) as exc:
        raise HTTPException(502, f"ERA5 data unavailable: {exc}")
    result = {
        "source": "Open-Meteo ERA5", "data_kind": "reanalysis",
        "timestamp_utc": key + ":00Z", "wind_speed_mps": float(speed),
        "wind_from_deg": float(direction),
        "original_wind_speed_mps": float(speed),
        "ai_corrected": False,
        "limitation": "ERA5 reanalysis is not a direct station observation or voxel-scale wind.",
    }
    if MODEL_PATH.is_file():
        try:
            with MODEL_PATH.open("rb") as stream:
                artifact = pickle.load(stream)  # Local trusted training output only.
            corrected = predict_wind(artifact, float(speed), float(direction), key)
            if not 0 <= corrected <= 60:
                raise ValueError("Corrected speed outside supported range")
            result["wind_speed_mps"] = corrected
            result["ai_corrected"] = True
            result["correction_station"] = artifact["station"]
            result["correction_metrics"] = artifact["metrics"]
            result["limitation"] = "AI-corrected station-level estimate; not measured urban-canyon voxel wind."
        except (OSError, ValueError, KeyError, TypeError, pickle.UnpicklingError) as exc:
            raise HTTPException(503, f"Trained wind model could not be applied: {exc}")
    return result
