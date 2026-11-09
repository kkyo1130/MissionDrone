from __future__ import annotations

import math
from typing import Annotated, Literal, Tuple, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

VoxelPoint = Tuple[StrictInt, StrictInt, StrictInt]


class MissionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FlightConstraints(MissionModel):
    min_altitude_m: float = Field(
        ge=0,
        description="Minimum altitude above the local grid origin, in meters.",
    )
    max_altitude_m: float = Field(gt=0)
    speed_mps: float = Field(gt=0)
    climb_rate_mps: float = Field(gt=0)
    battery_budget_wh: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_altitude_and_climb(self) -> FlightConstraints:
        if self.min_altitude_m >= self.max_altitude_m:
            raise ValueError("min_altitude_m must be less than max_altitude_m")
        if self.climb_rate_mps > self.speed_mps:
            raise ValueError("climb_rate_mps cannot exceed speed_mps")
        return self


class CostWeights(MissionModel):
    distance: float = Field(ge=0, le=1)
    risk: float = Field(ge=0, le=1)
    time: float = Field(ge=0, le=1)
    energy: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> CostWeights:
        total = self.distance + self.risk + self.time + self.energy
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError("cost weights must sum to 1")
        return self


class MissionBase(MissionModel):
    mission_type: str
    name: str = Field(min_length=1, max_length=100)
    start: VoxelPoint
    end: VoxelPoint
    waypoints: list[VoxelPoint] = Field(default_factory=list)
    required_waypoints: list[VoxelPoint] = Field(default_factory=list)
    constraints: FlightConstraints
    cost_weights: CostWeights = Field(
        default_factory=lambda: CostWeights(
            distance=0.25, risk=0.25, time=0.25, energy=0.25
        )
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @model_validator(mode="after")
    def validate_waypoints(self) -> MissionBase:
        if len(set(self.waypoints)) != len(self.waypoints):
            raise ValueError("waypoints must not contain duplicates")
        if len(set(self.required_waypoints)) != len(self.required_waypoints):
            raise ValueError("required_waypoints must not contain duplicates")
        if not set(self.required_waypoints).issubset(self.waypoints):
            raise ValueError("required_waypoints must be included in waypoints")
        return self


class InspectionMission(MissionBase):
    mission_type: Literal["inspection"]
    target_points: list[VoxelPoint] = Field(min_length=1)
    view_distance_m: float = Field(gt=0)
    cost_weights: CostWeights = Field(
        default_factory=lambda: CostWeights(
            distance=0.2, risk=0.35, time=0.25, energy=0.2
        )
    )


class DisasterMission(MissionBase):
    mission_type: Literal["disaster"]
    search_area: list[VoxelPoint] = Field(min_length=1)
    priority_points: list[VoxelPoint] = Field(default_factory=list)
    time_limit_s: float = Field(gt=0)
    cost_weights: CostWeights = Field(
        default_factory=lambda: CostWeights(
            distance=0.15, risk=0.3, time=0.4, energy=0.15
        )
    )

    @model_validator(mode="after")
    def validate_priority_points(self) -> DisasterMission:
        if not set(self.priority_points).issubset(self.search_area):
            raise ValueError("priority_points must be included in search_area")
        return self


class DeliveryMission(MissionBase):
    mission_type: Literal["delivery"]
    payload_kg: float = Field(ge=0)
    cost_weights: CostWeights = Field(
        default_factory=lambda: CostWeights(
            distance=0.25, risk=0.25, time=0.25, energy=0.25
        )
    )


MissionPayload = Annotated[
    Union[InspectionMission, DisasterMission, DeliveryMission],
    Field(discriminator="mission_type"),
]
