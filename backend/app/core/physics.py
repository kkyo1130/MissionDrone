from __future__ import annotations

import numpy as np


def compute_risk_map(
    occupancy: np.ndarray, wind_speed: float, wind_direction: float
) -> np.ndarray:
    """Building-local wind exposure heuristic, not validated CFD.

    Obstacles generate nearby acceleration/turbulence zones. Wind direction
    (meteorological degrees, clockwise from north) biases the wake downwind.
    No buildings means no building-induced risk.
    """
    free = (occupancy != 0).astype(np.float32)
    blocked = 1.0 - free
    if not np.any(blocked) or wind_speed <= 0:
        return np.zeros_like(free)

    nx, ny, nz = blocked.shape
    # Spread the influence a few voxels from walls; never color solid buildings.
    influence = np.zeros_like(blocked)
    wake = np.zeros_like(blocked)
    theta = np.deg2rad(wind_direction)
    # Incoming wind is reported as 'from'; the wake is in the opposite direction.
    downwind_x = -np.sin(theta)
    downwind_y = -np.cos(theta)

    for dx in range(-4, 5):
        for dy in range(-4, 5):
            for dz in range(-2, 3):
                distance = np.sqrt(dx * dx + dy * dy + dz * dz)
                if distance == 0 or distance > 4.5:
                    continue
                src_x0, src_x1 = max(0, -dx), min(nx, nx - dx)
                src_y0, src_y1 = max(0, -dy), min(ny, ny - dy)
                src_z0, src_z1 = max(0, -dz), min(nz, nz - dz)
                dst_x0, dst_x1 = max(0, dx), min(nx, nx + dx)
                dst_y0, dst_y1 = max(0, dy), min(ny, ny + dy)
                dst_z0, dst_z1 = max(0, dz), min(nz, nz + dz)
                source = blocked[src_x0:src_x1, src_y0:src_y1, src_z0:src_z1]
                weight = 1.0 / (1.0 + distance * distance)
                influence[dst_x0:dst_x1, dst_y0:dst_y1, dst_z0:dst_z1] += source * weight
                alignment = (dx * downwind_x + dy * downwind_y) / distance
                if alignment > 0:
                    wake[dst_x0:dst_x1, dst_y0:dst_y1, dst_z0:dst_z1] += (
                        source * weight * alignment
                    )

    # Keep the spatial field localized; no artificial city-wide risk gradient.
    spatial = (0.55 * influence + 0.45 * wake) * free
    maximum = float(spatial.max())
    if maximum <= 1e-8:
        return np.zeros_like(free)
    speed_factor = max(0.0, wind_speed) / 8.0
    return np.clip(spatial / maximum * speed_factor, 0.0, 1.0)
