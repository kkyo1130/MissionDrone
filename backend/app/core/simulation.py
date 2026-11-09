"""Synthetic wind scenarios and safety-first route scoring (adapted from PR #2)."""
from __future__ import annotations

import math
import random
from typing import Any

from app.schemas.missions import DeliveryMission, DisasterMission, MissionPayload


def simulate_route(route: dict[str, Any], mission: MissionPayload, weather: dict[str, Any], seed: int) -> dict[str, Any]:
    """Evaluate ENU route segments in meters; no real-world flight safety guarantee."""
    points = [tuple(p["enu_m"]) for p in route["coordinates"]]
    constraints = mission.constraints
    rng = random.Random(seed)
    nominal = float(weather["wind_speed_mps"])
    speed = max(0.0, nominal * (1.0 + rng.uniform(-0.05, 0.05)))
    heading = math.radians(float(weather["wind_from_deg"]))
    wind = (-speed * math.sin(heading), -speed * math.cos(heading), 0.0)
    payload_kg = mission.payload_kg if isinstance(mission, DeliveryMission) else 0.0
    duration = energy = 0.0
    failures: list[str] = []
    for a, b in zip(points, points[1:]):
        distance = math.dist(a, b)
        if distance <= 0:
            continue
        direction = tuple((y - x) / distance for x, y in zip(a, b))
        along = sum(x * y for x, y in zip(wind, direction))
        cross_sq = max(0.0, speed * speed - along * along)
        if cross_sq >= constraints.speed_mps**2:
            failures.append("wind_exceeds_track_hold")
            break
        ground_speed = math.sqrt(constraints.speed_mps**2 - cross_sq) + along
        if ground_speed <= 0.5:
            failures.append("insufficient_ground_speed")
            break
        seconds = distance / ground_speed
        if abs(b[2] - a[2]) / seconds > constraints.climb_rate_mps + 1e-9:
            failures.append("climb_rate")
        power = 180 + 25 * payload_kg + 4 * speed * speed + max(0.0, direction[2]) * 100
        energy += power * seconds / 3600
        duration += seconds
    if energy > constraints.battery_budget_wh:
        failures.append("battery_budget")
    if isinstance(mission, DisasterMission) and duration > mission.time_limit_s:
        failures.append("time_budget")
    return {
        "safe": not failures,
        "failure_reasons": sorted(set(failures)),
        "distance_m": route["distance_m"],
        "time_s": duration,
        "energy_wh": energy,
        "risk_exposure": route["risk_exposure"],
        "minimum_clearance_m": route["minimum_clearance_m"],
        "synthetic": True,
    }


def compare_runs(runs: list[dict[str, Any]], mission: MissionPayload) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        grouped.setdefault(run["route_id"], []).append(run)
    eligible = {rid: group for rid, group in grouped.items() if all(run["metrics"]["safe"] for run in group)}
    weights = mission.cost_weights.model_dump()
    keys = ["distance_m", "risk_exposure", "time_s", "energy_wh"]
    means = {
        rid: {key: sum(run["metrics"][key] for run in group) / len(group) for key in keys}
        for rid, group in eligible.items()
    }
    scores: dict[str, float] = {}
    for rid, values in means.items():
        cost = 0.0
        for key, weight in zip(keys, (weights["distance"], weights["risk"], weights["time"], weights["energy"])):
            low = min(value[key] for value in means.values())
            high = max(value[key] for value in means.values())
            cost += weight * ((values[key] - low) / (high - low) if high > low else 0.0)
        scores[rid] = round(100 * (1 - cost), 6)
    winner = max(scores, key=scores.get) if scores else None
    return {
        "recommended_route_id": winner,
        "scores": scores,
        "excluded_route_ids": sorted(set(grouped) - set(eligible)),
        "reason": "All selected weather runs passed safety constraints." if winner else "No route passed all selected weather runs.",
        "weights": weights,
    }
