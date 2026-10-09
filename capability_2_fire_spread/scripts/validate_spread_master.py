#!/usr/bin/env python3
"""Check the master fire-spread dataset and record the results.

Step 8 of the fire-spread build.  Structural checks must pass.  Sanity checks
compare independent sources (satellite area against the agency's perimeter, the
computed Fire Weather Index against the one the agency recorded) and confirm
the physics shows up (fires run downwind and grow more in drier, windier
weather).  Results go to the build log and to a JSON report.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from spread_common import DATASET_VERSION, PROCESSED, RAW, action_log


def main() -> None:
    log = action_log("8 validation")
    master = pd.read_csv(PROCESSED / "ontario_fire_spread_master_v1.csv", dtype={"fire_id": str}, low_memory=False)
    fires = pd.read_csv(PROCESSED / "ontario_fire_spread_fires_v1.csv", dtype={"fire_id": str})
    hourly = pd.read_csv(PROCESSED / "ontario_fire_spread_weather_hourly_v1.csv.gz", dtype={"fire_id": str})
    daily = pd.read_csv(PROCESSED / "ontario_fire_spread_weather_daily_v1.csv", dtype={"fire_id": str})
    cells = pd.read_csv(PROCESSED / "ontario_fire_spread_arrival_cells_v1.csv.gz", dtype={"fire_id": str})
    log.info(f"START: validating master ({len(master):,} rows x {master.shape[1]} columns) and its supporting tables")
    checks: dict[str, bool] = {}

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks[name] = bool(passed)
        log.info(("PASS " if passed else "FAIL ") + name + (f" [{detail}]" if detail else ""))

    master = master.sort_values(["fire_id", "observation"])
    by_fire = master.groupby("fire_id")
    check("fire ids are unique in the fire table", fires.fire_id.is_unique)
    check("(fire, observation) keys are unique", not master.duplicated(["fire_id", "observation"]).any())
    check("every master fire is in the fire table", master.fire_id.isin(fires.fire_id).all())
    check("observations are numbered 1..n in time order", (by_fire.observation.apply(lambda s: list(s) == list(range(1, len(s) + 1))).all()) and (by_fire.time_utc.apply(lambda s: s.is_monotonic_increasing).all()))
    check("cumulative area never decreases", (by_fire.cumulative_area_ha.diff().dropna() >= -1e-6).all())
    step = master.cumulative_area_ha - by_fire.cumulative_area_ha.shift().fillna(0)
    check("new area equals the change in cumulative area", np.allclose(step, master.new_area_ha, atol=0.02))
    check("arrival cells match each fire's final cumulative cell count", (cells.groupby("fire_id").size().reindex(fires.fire_id[fires.detections > 0]).values == by_fire.cumulative_cells.max().reindex(fires.fire_id[fires.detections > 0]).values).all())
    check("an arrival cell appears once per fire", not cells.duplicated(["fire_id", "cell_x", "cell_y"]).any())
    check("hours between observations are positive", (master.hours_since_previous_observation.dropna() > 0).all())
    check("no observation is dated before the fire's detection window", (pd.to_datetime(master.time_utc) >= pd.to_datetime(master.start_date) - pd.Timedelta(days=3)).all())
    check("holdout and calibration fires do not share a year", set(master.fire_year[master.split == "calibration"]).isdisjoint(set(master.fire_year[master.split == "holdout"])))
    check("bearings and wind directions lie in 0-360", all(master[c].dropna().between(0, 360).all() for c in ("spread_bearing_deg", "head_bearing_deg", "wind_direction_deg", "downwind_bearing_deg")))
    check("hourly wind components reproduce the wind speed", np.allclose(np.hypot(hourly.wind_u_kmh, hourly.wind_v_kmh), hourly.wind_speed_kmh, atol=0.02))
    check("hourly weather is physically plausible", hourly.wind_speed_kmh.between(0, 120).all() and hourly.rh_pct.between(0, 100).all() and hourly.temp_c.between(-40, 45).all() and (hourly.precip_mm >= 0).all(), f"wind max {hourly.wind_speed_kmh.max():.0f} km/h, temp {hourly.temp_c.min():.0f} to {hourly.temp_c.max():.0f} C")
    check("fire-weather codes are in range", daily.ffmc.between(0, 101).all() and (daily[["dmc", "dc", "isi", "bui", "fwi"]] >= 0).all().all())
    check("vector-mean wind never exceeds mean wind speed", (np.hypot(master.wind_u_kmh, master.wind_v_kmh) <= master.wind_speed_mean_kmh + 0.01)[master.weather_hours > 0].all())
    check("every row has interval weather, fire-weather codes, fuel and terrain", (master.weather_hours > 0).all() and master.fwi.notna().all() and master.fuel_around_conifer_share.notna().all() and master.terrain_window_slope_mean_pct.notna().all(), f"missing: weather {(master.weather_hours == 0).sum()}, codes {master.fwi.isna().sum()}, fuel {master.fuel_around_conifer_share.isna().sum()}, terrain {master.terrain_window_slope_mean_pct.isna().sum()}")
    fuel_total = master[[f"fuel_around_{g}_share" for g in ("conifer", "mixedwood", "deciduous", "open", "non_fuel", "water")]].sum(axis=1) + master.fuel_around_unrecognised_code_share
    check("fuel shares add up to one", np.allclose(fuel_total, 1.0, atol=0.001))

    sanity: dict[str, object] = {}
    seen = fires[fires.detections > 0]
    sanity["satellite_area_vs_agency_perimeter"] = {
        "fires": len(seen),
        "rank_correlation": round(float(seen.detected_share_of_perimeter.mul(seen.perimeter_area_ha).corr(seen.perimeter_area_ha, method="spearman")), 3),
        "median_detected_share": round(float(seen.detected_share_of_perimeter.median()), 2),
        "share_for_fires_of_1000_ha_or_more": round(float(seen[seen.reported_size_ha >= 1000].detected_share_of_perimeter.median()), 2),
        "share_for_fires_under_200_ha": round(float(seen[seen.reported_size_ha < 200].detected_share_of_perimeter.median()), 2),
    }
    sanity["first_detection_days_after_reported_start"] = {str(q): float(seen.first_detection_days_after_start.quantile(q)) for q in (0.1, 0.5, 0.9)}

    reported = pd.DataFrame([f["properties"] for f in json.loads((RAW / "fire_disturbance/fire_disturbance_area.geojson").read_text(encoding="utf-8"))["features"]])
    reported["fire_id"] = reported.FIRE_YEAR.astype(str) + "_" + reported.FIRE_DISTURBANCE_AREA_IDENT
    compare = fires[["fire_id", "start_date"]].merge(reported[["fire_id", "FIRE_WEATHER_INDEX"]], on="fire_id").merge(daily[["fire_id", "date", "fwi"]], left_on=["fire_id", "start_date"], right_on=["fire_id", "date"])
    compare = compare[compare.FIRE_WEATHER_INDEX > 0]
    sanity["computed_fwi_vs_agency_recorded_fwi_on_start_date"] = {
        "fires": len(compare),
        "pearson_r": round(float(compare.fwi.corr(compare.FIRE_WEATHER_INDEX)), 3),
        "median_computed": round(float(compare.fwi.median()), 1),
        "median_recorded": round(float(compare.FIRE_WEATHER_INDEX.median()), 1),
    }

    growth = master[(master.new_cells > 0) & (master.growth_reference == "earlier_detections")]
    aligned = growth[growth.head_vs_wind_angle_deg.notna()]
    by_speed = aligned.groupby(pd.cut(aligned.wind_speed_mean_kmh, [0, 10, 15, 20, 100], right=False), observed=True).head_vs_wind_angle_deg
    sanity["head_within_45_deg_of_downwind"] = {
        "all_growth_steps": round(float((aligned.head_vs_wind_angle_deg <= 45).mean()), 3),
        "if_unrelated": 0.25,
        "by_mean_wind_kmh": {str(k): round(float(v), 3) for k, v in by_speed.apply(lambda s: (s <= 45).mean()).items()},
        "steps": len(aligned),
    }
    later = master[master.observation > 1]
    by_isi = later.groupby(pd.cut(later.isi, [0, 2, 4, 6, 8, 12, 100], right=False), observed=True)
    sanity["growth_by_initial_spread_index"] = {
        str(k): {"observations": int(v.shape[0]), "share_with_new_area": round(float((v.new_cells > 0).mean()), 3), "median_new_area_ha_when_growing": round(float(v.new_area_ha[v.new_cells > 0].median()), 1)}
        for k, v in by_isi
    }
    rate = growth.spread_rate_m_per_h.dropna()
    sanity["spread_rate_m_per_h"] = {str(q): round(float(rate.quantile(q)), 1) for q in (0.5, 0.9, 0.99)}
    interval = master.hours_since_previous_observation.dropna()
    sanity["hours_between_observations"] = {"share_6h_or_less": round(float((interval <= 6).mean()), 3), "share_12h_or_less": round(float((interval <= 12.5).mean()), 3), "share_over_24h": round(float((interval > 24).mean()), 3), "median": round(float(interval.median()), 1)}

    for name, value in sanity.items():
        log.info(f"SANITY {name}: {json.dumps(value)}")
    failed = [name for name, passed in checks.items() if not passed]
    report = {"dataset_version": DATASET_VERSION, "created_at_utc": datetime.now(UTC).isoformat(), "checks_passed": len(checks) - len(failed), "checks_failed": failed, "checks": checks, "sanity": sanity}
    (PROCESSED / "ontario_fire_spread_v1_validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(f"DONE: {len(checks) - len(failed)} of {len(checks)} structural checks passed" + (f"; FAILED: {failed}" if failed else ""))


if __name__ == "__main__":
    main()
