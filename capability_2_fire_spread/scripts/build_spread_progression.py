#!/usr/bin/env python3
"""Rebuild how each fire grew from VIIRS satellite detections.

Step 4 of the fire-spread build.  Detections are matched to a fire when they
fall on or just outside its final perimeter between its start and out dates.
Detections close together in time form one observation (a satellite pass), and
each observation records where the fire had newly reached, how far that is from
where it was already seen, and in which direction.

Work is done in EPSG:3978 (NAD83 / Canada Atlas Lambert, metres), the grid the
ignition panel already uses.  Needs pandas, numpy, scipy, shapely, and pyproj.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import shapely
from pyproj import Geod, Transformer
from scipy.spatial import cKDTree
from shapely.geometry import shape
from shapely.ops import transform

from spread_common import DATASET_VERSION, INTERIM, RAW, action_log


CELL_M = 375.0  # nominal VIIRS I-band pixel
CELL_HA = CELL_M * CELL_M / 10_000
PERIMETER_BUFFER_M = 750.0  # two pixels: detection footprint and geolocation error
DAYS_BEFORE_START = 3  # a fire can be seen before the agency's reported start date
DAYS_AFTER_OUT = 1
OBSERVATION_GAP_MINUTES = 90  # passes closer together than this are one observation
HEAD_QUANTILE = 0.9
CENTRAL_TIME_WEST_OF_LONGITUDE = -90.0


def bearing(east: float, north: float) -> float | None:
    if np.hypot(east, north) < 1e-9:
        return None
    return float(np.degrees(np.arctan2(east, north)) % 360)


def growth_geometry(new_xy: np.ndarray, earlier_xy: np.ndarray) -> dict[str, float | None]:
    """Distance and direction from already-seen fire to newly seen cells (grid bearings)."""
    distance, index = cKDTree(earlier_xy).query(new_xy)
    offset = new_xy - earlier_xy[index]
    head = distance >= np.quantile(distance, HEAD_QUANTILE)
    mean_distance = float(distance.mean())
    return {
        "distance_mean_m": mean_distance,
        "distance_p90_m": float(np.quantile(distance, HEAD_QUANTILE)),
        "distance_max_m": float(distance.max()),
        "grid_bearing": bearing(offset[:, 0].mean(), offset[:, 1].mean()),
        "grid_head_bearing": bearing(offset[head, 0].mean(), offset[head, 1].mean()),
        "directionality": 0.0 if mean_distance == 0 else float(np.hypot(offset[:, 0].mean(), offset[:, 1].mean()) / mean_distance),
    }


def load_detections(year: int, log) -> pd.DataFrame:
    parts = []
    for path in sorted((RAW / "firms").glob(f"viirs-*_{year}_ontario.csv.gz")):
        parts.append(pd.read_csv(path, dtype={"acq_time": str, "confidence": str, "satellite": str}))
    if not parts:
        return pd.DataFrame()
    frame = pd.concat(parts, ignore_index=True)
    total = len(frame)
    frame = frame[(frame.type == 0) & (frame.confidence != "l")].copy()
    log.info(f"{year}: {total:,} detections read; kept {len(frame):,} that are vegetation fires (type 0) with nominal or high confidence")
    frame["time_utc"] = pd.to_datetime(frame.acq_date + " " + frame.acq_time.str.zfill(4), format="%Y-%m-%d %H%M")
    return frame


def build(args: argparse.Namespace) -> dict[str, object]:
    log = action_log("4 fire progression")
    fires = pd.read_csv(INTERIM / "spread_fires.csv", dtype={"fire_id": str})
    features = json.loads((INTERIM / "spread_fire_perimeters.geojson").read_text(encoding="utf-8"))["features"]
    to_grid = Transformer.from_crs("EPSG:4326", "EPSG:3978", always_xy=True)
    to_geographic = Transformer.from_crs("EPSG:3978", "EPSG:4326", always_xy=True)
    geod = Geod(ellps="GRS80")
    perimeters = {feature["properties"]["fire_id"]: transform(to_grid.transform, shape(feature["geometry"])) for feature in features}
    log.info(f"START: {len(fires):,} fires; perimeters projected to EPSG:3978; buffer {PERIMETER_BUFFER_M:.0f} m; time window start-{DAYS_BEFORE_START}d to out+{DAYS_AFTER_OUT}d")

    detection_parts, observation_rows, cell_parts, summaries = [], [], [], {}
    counts = {"detections_read_after_quality_filter": 0, "detections_matched_to_a_fire": 0, "detections_claimed_by_two_fires": 0}
    archive_years: set[int] = set()
    for year, year_fires in fires.groupby("fire_year"):
        detections = load_detections(int(year), log)
        if detections.empty:
            log.info(f"{year}: no FIRMS yearly archive; {len(year_fires)} fires get no satellite progression")
            continue
        archive_years.add(int(year))
        counts["detections_read_after_quality_filter"] += len(detections)
        x, y = to_grid.transform(detections.longitude.values, detections.latitude.values)
        detections["x"], detections["y"] = x, y
        ids = year_fires.fire_id.tolist()
        exact = [perimeters[fire_id] for fire_id in ids]
        buffered = [polygon.buffer(PERIMETER_BUFFER_M) for polygon in exact]
        tree = shapely.STRtree(buffered)
        point_index, fire_index = tree.query(shapely.points(x, y), predicate="intersects")
        pairs = pd.DataFrame({"row": point_index, "fire": fire_index})
        window_start = pd.to_datetime(year_fires.start_date.values) - pd.Timedelta(days=DAYS_BEFORE_START)
        window_end = pd.to_datetime(year_fires.out_date.values) + pd.Timedelta(days=DAYS_AFTER_OUT + 1)
        times = detections.time_utc.values[pairs.row.values]
        pairs = pairs[(times >= window_start.values[pairs.fire.values]) & (times < window_end.values[pairs.fire.values])].copy()
        pairs["gap"] = shapely.distance(shapely.points(x[pairs.row.values], y[pairs.row.values]), np.array(exact, dtype=object)[pairs.fire.values])
        counts["detections_claimed_by_two_fires"] += int(pairs.duplicated("row").sum())
        pairs = pairs.sort_values("gap").drop_duplicates("row")
        matched = detections.iloc[pairs.row.values].copy()
        matched["fire_id"] = np.array(ids)[pairs.fire.values]
        matched["distance_outside_perimeter_m"] = pairs.gap.values.round(0)
        counts["detections_matched_to_a_fire"] += len(matched)
        log.info(f"{year}: matched {len(matched):,} detections to {matched.fire_id.nunique()} of {len(ids)} fires")

        for fire_id, group in matched.groupby("fire_id"):
            fire = year_fires[year_fires.fire_id == fire_id].iloc[0]
            group = group.sort_values("time_utc")
            gap_minutes = group.time_utc.diff().dt.total_seconds().div(60)
            group["observation"] = (gap_minutes > OBSERVATION_GAP_MINUTES).cumsum() + 1
            group["cell_x"] = np.floor(group.x / CELL_M).astype(int)
            group["cell_y"] = np.floor(group.y / CELL_M).astype(int)
            offset_hours = -6 if fire.centroid_longitude < CENTRAL_TIME_WEST_OF_LONGITUDE else -5
            centre = perimeters[fire_id].centroid
            lon0, lat0 = to_geographic.transform(centre.x, centre.y)
            lon1, lat1 = to_geographic.transform(centre.x, centre.y + 1_000)
            convergence = geod.inv(lon0, lat0, lon1, lat1)[0]
            ignition_xy = None if pd.isna(fire.ignition_longitude) else np.array([to_grid.transform(fire.ignition_longitude, fire.ignition_latitude)])
            seen: dict[tuple[int, int], int] = {}
            previous_time = first_time = None
            for observation, passes in group.groupby("observation"):
                time_utc = passes.time_utc.iloc[len(passes) // 2]
                cells = sorted(set(zip(passes.cell_x, passes.cell_y)))
                new_cells = [cell for cell in cells if cell not in seen]
                geometry = {"distance_mean_m": None, "distance_p90_m": None, "distance_max_m": None, "grid_bearing": None, "grid_head_bearing": None, "directionality": None}
                reference = ""
                if new_cells:
                    new_xy = (np.array(new_cells) + 0.5) * CELL_M
                    if seen:
                        geometry, reference = growth_geometry(new_xy, (np.array(list(seen)) + 0.5) * CELL_M), "earlier_detections"
                    elif ignition_xy is not None:
                        geometry, reference = growth_geometry(new_xy, ignition_xy), "reported_ignition_point"
                for cell in new_cells:
                    seen[cell] = int(observation)
                    cell_parts.append((fire_id, cell[0], cell[1], int(observation), time_utc))
                first_time = first_time or time_utc
                true_bearing = {key: None if geometry[key] is None else round((geometry[key] + convergence) % 360, 1) for key in ("grid_bearing", "grid_head_bearing")}
                observation_rows.append(
                    {
                        "fire_id": fire_id,
                        "observation": int(observation),
                        "time_utc": time_utc.strftime("%Y-%m-%d %H:%M"),
                        "time_local": (time_utc + pd.Timedelta(hours=offset_hours)).strftime("%Y-%m-%d %H:%M"),
                        "day_or_night": passes.daynight.mode().iloc[0],
                        "satellites": "+".join(sorted(set(passes.satellite))),
                        "detections": len(passes),
                        "cells_detected": len(cells),
                        "new_cells": len(new_cells),
                        "cumulative_cells": len(seen),
                        "new_area_ha": round(len(new_cells) * CELL_HA, 2),
                        "cumulative_area_ha": round(len(seen) * CELL_HA, 2),
                        "hours_since_previous_observation": None if previous_time is None else round((time_utc - previous_time).total_seconds() / 3600, 2),
                        "hours_since_first_observation": round((time_utc - first_time).total_seconds() / 3600, 2),
                        "growth_reference": reference,
                        "spread_distance_mean_m": None if geometry["distance_mean_m"] is None else round(geometry["distance_mean_m"]),
                        "spread_distance_p90_m": None if geometry["distance_p90_m"] is None else round(geometry["distance_p90_m"]),
                        "spread_distance_max_m": None if geometry["distance_max_m"] is None else round(geometry["distance_max_m"]),
                        "spread_bearing_deg": true_bearing["grid_bearing"],
                        "head_bearing_deg": true_bearing["grid_head_bearing"],
                        "spread_directionality": None if geometry["directionality"] is None else round(geometry["directionality"], 3),
                        "frp_total_mw": round(float(passes.frp.sum()), 1),
                        "frp_max_mw": round(float(passes.frp.max()), 1),
                    }
                )
                previous_time = time_utc
            first = group.iloc[0]
            summaries[fire_id] = {
                "utc_offset_hours": offset_hours,
                "grid_convergence_deg": round(convergence, 3),
                "detections": len(group),
                "observations": int(group.observation.max()),
                "first_detection_utc": group.time_utc.iloc[0].strftime("%Y-%m-%d %H:%M"),
                "last_detection_utc": group.time_utc.iloc[-1].strftime("%Y-%m-%d %H:%M"),
                "first_detection_longitude": round(float(first.longitude), 5),
                "first_detection_latitude": round(float(first.latitude), 5),
                "first_detection_days_after_start": (group.time_utc.iloc[0].normalize() - pd.Timestamp(fire.start_date)).days,
                "detected_area_ha": round(len(seen) * CELL_HA, 2),
                "detected_share_of_perimeter": round(len(seen) * CELL_HA / fire.perimeter_area_ha, 3),
            }
            detection_parts.append(group[["fire_id", "observation", "time_utc", "satellite", "longitude", "latitude", "x", "y", "frp", "confidence", "daynight", "distance_outside_perimeter_m"]])

    summary = pd.DataFrame.from_dict(summaries, orient="index").rename_axis("fire_id").reset_index()
    fires = fires.merge(summary, on="fire_id", how="left")
    fires["detections"] = fires.detections.fillna(0).astype(int)
    fires["observations"] = fires.observations.fillna(0).astype(int)
    fires["satellite_archive_available"] = fires.fire_year.isin(archive_years)
    observations = pd.DataFrame(observation_rows)
    growth_steps = observations[(observations.new_cells > 0) & (observations.growth_reference == "earlier_detections")].groupby("fire_id").size()
    fires["growth_steps"] = fires.fire_id.map(growth_steps).fillna(0).astype(int)
    fires["trackable"] = (fires.growth_steps >= args.min_growth_steps) & (fires.detected_share_of_perimeter.fillna(0) >= args.min_detected_share)
    detections_out = pd.concat(detection_parts, ignore_index=True)
    detections_out[["x", "y"]] = detections_out[["x", "y"]].round(1)
    cells = pd.DataFrame(cell_parts, columns=["fire_id", "cell_x", "cell_y", "first_observation", "first_time_utc"])
    cells["cell_centre_x_m"] = (cells.cell_x + 0.5) * CELL_M
    cells["cell_centre_y_m"] = (cells.cell_y + 0.5) * CELL_M

    fires.to_csv(INTERIM / "spread_fires_with_progression.csv", index=False)
    observations.to_csv(INTERIM / "spread_observations.csv", index=False)
    detections_out.to_csv(INTERIM / "spread_detections.csv.gz", index=False)
    cells.to_csv(INTERIM / "spread_arrival_cells.csv.gz", index=False)

    with_satellite = fires[fires.detections > 0]
    interval = observations.hours_since_previous_observation.dropna()
    report = {
        "dataset_version": DATASET_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "method": {
            "grid": "EPSG:3978, 375 m cells aligned to multiples of 375 m",
            "match": f"type 0, confidence nominal or high, within {PERIMETER_BUFFER_M:.0f} m of the final perimeter, from {DAYS_BEFORE_START} days before the start date to {DAYS_AFTER_OUT} day after the out date",
            "observation": f"detections separated by no more than {OBSERVATION_GAP_MINUTES} minutes",
            "trackable": f"at least {args.min_growth_steps} growth steps and detections covering at least {args.min_detected_share:.0%} of the perimeter area",
        },
        "counts": {
            **counts,
            "fires": len(fires),
            "fires_in_years_without_satellite_archive": int((~fires.satellite_archive_available).sum()),
            "fires_with_detections": len(with_satellite),
            "fires_trackable": int(fires.trackable.sum()),
            "observations": len(observations),
            "observations_with_new_area": int((observations.new_cells > 0).sum()),
            "arrival_cells": len(cells),
        },
        "hours_between_observations": {str(q): round(float(interval.quantile(q)), 1) for q in (0.1, 0.25, 0.5, 0.75, 0.9)},
        "detected_share_of_perimeter": {str(q): round(float(with_satellite.detected_share_of_perimeter.quantile(q)), 2) for q in (0.1, 0.5, 0.9)},
        "outputs": {"fires": "spread_fires_with_progression.csv", "observations": "spread_observations.csv", "detections": "spread_detections.csv.gz", "arrival_cells": "spread_arrival_cells.csv.gz"},
    }
    (INTERIM / "spread_progression_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(
        f"DONE: {counts['detections_matched_to_a_fire']:,} detections matched; {len(with_satellite)} of {len(fires)} fires seen by satellite; "
        f"{len(observations):,} observations; {int(fires.trackable.sum())} fires trackable; median {interval.median():.1f} h between observations"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-growth-steps", type=int, default=3)
    parser.add_argument("--min-detected-share", type=float, default=0.2)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
