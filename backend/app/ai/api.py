from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai.wind_correction import predict_wind

router = APIRouter(prefix="/api/ai", tags=["AI wind correction"])
MODEL_PATH = Path(__file__).resolve().parents[3] / "models" / "wind_corrector.pkl"


class WindPredictionRequest(BaseModel):
    wind_speed_mps: float = Field(ge=0, le=60)
    wind_direction_deg: float = Field(ge=0, lt=360)
    timestamp_utc: datetime


@router.get("/status")
def status() -> dict:
    return {
        "trained_model_available": MODEL_PATH.is_file(),
        "model_scope": "station-level wind bias correction; not urban-canyon risk validation",
    }


@router.post("/correct-wind")
def correct_wind(payload: WindPredictionRequest) -> dict:
    if not MODEL_PATH.is_file():
        raise HTTPException(status_code=503, detail="No trained model. Run the NOAA/ERA5 training CLI first.")
    # Only load local, trusted model artifacts created by this project; pickle is unsafe for untrusted files.
    with MODEL_PATH.open("rb") as stream:
        artifact = pickle.load(stream)
    timestamp = payload.timestamp_utc.isoformat()
    corrected = predict_wind(artifact, payload.wind_speed_mps, payload.wind_direction_deg, timestamp)
    return {
        "original_wind_speed_mps": payload.wind_speed_mps,
        "corrected_wind_speed_mps": corrected,
        "station": artifact["station"],
        "metrics": artifact["metrics"],
        "warning": "Station-level estimate only; not a measured voxel wind or certified flight-safe value.",
    }
