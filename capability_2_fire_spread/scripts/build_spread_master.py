#!/usr/bin/env python3
"""Assemble the master fire-spread dataset.

Step 7 of the fire-spread build.  One row per fire per satellite observation:
what the fire did since the previous observation (new area, distance, bearing)
beside the weather over that interval, the day's fire-weather codes, the fuel
mix and the terrain, with the fire's own attributes repeated on every row.

This is a replay set for the wind-driven spread simulator.  It is not an
ignition training table and none of its columns may enter the ignition panel.
Nothing is imputed: a value that could not be measured is blank.

Needs pandas and numpy.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from spread_common import DATASET_VERSION, INTERIM, PROCESSED, action_log


FIRST_OBSERVATION_WINDOW_HOURS = 12
HOLDOUT_FIRST_YEAR = 2021
FIRE_COLUMNS = [
    "fire_id", "dataset_version", "split", "fire_year", "district_code", "start_date", "out_date", "duration_days",
    "reported_size_ha", "perimeter_area_ha", "size_class", "cause", "cause_group", "response_code",
    "ignition_longitude", "ignition_latitude", "ignition_inside_perimeter", "ignition_distance_to_perimeter_m",
    "centroid_longitude", "centroid_latitude", "utc_offset_hours",
    "detections", "observations", "growth_steps", "detected_share_of_perimeter", "trackable",
    "first_detection_utc", "last_detection_utc", "first_detection_days_after_start",
]  # fmt: skip


def direction_from(u, v):
    """Bearing the wind blows from, degrees clockwise from true north; NaN when calm."""
    bearing = np.degrees(np.arctan2(-u, -v)) % 360
    bearing = np.where(bearing > 360 - 1e-9, 0.0, bearing)
    return np.where(np.hypot(u, v) < 1e-9, np.nan, bearing)


def angle_between(a_deg, b_deg):
    """Smallest absolute angle between two bearings, 0 to 180 degrees."""
    return np.abs((a_deg - b_deg + 180) % 360 - 180)


def interval_weather(hourly: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float | None]:
    """Weather over the hours in (start, end]; hourly values are stamped at the hour they describe."""
    times = hourly.time_utc.values
    rows = hourly.iloc[np.searchsorted(times, np.datetime64(start), side="right") : np.searchsorted(times, np.datetime64(end), side="right")]
    rows = rows.dropna(subset=["wind_speed_kmh", "wind_u_kmh", "wind_v_kmh"])
    if rows.empty:
        return {"weather_hours": 0}
    u, v, speed = rows.wind_u_kmh.mean(), rows.wind_v_kmh.mean(), rows.wind_speed_kmh.mean()
    return {
        "weather_hours": len(rows),
        "wind_u_kmh": u,
        "wind_v_kmh": v,
        "wind_speed_mean_kmh": speed,
        "wind_speed_max_kmh": rows.wind_speed_kmh.max(),
        "wind_direction_deg": float(direction_from(u, v)),
        "wind_steadiness": float(np.hypot(u, v) / speed) if speed > 0 else None,
        "temp_mean_c": rows.temp_c.mean(),
        "temp_max_c": rows.temp_c.max(),
        "rh_mean_pct": rows.rh_pct.mean(),
        "rh_min_pct": rows.rh_pct.min(),
        "precip_total_mm": rows.precip_mm.sum(),
    }


def build(args: argparse.Namespace) -> dict[str, object]:
    log = action_log("7 master dataset")
    fires = pd.read_csv(INTERIM / "spread_fires_with_progression.csv", dtype={"fire_id": str})
    observations = pd.read_csv(INTERIM / "spread_observations.csv", dtype={"fire_id": str})
    hourly = pd.read_csv(INTERIM / "spread_weather_hourly.csv.gz", dtype={"fire_id": str}, parse_dates=["time_utc"])
    daily = pd.read_csv(INTERIM / "spread_weather_daily.csv", dtype={"fire_id": str})
    fuel = pd.read_csv(INTERIM / "spread_fuel_summary.csv", dtype={"fire_id": str})
    terrain = pd.read_csv(INTERIM / "spread_terrain_summary.csv", dtype={"fire_id": str})
    log.info(f"START: {len(fires)} fires, {len(observations):,} observations, {len(hourly):,} weather hours, {len(daily):,} fire-weather days, {len(fuel)} fuel and {len(terrain)} terrain summaries")

    fires["split"] = np.where(~fires.satellite_archive_available, "no_satellite_archive", np.where(fires.fire_year >= HOLDOUT_FIRST_YEAR, "holdout", "calibration"))
    fires["dataset_version"] = DATASET_VERSION
    fire_table = fires[FIRE_COLUMNS].merge(fuel, on="fire_id", how="left").merge(terrain, on="fire_id", how="left")

    hourly_by_fire = {fire_id: frame.sort_values("time_utc").reset_index(drop=True) for fire_id, frame in hourly.groupby("fire_id")}
    offsets = dict(zip(fires.fire_id, fires.utc_offset_hours))
    weather_rows = []
    for fire_id, group in observations.groupby("fire_id", sort=False):
        frame = hourly_by_fire.get(fire_id)
        to_local = pd.Timedelta(hours=int(offsets[fire_id]))
        previous_end = None
        for row in group.itertuples():
            end = pd.Timestamp(row.time_utc)
            start = previous_end if previous_end is not None else end - pd.Timedelta(hours=FIRST_OBSERVATION_WINDOW_HOURS)
            values = {"weather_hours": 0} if frame is None else interval_weather(frame, start, end)
            midpoint_local = start + (end - start) / 2 + to_local
            weather_rows.append({"fire_id": fire_id, "observation": row.observation, "weather_window_start_utc": start.strftime("%Y-%m-%d %H:%M"), "fire_weather_date": midpoint_local.date().isoformat(), **values})
            previous_end = end
    master = observations.merge(pd.DataFrame(weather_rows), on=["fire_id", "observation"], how="left", validate="one_to_one")
    daily_columns = {"date": "fire_weather_date", "noon_temp_c": "noon_temp_c", "noon_rh_pct": "noon_rh_pct", "noon_wind_kmh": "noon_wind_kmh", "rain_24h_mm": "rain_24h_mm", "ffmc": "ffmc", "dmc": "dmc", "dc": "dc", "isi": "isi", "bui": "bui", "fwi": "fwi", "fine_fuel_moisture_pct": "fine_fuel_moisture_pct", "days_since_startup": "fire_weather_days_since_startup"}
    master = master.merge(daily[["fire_id", *daily_columns]].rename(columns=daily_columns), on=["fire_id", "fire_weather_date"], how="left", validate="many_to_one")

    master["spread_rate_m_per_h"] = (master.spread_distance_p90_m / master.hours_since_previous_observation).where(master.hours_since_previous_observation > 0)
    master["downwind_bearing_deg"] = (master.wind_direction_deg + 180) % 360
    master["head_vs_wind_angle_deg"] = angle_between(master.head_bearing_deg, master.downwind_bearing_deg)
    master["spread_vs_wind_angle_deg"] = angle_between(master.spread_bearing_deg, master.downwind_bearing_deg)
    # Fire-level totals get a fire_ prefix so they cannot be confused with the per-observation counts.
    fire_level = fire_table.rename(columns={"detections": "fire_detections", "observations": "fire_observations", "growth_steps": "fire_growth_steps"})
    master = fire_level.merge(master, on="fire_id", how="right", validate="one_to_many").sort_values(["fire_id", "observation"])

    PROCESSED.mkdir(parents=True, exist_ok=True)
    outputs = {
        "master": "ontario_fire_spread_master_v1.csv",
        "fires": "ontario_fire_spread_fires_v1.csv",
        "weather_hourly": "ontario_fire_spread_weather_hourly_v1.csv.gz",
        "weather_daily": "ontario_fire_spread_weather_daily_v1.csv",
        "arrival_cells": "ontario_fire_spread_arrival_cells_v1.csv.gz",
        "detections": "ontario_fire_spread_detections_v1.csv.gz",
    }
    master.round(4).to_csv(PROCESSED / outputs["master"], index=False)
    fire_table.to_csv(PROCESSED / outputs["fires"], index=False)
    hourly.assign(time_utc=hourly.time_utc.dt.strftime("%Y-%m-%d %H:%M")).to_csv(PROCESSED / outputs["weather_hourly"], index=False)
    daily.to_csv(PROCESSED / outputs["weather_daily"], index=False)
    pd.read_csv(INTERIM / "spread_arrival_cells.csv.gz").to_csv(PROCESSED / outputs["arrival_cells"], index=False)
    pd.read_csv(INTERIM / "spread_detections.csv.gz").to_csv(PROCESSED / outputs["detections"], index=False)

    growth = master[(master.new_cells > 0) & (master.growth_reference == "earlier_detections")]
    windy = growth[growth.head_vs_wind_angle_deg.notna() & (growth.wind_speed_mean_kmh >= 10)]
    report = {
        "dataset_version": DATASET_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "retrospective_hindcast_not_operational_forecast",
        "grain": "one row per fire per satellite observation",
        "counts": {
            "fires_in_catalogue": len(fire_table),
            "fires_in_master": int(master.fire_id.nunique()),
            "rows": len(master),
            "columns": master.shape[1],
            "growth_steps": len(growth),
            "rows_with_interval_weather": int((master.weather_hours > 0).sum()),
            "rows_with_fire_weather_codes": int(master.fwi.notna().sum()),
            "rows_with_fuel_summary": int(master.fuel_around_conifer_share.notna().sum()),
            "rows_with_terrain_summary": int(master.terrain_window_slope_mean_pct.notna().sum()),
        },
        "split": {
            name: {"fires": int((fire_table.split == name).sum()), "fires_in_master": int(master[master.split == name].fire_id.nunique()), "rows": int((master.split == name).sum())}
            for name in ("calibration", "holdout", "no_satellite_archive")
        },
        "split_rule": f"calibration: fire years before {HOLDOUT_FIRST_YEAR}; holdout: {HOLDOUT_FIRST_YEAR} onward; fires in years with no FIRMS yearly archive have no rows",
        "coherence": {
            "definition": "angle between the observed head direction and the downwind direction, on growth steps with mean wind of at least 10 km/h",
            "steps": len(windy),
            "share_within_45_deg": None if windy.empty else round(float((windy.head_vs_wind_angle_deg <= 45).mean()), 4),
            "share_within_45_deg_if_unrelated": 0.25,
            "median_angle_deg": None if windy.empty else round(float(windy.head_vs_wind_angle_deg.median()), 1),
        },
        "outputs": outputs,
    }
    (PROCESSED / "ontario_fire_spread_v1_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(
        f"DONE: master has {len(master):,} rows x {master.shape[1]} columns for {master.fire_id.nunique()} fires; "
        f"{len(growth):,} growth steps; head within 45 degrees of downwind on {report['coherence']['share_within_45_deg']:.1%} of windy steps (25% if unrelated)"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
