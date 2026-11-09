"""Train an observed-station wind-speed bias corrector against ERA5.

NOAA Global Hourly CSV requires DATE and WND columns. WND format:
direction,quality,type,speed_tenths_mps,quality.
This predicts station-level wind, NOT building-scale voxel wind.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np

FEATURES = ("era5_speed", "era5_direction_sin", "era5_direction_cos", "hour_sin", "hour_cos")


def parse_noaa_csv(text: str) -> dict[str, float]:
    """UTC timestamp -> observed wind speed (m/s); reject missing/invalid values."""
    result = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            direction, direction_qc, _, speed, speed_qc = row["WND"].split(",")
            if speed in ("9999", "") or speed_qc not in ("1", "5"):
                continue
            if direction != "999" and direction_qc not in ("1", "5"):
                continue
            speed_ms = int(speed) / 10.0
            if not 0 <= speed_ms <= 60:
                continue
            dt = datetime.fromisoformat(row["DATE"].replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            result[dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")] = speed_ms
        except (ValueError, KeyError, AttributeError):
            continue
    return result


def fetch_text(url: str) -> str:
    with urlopen(url, timeout=45) as response:
        return response.read().decode("utf-8-sig")


def fetch_era5(lat: float, lon: float, start: str, end: str) -> dict:
    params = urlencode({
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms", "timezone": "GMT", "models": "era5",
    })
    return json.loads(fetch_text("https://archive-api.open-meteo.com/v1/archive?" + params))


def feature_vector(speed: float, direction: float, timestamp: str) -> list[float]:
    theta = np.deg2rad(direction)
    hour = datetime.fromisoformat(timestamp).hour
    return [float(speed), float(np.sin(theta)), float(np.cos(theta)),
            float(np.sin(2 * np.pi * hour / 24)), float(np.cos(2 * np.pi * hour / 24))]


def join_observations(observed: dict[str, float], era5: dict) -> tuple[np.ndarray, np.ndarray, list[str]]:
    hourly = era5["hourly"]
    rows = []
    for timestamp, speed, direction in zip(
        hourly["time"], hourly["wind_speed_10m"], hourly["wind_direction_10m"]
    ):
        key = timestamp[:13] + ":00"
        if key in observed and speed is not None and direction is not None:
            rows.append((key, feature_vector(float(speed), float(direction), key), observed[key]))
    rows.sort(key=lambda row: row[0])
    if not rows:
        raise ValueError("No overlapping valid hourly station observations and ERA5 values")
    return (np.asarray([r[1] for r in rows], dtype=float),
            np.asarray([r[2] for r in rows], dtype=float),
            [r[0] for r in rows])


def train(X: np.ndarray, y: np.ndarray, timestamps: list[str], station: str) -> tuple[dict, dict]:
    """Chronological holdout avoids randomly mixing nearby hours across train/test."""
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error

    if len(y) < 100:
        raise ValueError("At least 100 paired hourly records are required")
    split = int(len(y) * 0.8)
    while split < len(y) and timestamps[split - 1][:10] == timestamps[split][:10]:
        split += 1
    if split >= len(y) or split < 50:
        raise ValueError("Need observations spanning multiple days for a chronological holdout")
    model = RandomForestRegressor(n_estimators=120, min_samples_leaf=5, random_state=42, n_jobs=-1)
    model.fit(X[:split], y[:split])
    baseline = X[split:, 0]
    corrected = np.maximum(model.predict(X[split:]), 0)
    metrics = {
        "station": station, "samples": len(y), "train_samples": split,
        "test_samples": len(y) - split, "test_start_utc": timestamps[split],
        "baseline_mae_mps": float(mean_absolute_error(y[split:], baseline)),
        "corrected_mae_mps": float(mean_absolute_error(y[split:], corrected)),
        "baseline_rmse_mps": float(np.sqrt(mean_squared_error(y[split:], baseline))),
        "corrected_rmse_mps": float(np.sqrt(mean_squared_error(y[split:], corrected))),
        "data_kind": "station_observations_vs_ERA5_reanalysis",
        "scope": "station-level wind bias correction; not validated for urban-canyon voxels",
    }
    return {"model": model, "features": FEATURES, "station": station, "metrics": metrics}, metrics


def predict_wind(artifact: dict, speed: float, direction: float, timestamp: str) -> float:
    x = np.asarray([feature_vector(speed, direction, timestamp)], dtype=float)
    return float(max(0.0, artifact["model"].predict(x)[0]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--noaa-csv", required=True, help="Public NOAA Global Hourly CSV URL or downloaded CSV path")
    parser.add_argument("--station", required=True, help="NOAA station ID for provenance")
    parser.add_argument("--latitude", required=True, type=float)
    parser.add_argument("--longitude", required=True, type=float)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", default="models/wind_corrector.pkl")
    args = parser.parse_args()
    raw = fetch_text(args.noaa_csv) if args.noaa_csv.startswith(("https://", "http://")) else Path(args.noaa_csv).read_text(encoding="utf-8-sig")
    observed = parse_noaa_csv(raw)
    era5 = fetch_era5(args.latitude, args.longitude, args.start, args.end)
    X, y, timestamps = join_observations(observed, era5)
    artifact, metrics = train(X, y, timestamps, args.station)
    artifact["provenance"] = {
        "observation_source": args.noaa_csv, "reanalysis_source": "Open-Meteo ERA5",
        "latitude": args.latitude, "longitude": args.longitude,
        "start": args.start, "end": args.end,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as file:
        pickle.dump(artifact, file)
    path.with_suffix(".metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
