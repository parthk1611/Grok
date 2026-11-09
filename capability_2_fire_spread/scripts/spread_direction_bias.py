#!/usr/bin/env python3
"""Measure the grid's wind-azimuth bias and how much of the Monte Carlo band width it explains (SYNTHETIC cases only).

On uniform fuel and flat ground the burned area cannot depend on which way the wind blows, yet the 90 m grid makes fire
bigger when the wind is diagonal.  This script prints (1) the deterministic area for wind from each of eight directions at
1, 3 and 6 h, and (2) for the Monte Carlo bands, how wide they are when only wind direction is perturbed versus when
everything is, so the direction-driven part of the width can be separated from the grid artefact.

    python scripts/spread_direction_bias.py            # prints markdown tables
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spread_api as API  # noqa: E402
import simulate_spread as SIM  # noqa: E402
import spread_montecarlo as MC  # noqa: E402

SIZE = 301
HORIZONS = (1, 3, 6)
DIRECTIONS = (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0)
ROS_SCALE = 0.8


def deterministic_areas(wind: float) -> dict[float, dict[int, float]]:
    out = {}
    for wind_from in DIRECTIONS:
        case = MC.synthetic_case(size=SIZE, wind=wind, wind_from=wind_from, convergence_deg=0.0)  # grid-aligned: wind azimuth is relative to the grid
        result = API.run_point(case, MC.SYNTHETIC_START, ignition_xy=MC.synthetic_ignition_xy(case), settings=SIM.Settings(ros_scale=ROS_SCALE))
        out[wind_from] = {h: result.area_ha[float(h)] for h in HORIZONS}
    return out


def band_stats(wind_from: float, config: MC.PerturbationConfig, n_runs: int, seed: int) -> dict:
    case = MC.synthetic_case(size=SIZE, wind_from=wind_from, convergence_deg=0.0)
    result = MC.run_monte_carlo(case, MC.SYNTHETIC_START, ignition_xy=MC.synthetic_ignition_xy(case), seed=seed, n_runs=n_runs, config=config, mode="retrospective", **MC.SYNTHETIC_LABELS)
    area = {(f["properties"]["horizon_h"], f["properties"]["band"]): f["properties"]["area_ha"] for f in result.geojson["features"]}
    runs = result.run_area_ha
    return {
        "p10_ha": area[6, "p10"], "p50_ha": area[6, "p50"], "p90_ha": area[6, "p90"], "p10_over_p90": area[6, "p10"] / area[6, "p90"],
        "run_area_cv": float(runs[:, 2].std() / runs[:, 2].mean()), "run_area_max_over_min": float(runs[:, 2].max() / runs[:, 2].min()), "edge_runs": result.edge_runs,
    }  # fmt: skip


def main() -> None:
    print(f"Grid {SIZE}x{SIZE} at 90 m, uniform C-2 spruce, flat, constant weather, ros_scale {ROS_SCALE} (SYNTHETIC)\n")
    for wind in (10.0, 20.0):
        areas = deterministic_areas(wind)
        print(f"### Deterministic burned area (ha), wind {wind:g} km/h\n\n| wind from | " + " | ".join(f"{h} h" for h in HORIZONS) + " |\n|---|---|---|---|")
        for wind_from, row in areas.items():
            print(f"| {wind_from:g} | " + " | ".join(f"{row[h]:.1f}" for h in HORIZONS) + " |")
        cardinal = {h: np.mean([areas[d][h] for d in (0.0, 90.0, 180.0, 270.0)]) for h in HORIZONS}
        diagonal = {h: np.mean([areas[d][h] for d in (45.0, 135.0, 225.0, 315.0)]) for h in HORIZONS}
        print("\n| horizon | cardinal mean | diagonal mean | diagonal / cardinal |\n|---|---|---|---|")
        for h in HORIZONS:
            print(f"| {h} h | {cardinal[h]:.1f} | {diagonal[h]:.1f} | {diagonal[h] / cardinal[h]:.3f} |")
        print()
    print("### 6 h bands, 20 km/h, n_runs 100, seed 385\n\n| perturbed | wind from | p10 ha | p50 ha | p90 ha | p10/p90 | per-run area CV | per-run max/min |\n|---|---|---|---|---|---|---|---|")
    only_direction = MC.PerturbationConfig(wind_speed_log_sigma=0.0, wind_direction_sigma_deg=15.0, ffmc_sigma=0.0, ros_scale_log_sigma=0.0)
    for label, config in (("direction only (15 deg)", only_direction), ("all (provisional defaults)", MC.PerturbationConfig())):
        for wind_from in (270.0, 225.0):
            s = band_stats(wind_from, config, 100, 385)
            print(f"| {label} | {wind_from:g} | {s['p10_ha']:.1f} | {s['p50_ha']:.1f} | {s['p90_ha']:.1f} | {s['p10_over_p90']:.2f} | {s['run_area_cv']:.1%} | {s['run_area_max_over_min']:.3f} |" + (f" edge runs {s['edge_runs']}" if s["edge_runs"] else ""))


if __name__ == "__main__":
    main()
