from __future__ import annotations

import numpy as np


def compute_risk_map(
    occupancy: np.ndarray, wind_speed: float, wind_direction: float
) -> np.ndarray:
    """Compute a normalized [0, 1] Venturi/vortex-inspired risk heuristic."""
    free = occupancy.astype(np.float32)
    blocked = 1.0 - free

    padded = np.pad(blocked, 1, mode="constant")
    local_blocked = np.zeros_like(free)
    for dx in range(3):
        for dy in range(3):
            for dz in range(3):
                local_blocked += padded[
                    dx : dx + free.shape[0],
                    dy : dy + free.shape[1],
                    dz : dz + free.shape[2],
                ]

    venturi = local_blocked / 27.0
    nx, ny, nz = occupancy.shape
    xs = np.linspace(-1.0, 1.0, nx, dtype=np.float32)
    ys = np.linspace(-1.0, 1.0, ny, dtype=np.float32)
    xg, yg = np.meshgrid(xs, ys, indexing="ij")

    theta = np.deg2rad(wind_direction)
    direction_field = xg * np.cos(theta) + yg * np.sin(theta)
    direction_field = (direction_field - direction_field.min()) / (
        direction_field.max() - direction_field.min() + 1e-6
    )
    direction_field = np.repeat(direction_field[:, :, None], nz, axis=2)

    spatial_risk = (0.65 * venturi + 0.35 * direction_field) * free
    maximum = float(spatial_risk.max())
    if maximum <= 1e-8:
        return spatial_risk
    # Normalize the spatial pattern only; scale by an absolute 8 m/s reference.
    # This preserves the legacy risk map at 8 m/s without cancelling wind speed.
    speed_factor = max(0.0, wind_speed) / 8.0
    return np.clip((spatial_risk / maximum) * speed_factor, 0.0, 1.0)
