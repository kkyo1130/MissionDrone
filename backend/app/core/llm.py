from __future__ import annotations


def get_risk_alpha(
    wind_speed: float, wind_direction: float, vehicle_type: str = "default"
) -> float:
    """Return the prototype's deterministic placeholder risk weight."""
    base = 0.6
    speed_factor = min(max(wind_speed / 20.0, 0.0), 1.0) * 0.9
    direction_factor = abs((wind_direction % 180.0) - 90.0) / 90.0 * 0.3
    vehicle_bias = {
        "delivery": 0.1,
        "passenger": 0.2,
        "emergency": -0.1,
        "default": 0.0,
    }.get(vehicle_type, 0.0)

    alpha = base + speed_factor + direction_factor + vehicle_bias
    return max(0.2, min(alpha, 2.0))
