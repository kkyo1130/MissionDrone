from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, StrictInt
from starlette.requests import Request

from app.core.llm import get_risk_alpha
from app.core.pathfinding import find_path_astar
from app.core.physics import compute_risk_map
from app.core.voxel import GridSpec, create_airspace_grid

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPOSITORY_ROOT / ".env")

GRID_SPEC = GridSpec()
OCCUPANCY = create_airspace_grid(GRID_SPEC)
PointInput = Tuple[StrictInt, StrictInt, StrictInt]

app = FastAPI(title="MissionDrone API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=REPOSITORY_ROOT / "frontend" / "templates")
app.mount(
    "/static",
    StaticFiles(directory=REPOSITORY_ROOT / "frontend" / "static"),
    name="static",
)


class RouteRequest(BaseModel):
    start: PointInput
    end: PointInput
    wind_speed: float = Field(default=8.0, ge=0.0, le=60.0)
    wind_direction: float = Field(default=90.0, ge=0.0, le=360.0)
    vehicle_type: str = "default"


def _validate_point(point: PointInput, key: str) -> Tuple[int, int, int]:
    result = tuple(point)
    if any(value < 0 or value >= limit for value, limit in zip(result, OCCUPANCY.shape)):
        raise HTTPException(status_code=422, detail=f"'{key}' out of grid bounds")
    return result


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "grid_shape": OCCUPANCY.shape,
            "voxel_size_m": GRID_SPEC.voxel_size_m,
            "cesium_ion_token": os.getenv("CESIUM_ION_TOKEN", ""),
        },
    )


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/risk-map")
def risk_map_api(
    wind_speed: float = Query(default=8.0, ge=0.0, le=60.0),
    wind_direction: float = Query(default=90.0, ge=0.0, le=360.0),
    threshold: float = Query(default=0.55, ge=0.0, le=1.0),
) -> Dict[str, Any]:
    risk_map = compute_risk_map(
        OCCUPANCY, wind_speed=wind_speed, wind_direction=wind_direction
    )
    indices = np.argwhere(risk_map >= threshold)
    points: List[Dict[str, Any]] = [
        {"x": x, "y": y, "z": z, "risk": float(risk_map[x, y, z])}
        for x, y, z in indices.tolist()
    ]
    return {
        "grid_shape": list(OCCUPANCY.shape),
        "voxel_size_m": GRID_SPEC.voxel_size_m,
        "wind_speed": wind_speed,
        "wind_direction": wind_direction,
        "threshold": threshold,
        "points": points,
    }


@app.post("/api/route")
def route_api(payload: RouteRequest) -> Dict[str, Any]:
    start = _validate_point(payload.start, "start")
    end = _validate_point(payload.end, "end")
    if OCCUPANCY[start] == 0 or OCCUPANCY[end] == 0:
        raise HTTPException(status_code=422, detail="start and end must be free voxels")

    risk_map = compute_risk_map(
        OCCUPANCY,
        wind_speed=payload.wind_speed,
        wind_direction=payload.wind_direction,
    )
    alpha = get_risk_alpha(
        wind_speed=payload.wind_speed,
        wind_direction=payload.wind_direction,
        vehicle_type=payload.vehicle_type,
    )
    path = find_path_astar(OCCUPANCY, risk_map, start, end, alpha)
    if path is None:
        raise HTTPException(status_code=404, detail="No valid path found")

    risk_values = [float(risk_map[x, y, z]) for x, y, z in path]
    return {
        "start": list(start),
        "end": list(end),
        "wind_speed": payload.wind_speed,
        "wind_direction": payload.wind_direction,
        "vehicle_type": payload.vehicle_type,
        "alpha": alpha,
        "path": [list(point) for point in path],
        "path_length": len(path),
        "average_risk": float(np.mean(risk_values)) if risk_values else 0.0,
    }
