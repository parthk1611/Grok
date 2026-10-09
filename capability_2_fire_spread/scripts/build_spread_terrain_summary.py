#!/usr/bin/env python3
"""Compute slope and aspect from each fire's elevation window and summarise them.

Step 6d of the fire-spread build.  Slope (percent and degrees) and aspect are
derived from the 90 m MRDEM window by central differences.  Aspect is the
compass direction the ground faces downhill, stored as sine and cosine so that
359° and 1° stay close.  Each fire gets terrain statistics for the area inside
its final perimeter and for its whole window.  Needs numpy, pandas, rasterio,
and shapely.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import rasterio
import shapely

from spread_common import DATASET_VERSION, INTERIM, RAW, action_log, load_perimeters_3978


SOURCE = RAW / "fuel_terrain"
NODATA = -32767.0


def slope_and_aspect(elevation: np.ndarray, pixel_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Slope in percent and downslope aspect in degrees clockwise from grid north."""
    d_south, d_east = np.gradient(elevation, pixel_m)  # rows run north to south
    d_north = -d_south
    slope_pct = 100.0 * np.hypot(d_east, d_north)
    aspect = np.degrees(np.arctan2(-d_east, -d_north)) % 360
    return slope_pct, aspect


def summarise(elevation, slope_pct, aspect, mask, prefix: str) -> dict[str, float | None]:
    if not mask.any():
        return {f"{prefix}_elevation_mean_m": None}
    radians = np.radians(aspect[mask])
    weight = slope_pct[mask]
    total = weight.sum()
    return {
        f"{prefix}_elevation_mean_m": round(float(elevation[mask].mean()), 1),
        f"{prefix}_elevation_range_m": round(float(elevation[mask].max() - elevation[mask].min()), 1),
        f"{prefix}_slope_mean_pct": round(float(slope_pct[mask].mean()), 2),
        f"{prefix}_slope_p90_pct": round(float(np.quantile(slope_pct[mask], 0.9)), 2),
        f"{prefix}_slope_mean_deg": round(float(np.degrees(np.arctan(slope_pct[mask] / 100.0)).mean()), 2),
        # slope-weighted, so flat ground with an arbitrary aspect does not dominate
        f"{prefix}_aspect_sin": round(float((np.sin(radians) * weight).sum() / total), 3) if total > 0 else None,
        f"{prefix}_aspect_cos": round(float((np.cos(radians) * weight).sum() / total), 3) if total > 0 else None,
    }


def main() -> None:
    log = action_log("6d terrain summary")
    fuel_windows = {window["fire_id"]: window for window in json.loads((SOURCE / "fuel_windows_manifest.json").read_text(encoding="utf-8"))["windows"]}
    terrain = json.loads((SOURCE / "terrain_windows_manifest.json").read_text(encoding="utf-8"))["windows"]
    perimeters = load_perimeters_3978()
    log.info(f"START: slope and aspect for {len(terrain)} terrain windows")
    rows = []
    for entry in terrain:
        window = fuel_windows[entry["fire_id"]]
        with rasterio.open(SOURCE / "windows" / entry["filename"]) as source:
            elevation = source.read(1).astype("float64")
        valid = elevation != NODATA
        elevation[~valid] = np.nan
        pixel = window["pixel_size_m"]
        slope_pct, aspect = slope_and_aspect(elevation, pixel)
        usable = valid & np.isfinite(slope_pct)
        x = window["west"] + (np.arange(window["columns"]) + 0.5) * pixel
        y = window["north"] - (np.arange(window["rows"]) + 0.5) * pixel
        grid_x, grid_y = np.meshgrid(x, y)
        perimeter = perimeters[entry["fire_id"]]
        shapely.prepare(perimeter)
        inside = shapely.contains_xy(perimeter, grid_x, grid_y) & usable
        row = {"fire_id": entry["fire_id"], "terrain_window_file": entry["filename"], "terrain_valid_share": entry["valid_share"]}
        row.update(summarise(elevation, slope_pct, aspect, inside, "terrain_inside"))
        row.update(summarise(elevation, slope_pct, aspect, usable, "terrain_window"))
        rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(INTERIM / "spread_terrain_summary.csv", index=False)
    report = {
        "dataset_version": DATASET_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "method": "central differences on the 90 m DTM; aspect is the downslope direction from grid north, slope-weighted when averaged",
        "fires": len(table),
        "slope_mean_pct_quantiles": {str(q): round(float(table.terrain_window_slope_mean_pct.quantile(q)), 2) for q in (0.1, 0.5, 0.9, 1.0)},
        "elevation_mean_m_range": [round(float(table.terrain_window_elevation_mean_m.min()), 1), round(float(table.terrain_window_elevation_mean_m.max()), 1)],
        "outputs": {"summary": "spread_terrain_summary.csv"},
    }
    (INTERIM / "spread_terrain_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(f"DONE: terrain summary for {len(table)} fires; median window slope {table.terrain_window_slope_mean_pct.median():.2f}%, steepest {table.terrain_window_slope_mean_pct.max():.2f}%")


if __name__ == "__main__":
    main()
