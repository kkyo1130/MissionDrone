from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4
import hashlib
import json
import csv
import io
import re
from math import cos, radians
from urllib.request import Request as UrlRequest, urlopen
from urllib.parse import urlencode
from urllib.error import URLError
from datetime import datetime, timezone
from urllib.error import HTTPError

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, StrictInt, TypeAdapter
from starlette.requests import Request

from app.ai.api import router as ai_router
from app.ai.weather_api import router as weather_router
from app.core.llm import get_risk_alpha
from app.core.pathfinding import find_path_astar
from app.core.physics import compute_risk_map
from app.core.routing import generate_route_candidates
from app.core.simulation import simulate_route, compare_runs
from app.store import Store
from app.core.voxel import GridSpec, voxelize_osm_buildings
from app.schemas.missions import (
    DisasterMission,
    InspectionMission,
    MissionModel,
    MissionPayload,
    NoFlyZone,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPOSITORY_ROOT / ".env")

GRID_SPEC = GridSpec()
OCCUPANCY = np.ones((GRID_SPEC.width, GRID_SPEC.depth, GRID_SPEC.height), dtype=np.int8)
GRID_ORIGIN_WGS84 = (126.642, 37.389, 0.0)
OSM_BUILDINGS: list[dict] = []
ENVIRONMENT_KIND = 'osm_unavailable'
PointInput = Tuple[StrictInt, StrictInt, StrictInt]
MISSIONS: Dict[str, Dict[str, Any]] = {}
ROUTE_RESULTS: Dict[str, Dict[str, Any]] = {}
_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


class WeatherScenario(BaseModel):
    name: str = Field(min_length=1)
    wind_speed_mps: float = Field(ge=0, le=60)
    wind_from_deg: float = Field(ge=0, lt=360)


class ExperimentRequest(BaseModel):
    weather: List[WeatherScenario] = Field(min_length=1, max_length=10)
    repeats: int = Field(default=3, ge=1, le=30)
    seed: int = Field(default=42, ge=0)


app = FastAPI(title="MissionDrone API", version="0.1.0")
app.include_router(ai_router)
app.include_router(weather_router)
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


class RouteGenerationRequest(MissionModel):
    count: int = Field(default=5, ge=3, le=5)
    wind_speed: float = Field(default=8.0, ge=0.0, le=60.0)
    wind_direction: float = Field(default=90.0, ge=0.0, le=360.0)
    no_fly_zones: List[NoFlyZone] = Field(default_factory=list)


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


class OsmEnvironmentRequest(BaseModel):
    center_lon: float = Field(default=126.6465, ge=126.60, le=126.70)
    center_lat: float = Field(default=37.3926, ge=37.34, le=37.43)


def _osm_height(tags: dict) -> tuple[float, float, str]:
    def metres(value):
        if value is None:
            return None
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:m)?\s*", str(value))
        return float(match.group(1)) if match else None

    height = metres(tags.get("height")) or metres(tags.get("building:height"))
    levels = metres(tags.get("building:levels"))
    source = "tagged_height" if height is not None else (
        "estimated_from_levels" if levels is not None else "estimated_default"
    )
    if height is None:
        height = levels * 3.2 if levels is not None else 12.0
    minimum = metres(tags.get("min_height")) or 0.0
    return min(300.0, max(2.0, height)), max(0.0, minimum), source


LOCAL_BUILDINGS_PATH = REPOSITORY_ROOT / "backend" / "data" / "songdo_buildings.geojson"


def _local_geojson_buildings(document: dict) -> list[dict]:
    """Read real OSM-derived GeoJSON polygons (no invented footprints)."""
    if document.get("type") != "FeatureCollection" or not isinstance(document.get("features"), list):
        raise ValueError("Expected GeoJSON FeatureCollection with polygon features")
    buildings = []
    for index, feature in enumerate(document["features"]):
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            continue
        if props.get("building") in (None, "no") and props.get("building:part") in (None, "no"):
            continue
        polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                    else geometry["coordinates"])
        height, minimum, source = _osm_height(props)
        for part_index, polygon in enumerate(polygons):
            if not polygon or not isinstance(polygon[0], list):
                continue
            ring = polygon[0]  # Exterior ring; holes require separate treatment.
            try:
                coords = [[float(pt[0]), float(pt[1])] for pt in ring]
            except (TypeError, ValueError, IndexError):
                continue
            if len(coords) >= 4 and coords[0] == coords[-1]:
                coords.pop()
            if len(coords) < 3 or not all(
                126.0 <= lon <= 127.5 and 36.5 <= lat <= 38.5 for lon, lat in coords
            ):
                continue
            buildings.append({
                "id": str(feature.get("id") or props.get("@id") or f"local-{index}") + f"-{part_index}",
                "name": str(props.get("name") or f"OSM 건물 {index + 1}"),
                "coordinates": coords,
                "height_m": height,
                "min_height_m": minimum,
                "height_source": source,
            })
    if not buildings:
        raise ValueError("No valid OSM building polygons in GeoJSON (building tag required)")
    return buildings


def _activate_local_buildings(buildings: list[dict], center_lon: float, center_lat: float) -> dict:
    global OCCUPANCY, GRID_ORIGIN_WGS84, OSM_BUILDINGS, ENVIRONMENT_KIND
    half_x = GRID_SPEC.width * GRID_SPEC.voxel_size_m / 2
    half_y = GRID_SPEC.depth * GRID_SPEC.voxel_size_m / 2
    origin = (
        center_lon - half_x / (111320 * cos(radians(center_lat))),
        center_lat - half_y / 111132,
        0.0,
    )
    grid = voxelize_osm_buildings(GRID_SPEC, origin, buildings)
    if not np.any(grid == 0):
        raise ValueError("No building footprints intersect the 800m study area; adjust center coordinates")
    OCCUPANCY = grid
    GRID_ORIGIN_WGS84 = origin
    OSM_BUILDINGS = buildings
    ENVIRONMENT_KIND = "osm_buildings"
    MISSIONS.clear()
    ROUTE_RESULTS.clear()
    return {
        "environment_kind": ENVIRONMENT_KIND,
        "buildings_count": len(buildings),
        "blocked_voxels_count": int(np.count_nonzero(grid == 0)),
        "origin_wgs84": list(origin),
        "height_estimates": sum(b["height_source"] != "tagged_height" for b in buildings),
        "source": "local_geojson",
    }


class LocalEnvironmentRequest(OsmEnvironmentRequest):
    geojson: dict


@app.post("/api/environment/local")
def import_local_environment(payload: LocalEnvironmentRequest) -> Dict[str, Any]:
    """Import real building GeoJSON once, persist it, and reuse it offline."""
    try:
        buildings = _local_geojson_buildings(payload.geojson)
        # Validate before writing so an invalid file never replaces a working dataset.
        half_x = GRID_SPEC.width * GRID_SPEC.voxel_size_m / 2
        half_y = GRID_SPEC.depth * GRID_SPEC.voxel_size_m / 2
        origin = (
            payload.center_lon - half_x / (111320 * cos(radians(payload.center_lat))),
            payload.center_lat - half_y / 111132, 0.0,
        )
        if not np.any(voxelize_osm_buildings(GRID_SPEC, origin, buildings) == 0):
            raise ValueError("No building footprints intersect the 800m study area")
        LOCAL_BUILDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOCAL_BUILDINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "type": "FeatureCollection",
            "center_lon": payload.center_lon,
            "center_lat": payload.center_lat,
            "features": payload.geojson["features"],
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(LOCAL_BUILDINGS_PATH)
        return _activate_local_buildings(buildings, payload.center_lon, payload.center_lat)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/environment/osm")
def load_osm_environment(payload: OsmEnvironmentRequest) -> Dict[str, Any]:
    """Load real OSM footprints into Cesium and the same routing voxel grid."""
    global OCCUPANCY, GRID_ORIGIN_WGS84, OSM_BUILDINGS, ENVIRONMENT_KIND
    half_x = GRID_SPEC.width * GRID_SPEC.voxel_size_m / 2
    half_y = GRID_SPEC.depth * GRID_SPEC.voxel_size_m / 2
    lon_scale = 111320 * cos(radians(payload.center_lat))
    lat_scale = 111132
    west = payload.center_lon - half_x / lon_scale
    east = payload.center_lon + half_x / lon_scale
    south = payload.center_lat - half_y / lat_scale
    north = payload.center_lat + half_y / lat_scale
    origin = (west, south, 0.0)
    query = (f'[out:json][timeout:30];'
             f'(way["building"]({south:.7f},{west:.7f},{north:.7f},{east:.7f});'
             f'relation["building"]({south:.7f},{west:.7f},{north:.7f},{east:.7f}););out geom;')
    # Retry across independent Overpass instances. A single public endpoint
    # can be overloaded or unavailable, so do not make it a single point of failure.
    endpoints = list(dict.fromkeys([
        os.getenv("OSM_OVERPASS_URL", "https://overpass.kumi.systems/api/interpreter"),
        "https://overpass.nchc.org.tw/api/interpreter",
        "https://overpass-api.de/api/interpreter",
    ]))
    raw = None
    errors = []
    for endpoint in endpoints:
        req = UrlRequest(
            endpoint,
            data=urlencode({"data": query}).encode("utf-8"),
            headers={
                "User-Agent": "MissionDrone-University-Simulation/1.0",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as response:
                candidate = json.load(response)
            if not isinstance(candidate, dict) or not isinstance(candidate.get("elements"), list):
                raise ValueError("Invalid Overpass JSON response")
            raw = candidate
            break
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            errors.append(f"{endpoint}: {exc}")
    if raw is None:
        raise HTTPException(
            status_code=503,
            detail="OSM building service unavailable (all providers failed): " + " | ".join(errors),
        )
    buildings = []
    for item in raw.get("elements", []):
        if item.get("type") != "way":
            # Relation outer rings need stitching; never use incomplete geometry.
            continue
        geometry = item.get("geometry")
        if not geometry or len(geometry) < 4:
            continue
        coords = [[p["lon"], p["lat"]] for p in geometry]
        if coords[0] == coords[-1]:
            coords.pop()
        if len(coords) < 3:
            continue
        tags = item.get("tags") or {}
        height, min_height, source = _osm_height(tags)
        buildings.append({
            "id": f'osm-way-{item["id"]}',
            "name": tags.get("name", f'OSM 건물 {item["id"]}'),
            "coordinates": coords,
            "height_m": height,
            "min_height_m": min_height,
            "height_source": source,
        })
    if not buildings:
        raise HTTPException(status_code=422, detail="No usable OSM building footprints in selected area")
    new_grid = voxelize_osm_buildings(GRID_SPEC, origin, buildings)
    if not np.any(new_grid == 0):
        raise HTTPException(status_code=422, detail="OSM buildings do not intersect the selected voxel grid")
    OCCUPANCY = new_grid
    GRID_ORIGIN_WGS84 = origin
    OSM_BUILDINGS = buildings
    ENVIRONMENT_KIND = "osm_buildings"
    # Cache the exact same OSM geometry used for display and voxelization.
    cache_path = Path(__file__).resolve().parent.parent / "data" / "osm_songdo_cache.json"
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"center_lon": payload.center_lon,
                                   "center_lat": payload.center_lat,
                                   "buildings": buildings}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache_path)
    except OSError:
        pass  # Read-only deployments can still use the in-memory environment.
    MISSIONS.clear()
    ROUTE_RESULTS.clear()
    return {
        "environment_kind": ENVIRONMENT_KIND,
        "buildings_count": len(buildings),
        "blocked_voxels_count": int(np.count_nonzero(new_grid == 0)),
        "origin_wgs84": list(origin),
        "height_estimates": sum(b["height_source"] != "tagged_height" for b in buildings),
        "notice": "OSM can be incomplete; estimated heights are not surveyed. Simulation only.",
    }


@app.on_event("startup")
def initialize_osm_city() -> None:
    """Start quickly using a previously downloaded real OSM dataset, if present.

    Never block server startup on a remote Overpass request and never use
    synthetic buildings as a fallback. Use the import button for first load.
    """
    global OCCUPANCY, GRID_ORIGIN_WGS84, OSM_BUILDINGS, ENVIRONMENT_KIND
    if LOCAL_BUILDINGS_PATH.exists():
        try:
            document = json.loads(LOCAL_BUILDINGS_PATH.read_text(encoding="utf-8"))
            buildings = _local_geojson_buildings(document)
            center_lon = float(document.get("center_lon", 126.6465))
            center_lat = float(document.get("center_lat", 37.3926))
            result = _activate_local_buildings(buildings, center_lon, center_lat)
            print(f"Loaded {result['buildings_count']} real buildings from local GeoJSON.")
            return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"Local building GeoJSON invalid: {exc}")
    cache_path = Path(__file__).resolve().parent.parent / "data" / "osm_songdo_cache.json"
    if not cache_path.exists():
        ENVIRONMENT_KIND = "osm_unavailable"
        print("OSM cache missing: server ready; import real Songdo buildings in the UI.")
        return
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        buildings = data["buildings"]
        center_lon = float(data["center_lon"])
        center_lat = float(data["center_lat"])
        half_x = GRID_SPEC.width * GRID_SPEC.voxel_size_m / 2
        half_y = GRID_SPEC.depth * GRID_SPEC.voxel_size_m / 2
        origin = (center_lon - half_x / (111320 * cos(radians(center_lat))),
                  center_lat - half_y / 111132, 0.0)
        occupancy = voxelize_osm_buildings(GRID_SPEC, origin, buildings)
        if not buildings or not np.any(occupancy == 0):
            raise ValueError("Cached OSM buildings contain no usable obstacles")
        OCCUPANCY = occupancy
        GRID_ORIGIN_WGS84 = origin
        OSM_BUILDINGS = buildings
        ENVIRONMENT_KIND = "osm_buildings"
        print(f"Loaded {len(buildings)} real OSM buildings from local cache.")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ENVIRONMENT_KIND = "osm_unavailable"
        print(f"OSM cache invalid: {exc}. Re-import real buildings in the UI.")


@app.get("/api/environment")
def environment_api() -> Dict[str, Any]:
    blocked_voxels = np.argwhere(OCCUPANCY == 0)
    return {
        "grid_shape": list(OCCUPANCY.shape),
        "voxel_size_m": GRID_SPEC.voxel_size_m,
        "blocked_voxels": blocked_voxels.tolist(),
        "buildings": OSM_BUILDINGS,
        "environment_kind": ENVIRONMENT_KIND,
        "origin_wgs84": list(GRID_ORIGIN_WGS84),
    }


@app.post("/api/missions", status_code=201)
def create_mission_api(payload: MissionPayload) -> Dict[str, Any]:
    _validate_mission(payload)
    mission_id = str(uuid4())
    mission = {"id": mission_id, **payload.model_dump(mode="json")}
    get_store().save_mission(mission)
    MISSIONS[mission_id] = mission
    return mission


@app.get("/api/missions/{mission_id}")
def get_mission_api(mission_id: str) -> Dict[str, Any]:
    mission = MISSIONS.get(mission_id) or get_store().get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@app.post("/api/missions/{mission_id}/routes")
def generate_routes_api(
    mission_id: str, payload: RouteGenerationRequest
) -> Dict[str, Any]:
    mission_record = MISSIONS.get(mission_id) or get_store().get_mission(mission_id)
    if mission_record is None:
        raise HTTPException(status_code=404, detail="Mission not found")

    mission = TypeAdapter(MissionPayload).validate_python(
        {key: value for key, value in mission_record.items() if key != "id"}
    )
    for zone in payload.no_fly_zones:
        if any(
            lower < 0 or upper >= limit
            for lower, upper, limit in zip(zone.minimum, zone.maximum, OCCUPANCY.shape)
        ):
            raise HTTPException(
                status_code=422, detail="no-fly zone is outside the environment grid"
            )

    risk_map = compute_risk_map(
        OCCUPANCY,
        wind_speed=payload.wind_speed,
        wind_direction=payload.wind_direction,
    )
    routes, reason = generate_route_candidates(
        mission,
        OCCUPANCY,
        risk_map,
        voxel_size_m=GRID_SPEC.voxel_size_m,
        origin_wgs84=GRID_ORIGIN_WGS84,
        count=payload.count,
        no_fly_zones=payload.no_fly_zones,
    )
    result = {
        "mission_id": mission_id,
        "requested_count": payload.count,
        "generated_count": len(routes),
        "routes": [{"id": str(uuid4()), **route} for route in routes],
        "reason": reason,
        "wind_speed": payload.wind_speed,
        "wind_direction": payload.wind_direction,
        "no_fly_zones": [
            {"minimum": list(zone.minimum), "maximum": list(zone.maximum)}
            for zone in payload.no_fly_zones
        ],
    }
    get_store().save_routes(mission_id, result)
    ROUTE_RESULTS[mission_id] = result
    return result


@app.get("/api/missions/{mission_id}/routes")
def get_routes_api(mission_id: str) -> Dict[str, Any]:
    if mission_id not in MISSIONS and get_store().get_mission(mission_id) is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return ROUTE_RESULTS.get(mission_id) or get_store().get_routes(mission_id) or {
            "mission_id": mission_id,
            "requested_count": 0,
            "generated_count": 0,
            "routes": [],
            "reason": "Routes have not been generated yet.",
        }


class PointCheckRequest(BaseModel):
    point: Tuple[int, int, int]
    min_altitude_m: float = 0
    max_altitude_m: float = 360
    clearance_m: float = Field(default=3.0, ge=0)
    drone_radius_m: float = Field(default=1.0, ge=0)
    position_error_m: float = Field(default=1.0, ge=0)


@app.post("/api/points/check")
def check_flight_point(payload: PointCheckRequest) -> Dict[str, Any]:
    """Validate a selected endpoint with the same clearance geometry as route generation."""
    from app.core.routing import _transition_is_clear

    point = tuple(payload.point)
    shape = OCCUPANCY.shape
    radius_m = max(0.0, payload.clearance_m + payload.drone_radius_m + payload.position_error_m)
    radius = radius_m / GRID_SPEC.voxel_size_m

    def reason_for(candidate):
        if any(v < 0 or v >= limit for v, limit in zip(candidate, shape)):
            return "연구 영역 밖입니다."
        altitude = (candidate[2] + 0.5) * GRID_SPEC.voxel_size_m
        if not payload.min_altitude_m <= altitude <= payload.max_altitude_m:
            return "임무 비행 고도 범위를 벗어났습니다."
        if OCCUPANCY[candidate] == 0:
            return "건물 내부 또는 장애물 복셀입니다."
        margin = int(np.ceil(radius)) + 2
        slices = tuple(slice(max(0, v-margin), min(limit, v+margin+1))
                       for v, limit in zip(candidate, shape))
        local = tuple(v - sl.start for v, sl in zip(candidate, slices))
        if not _transition_is_clear(OCCUPANCY[slices], local, radius):
            return "건물과의 안전거리가 부족합니다."
        return None

    reason = reason_for(point)
    suggestion = None
    if reason is not None:
        # Prefer nearby horizontal positions at the requested flight altitude.
        # Search vertical alternatives only if no horizontal alternative exists.
        for vertical in (False, True):
            candidates = []
            for dx in range(-8, 9):
                for dy in range(-8, 9):
                    for dz in (range(-5, 6) if vertical else (0,)):
                        candidate = (point[0]+dx, point[1]+dy, point[2]+dz)
                        if not all(0 <= v < limit for v, limit in zip(candidate, shape)):
                            continue
                        candidates.append((dx*dx+dy*dy+dz*dz, candidate))
            for _, candidate in sorted(candidates, key=lambda item: item[0]):
                if reason_for(candidate) is None:
                    suggestion = list(candidate)
                    break
            if suggestion is not None:
                break
    return {"valid": reason is None, "reason": reason, "point": list(point),
            "suggested_point": suggestion}


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


@app.post("/api/missions/{mission_id}/experiments", status_code=201)
def create_experiment_api(mission_id: str, payload: ExperimentRequest) -> Dict[str, Any]:
    record = MISSIONS.get(mission_id) or get_store().get_mission(mission_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    routes = ROUTE_RESULTS.get(mission_id) or get_store().get_routes(mission_id)
    if not routes or not routes["routes"]:
        raise HTTPException(status_code=409, detail="Generate routes before running experiments")
    mission = TypeAdapter(MissionPayload).validate_python({key: value for key, value in record.items() if key != "id"})
    weather = [scenario.model_dump() for scenario in payload.weather]
    runs = []
    for route in routes["routes"]:
        for weather_index, scenario in enumerate(weather):
            for repeat in range(payload.repeats):
                seed = payload.seed + weather_index * 100000 + repeat
                runs.append({
                    "route_id": route["id"],
                    "weather_index": weather_index,
                    "repeat": repeat,
                    "seed": seed,
                    "metrics": simulate_route(route, mission, scenario, seed),
                })
    evaluation = compare_runs(runs, mission)
    snapshot = {"mission": record, "routes": routes, "weather": weather, "repeats": payload.repeats, "seed": payload.seed}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = {
        "id": str(uuid4()), "mission_id": mission_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_hash_sha256": digest, "snapshot": snapshot,
        "runs": runs, "evaluation": evaluation, "synthetic": True,
    }
    get_store().save_experiment(result)
    return result


@app.get("/api/experiments/{experiment_id}")
def get_experiment_api(experiment_id: str) -> Dict[str, Any]:
    result = get_store().get_experiment(experiment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return result


@app.get("/api/missions/{mission_id}/experiments")
def list_experiments_api(mission_id: str) -> List[Dict[str, Any]]:
    if (MISSIONS.get(mission_id) or get_store().get_mission(mission_id)) is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return get_store().list_experiments(mission_id)


@app.get("/api/experiments/{experiment_id}/replay")
def replay_experiment_api(experiment_id: str) -> Dict[str, Any]:
    result = get_experiment_api(experiment_id)
    snapshot = result["snapshot"]
    mission = TypeAdapter(MissionPayload).validate_python({key: value for key, value in snapshot["mission"].items() if key != "id"})
    route_by_id = {route["id"]: route for route in snapshot["routes"]["routes"]}
    replayed = [
        {**{key: run[key] for key in ("route_id", "weather_index", "repeat", "seed")},
         "metrics": simulate_route(route_by_id[run["route_id"]], mission, snapshot["weather"][run["weather_index"]], run["seed"])}
        for run in result["runs"]
    ]
    return {"experiment_id": experiment_id, "matches": replayed == result["runs"], "evaluation": compare_runs(replayed, mission)}


@app.get("/api/experiments/{experiment_id}/export")
def export_experiment_api(experiment_id: str, format: str = Query(default="json", pattern="^(json|csv)$")) -> Response:
    result = get_experiment_api(experiment_id)
    if format == "json":
        return Response(content=json.dumps(result, ensure_ascii=False), media_type="application/json")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["route_id", "weather_index", "repeat", "seed", "safe", "time_s", "energy_wh", "failure_reasons"])
    for run in result["runs"]:
        metrics = run["metrics"]
        writer.writerow([run["route_id"], run["weather_index"], run["repeat"], run["seed"], metrics["safe"], metrics["time_s"], metrics["energy_wh"], ";".join(metrics["failure_reasons"])])
    return Response(content=output.getvalue(), media_type="text/csv")
