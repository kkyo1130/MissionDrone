import math
from typing import Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Point(StrictModel):
    x: float
    y: float
    z: float

class Box(StrictModel):
    minimum: Point
    maximum: Point

    @model_validator(mode="after")
    def ordered(self):
        if any(getattr(self.minimum, a) >= getattr(self.maximum, a) for a in "xyz"):
            raise ValueError("box minimum must be below maximum")
        return self

class RiskBox(Box):
    risk: float = Field(ge=0, le=1)
    uncertainty: float = Field(default=0, ge=0, le=1)

class Environment(StrictModel):
    name: str = Field(min_length=1)
    origin_lat: float = Field(default=37.5665, ge=-85, le=85)
    origin_lon: float = Field(default=126.978, ge=-180, le=180)
    origin_alt: float = 0
    size: Point
    resolution: float = Field(default=10, ge=1)
    obstacles: list[Box] = Field(default_factory=list)
    no_fly_zones: list[Box] = Field(default_factory=list)
    risk_fields: list[RiskBox] = Field(default_factory=list)
    source: str = "synthetic"
    model_version: str = "conservative-box-v1"

    @model_validator(mode="after")
    def bounded(self):
        if any(getattr(self.size, a) <= 0 for a in "xyz"):
            raise ValueError("size must be positive")
        if math.prod(math.ceil(getattr(self.size, a) / self.resolution) + 1 for a in "xyz") > 150000:
            raise ValueError("environment exceeds 150000 voxel limit")
        return self

class Mission(StrictModel):
    environment_id: str
    kind: Literal["inspection", "disaster", "delivery"]
    start: Point
    goal: Point
    waypoints: list[Point] = Field(default_factory=list, max_length=20)
    targets: list[Point] = Field(default_factory=list, max_length=30)
    clearance_m: float = Field(default=3, ge=0)
    drone_radius_m: float = Field(default=1, gt=0)
    position_error_m: float = Field(default=1, ge=0)
    min_altitude_m: float = Field(default=5, ge=0)
    max_altitude_m: float = Field(default=100, gt=0)
    airspeed_mps: float = Field(default=10, gt=0, le=30)
    max_climb_mps: float = Field(default=5, gt=0, le=15)
    max_turn_deg: float = Field(default=135, gt=0, le=180)
    battery_wh: float = Field(default=150, gt=0)
    time_limit_s: float = Field(default=1800, gt=0)
    payload_kg: float = Field(default=0, ge=0, le=20)
    observation_radius_m: float = Field(default=20, gt=0)

    @model_validator(mode="after")
    def constraints(self):
        if self.min_altitude_m > self.max_altitude_m:
            raise ValueError("altitude bounds reversed")
        if self.kind != "delivery" and not self.targets:
            raise ValueError("inspection/disaster require targets (mandatory visit points)")
        return self

class RouteRequest(StrictModel):
    count: int = Field(default=3, ge=3, le=5)

class Weather(StrictModel):
    name: str = Field(min_length=1)
    wind_speed_mps: float = Field(ge=0, le=60)
    wind_from_deg: float = Field(ge=0, lt=360)
    timestamp: str
    source: str = Field(min_length=1)
    historical: bool = False

    @field_validator("timestamp")
    @classmethod
    def valid_timestamp(cls, value):
        parsed=datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None: raise ValueError("timestamp requires timezone")
        return parsed.isoformat()

class SimulationRequest(StrictModel):
    mission_id: str
    weather_ids: list[str] = Field(min_length=1, max_length=10)
    repeats: int = Field(default=3, ge=1, le=30)
    seed: int = Field(default=42, ge=0)

class CompareRequest(StrictModel):
    experiment_id: str
