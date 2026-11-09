from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

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
from app.schemas.missions import (
    DisasterMission,
    InspectionMission,
    MissionPayload,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPOSITORY_ROOT / ".env")

GRID_SPEC = GridSpec()
OCCUPANCY = create_airspace_grid(GRID_SPEC)
GRID_ORIGIN_WGS84 = (126.62, 37.37, 0.0)
PointInput = Tuple[StrictInt, StrictInt, StrictInt]
MISSIONS: Dict[str, Dict[str, Any]] = {}

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


def _validate_mission(payload: MissionPayload) -> None:
    flight_points = [("start", payload.start), ("end", payload.end)]
    flight_points.extend(
        (f"waypoints[{index}]", point)
        for index, point in enumerate(payload.waypoints)
    )
    for key, point in flight_points:
        validated_point = _validate_point(point, key)
        if OCCUPANCY[validated_point] == 0:
            raise HTTPException(
                status_code=422,
                detail=f"'{key}' must be a free voxel",
            )

    max_grid_altitude_m = GRID_SPEC.height * GRID_SPEC.voxel_size_m
    constraints = payload.constraints
    if constraints.max_altitude_m > max_grid_altitude_m:
        raise HTTPException(
            status_code=422,
            detail=f"max_altitude_m cannot exceed {max_grid_altitude_m}",
        )
    for key, point in flight_points:
        voxel_altitude_m = (point[2] + 0.5) * GRID_SPEC.voxel_size_m
        if not constraints.min_altitude_m <= voxel_altitude_m <= constraints.max_altitude_m:
            raise HTTPException(
                status_code=422,
                detail=f"'{key}' altitude is outside the mission altitude range",
            )

    mission_points = []
    if isinstance(payload, InspectionMission):
        mission_points.extend(
            (f"target_points[{index}]", point)
            for index, point in enumerate(payload.target_points)
        )
    elif isinstance(payload, DisasterMission):
        mission_points.extend(
            (f"search_area[{index}]", point)
            for index, point in enumerate(payload.search_area)
        )
        mission_points.extend(
            (f"priority_points[{index}]", point)
            for index, point in enumerate(payload.priority_points)
        )
    for key, point in mission_points:
        _validate_point(point, key)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "grid_shape": OCCUPANCY.shape,
            "voxel_size_m": GRID_SPEC.voxel_size_m,
            "origin_wgs84": GRID_ORIGIN_WGS84,
            "cesium_ion_token": os.getenv("CESIUM_ION_TOKEN", ""),
        },
    )


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/environment")
def environment_api() -> Dict[str, Any]:
    blocked_voxels = np.argwhere(OCCUPANCY == 0)
    return {
        "grid_shape": list(OCCUPANCY.shape),
        "voxel_size_m": GRID_SPEC.voxel_size_m,
        "blocked_voxels": blocked_voxels.tolist(),
    }


@app.post("/api/missions", status_code=201)
def create_mission_api(payload: MissionPayload) -> Dict[str, Any]:
    _validate_mission(payload)
    mission_id = str(uuid4())
    mission = {"id": mission_id, **payload.model_dump(mode="json")}
    MISSIONS[mission_id] = mission
    return mission


@app.get("/api/missions/{mission_id}")
def get_mission_api(mission_id: str) -> Dict[str, Any]:
    mission = MISSIONS.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


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
