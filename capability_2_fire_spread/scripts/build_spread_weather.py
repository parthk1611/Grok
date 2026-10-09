#!/usr/bin/env python3
"""Turn the cached hourly reanalysis into wind components and fire-weather codes.

Step 5b of the fire-spread build.  For each fire this writes two tables over
the fire's active window (the day before its first satellite detection to the
day after its last):

- hourly: temperature, humidity, rain, wind speed and direction, and the wind
  as east-west (u) and north-south (v) components;
- daily: local-noon weather, 24-hour rain, and FFMC, DMC, DC, ISI, BUI and FWI.

The fire-weather codes are run in date order from the standard start-up values
on the first day of each fire's weather record, so by the time a fire is active
they reflect the season so far.  Needs pandas and numpy.
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from fire_weather_index import STARTUP, daily_codes, fine_fuel_moisture
from spread_common import DATASET_VERSION, INTERIM, RAW, action_log


CACHE = RAW / "era5_openmeteo/fires"
NOON_HOUR = 12
RENAME = {
    "temperature_2m": "temp_c",
    "dew_point_2m": "dew_point_c",
    "relative_humidity_2m": "rh_pct",
    "precipitation": "precip_mm",
    "wind_speed_10m": "wind_speed_kmh",
    "wind_direction_10m": "wind_direction_deg",
}


def wind_components(speed_kmh, direction_from_deg):
    """East-west (u) and north-south (v) components of a wind blowing *from* a bearing."""
    radians = np.radians(direction_from_deg)
    return -speed_kmh * np.sin(radians), -speed_kmh * np.cos(radians)


def load_hourly(fire_id: str, utc_offset_hours: int) -> pd.DataFrame:
    with gzip.open(CACHE / f"{fire_id}.json.gz", "rt", encoding="utf-8") as source:
        payload = json.load(source)
    hourly = pd.DataFrame(payload["hourly"]).rename(columns=RENAME)
    hourly["time_utc"] = pd.to_datetime(hourly.pop("time"))
    hourly["time_local"] = hourly.time_utc + pd.Timedelta(hours=utc_offset_hours)
    hourly["wind_u_kmh"], hourly["wind_v_kmh"] = wind_components(hourly.wind_speed_kmh, hourly.wind_direction_deg)
    return hourly


def daily_fire_weather(hourly: pd.DataFrame) -> pd.DataFrame:
    """Noon weather, noon-to-noon rain, and the six codes for every complete day."""
    frame = hourly.set_index("time_local")
    # precipitation is the total of the preceding hour, so a trailing 24-hour sum at noon covers 12:01 yesterday to noon today
    rain = frame.precip_mm.rolling(24, min_periods=24).sum()
    noon = frame[frame.index.hour == NOON_HOUR].assign(rain_24h_mm=rain[frame.index.hour == NOON_HOUR])
    noon = noon.dropna(subset=["temp_c", "rh_pct", "wind_speed_kmh", "rain_24h_mm"])
    previous = dict(STARTUP)
    rows = []
    for day_number, (moment, values) in enumerate(noon.iterrows(), start=1):
        codes = daily_codes(values.temp_c, values.rh_pct, values.wind_speed_kmh, values.rain_24h_mm, moment.month, previous)
        rows.append(
            {
                "date": moment.date().isoformat(),
                "noon_temp_c": values.temp_c,
                "noon_rh_pct": values.rh_pct,
                "noon_wind_kmh": values.wind_speed_kmh,
                "noon_wind_direction_deg": values.wind_direction_deg,
                "rain_24h_mm": values.rain_24h_mm,
                **codes,
                "fine_fuel_moisture_pct": fine_fuel_moisture(codes["ffmc"]),
                "days_since_startup": day_number,
            }
        )
        previous = codes
    return pd.DataFrame(rows)


def build(args: argparse.Namespace) -> dict[str, object]:
    log = action_log("5b fire weather")
    fires = pd.read_csv(INTERIM / "spread_fires_with_progression.csv", dtype={"fire_id": str})
    fires = fires[fires.detections > 0]
    log.info(f"START: fire-weather build for {len(fires)} fires; codes started at FFMC {STARTUP['ffmc']:.0f}, DMC {STARTUP['dmc']:.0f}, DC {STARTUP['dc']:.0f}; noon is {NOON_HOUR}:00 local standard time")
    hourly_parts, daily_parts = [], []
    missing_cache = short_spinup = hours_with_gaps = 0
    for fire in fires.itertuples():
        if not (CACHE / f"{fire.fire_id}.json.gz").exists():
            missing_cache += 1
            continue
        hourly = load_hourly(fire.fire_id, int(fire.utc_offset_hours))
        hours_with_gaps += int(hourly[list(RENAME.values())].isna().any(axis=1).sum())
        daily = daily_fire_weather(hourly)
        first = pd.Timestamp(fire.first_detection_utc).normalize() - pd.Timedelta(days=1)
        last = pd.Timestamp(fire.last_detection_utc).normalize() + pd.Timedelta(days=2)
        active = hourly[(hourly.time_utc >= first) & (hourly.time_utc < last)].copy()
        active.insert(0, "fire_id", fire.fire_id)
        hourly_parts.append(active)
        daily = daily[(daily.date >= first.date().isoformat()) & (daily.date <= last.date().isoformat())].copy()
        daily.insert(0, "fire_id", fire.fire_id)
        short_spinup += int(len(daily) > 0 and daily.days_since_startup.min() < args.min_spinup_days)
        daily_parts.append(daily)
    hourly_all = pd.concat(hourly_parts, ignore_index=True)
    daily_all = pd.concat(daily_parts, ignore_index=True)
    for column in ("time_utc", "time_local"):
        hourly_all[column] = hourly_all[column].dt.strftime("%Y-%m-%d %H:%M")
    hourly_columns = ["fire_id", "time_utc", "time_local", "temp_c", "dew_point_c", "rh_pct", "precip_mm", "wind_speed_kmh", "wind_direction_deg", "wind_u_kmh", "wind_v_kmh"]
    hourly_all[hourly_columns].round(2).to_csv(INTERIM / "spread_weather_hourly.csv.gz", index=False)
    daily_all.round(2).to_csv(INTERIM / "spread_weather_daily.csv", index=False)

    report = {
        "dataset_version": DATASET_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "retrospective_hindcast_not_operational_forecast",
        "source": "ERA5 / ERA5-Land reanalysis via Open-Meteo (era5_seamless), at each fire's centroid",
        "fire_weather": {
            "equations": "Van Wagner (1987) Canadian Forest Fire Weather Index System, standard daily version",
            "startup_values": STARTUP,
            "startup_date": "first day of each fire's weather record (1 April, or 30 days before the fire if earlier)",
            "noon": f"{NOON_HOUR}:00 local standard time",
            "rain": "sum of the 24 hours ending at local noon",
            "overwintering": "not applied; every season starts from the standard values",
        },
        "counts": {
            "fires": int(hourly_all.fire_id.nunique()),
            "fires_without_cached_weather": missing_cache,
            "hourly_rows": len(hourly_all),
            "daily_rows": len(daily_all),
            "hourly_rows_with_a_missing_value": hours_with_gaps,
            "fires_with_under_min_spinup_days": short_spinup,
        },
        "ranges": {column: [round(float(daily_all[column].min()), 1), round(float(daily_all[column].median()), 1), round(float(daily_all[column].max()), 1)] for column in ("ffmc", "dmc", "dc", "isi", "bui", "fwi")},
        "outputs": {"hourly": "spread_weather_hourly.csv.gz", "daily": "spread_weather_daily.csv"},
    }
    (INTERIM / "spread_weather_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(
        f"DONE: {len(hourly_all):,} hourly rows and {len(daily_all):,} daily rows for {report['counts']['fires']} fires; "
        f"{hours_with_gaps} hourly rows with a missing value; {short_spinup} fires with under {args.min_spinup_days} days of code spin-up"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-spinup-days", type=int, default=21)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
