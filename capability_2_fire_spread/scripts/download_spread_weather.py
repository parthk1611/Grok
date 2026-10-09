#!/usr/bin/env python3
"""Download hourly reanalysis weather at each fire.

Step 5a of the fire-spread build.  For every fire the satellites saw, this
requests hourly 2 m temperature, dew point and relative humidity, precipitation,
and 10 m wind speed and direction at the fire's centroid, from the start of the
fire-weather season through the fire's last detection.

Source: the Open-Meteo historical archive, `era5_seamless` model, which serves
the Copernicus ERA5 reanalysis family without an account: ERA5-Land (0.1°) for
temperature and humidity, ERA5 (0.25°) where ERA5-Land has no value.  The team
plan names ERA5-Land from the Copernicus Climate Data Store; that needs a
personal API key, so this is the same reanalysis by a keyless route.  One JSON
response per fire is cached (ignored by git).
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from spread_common import INTERIM, RAW, USER_AGENT, action_log


API_URL = "https://archive-api.open-meteo.com/v1/archive"
MODEL = "era5_seamless"
VARIABLES = ["temperature_2m", "dew_point_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m", "wind_direction_10m"]
DESTINATION = RAW / "era5_openmeteo"
MANIFEST = DESTINATION / "manifest.json"
SEASON_START = (4, 1)  # fire-weather codes are started from defaults on this date
MIN_SPINUP_DAYS = 30


def window(fire) -> tuple[date, date]:
    start = date.fromisoformat(fire.start_date)
    first_seen = datetime.strptime(fire.first_detection_utc, "%Y-%m-%d %H:%M").date()
    begin = min(date(start.year, *SEASON_START), min(start, first_seen) - timedelta(days=MIN_SPINUP_DAYS))
    end = datetime.strptime(fire.last_detection_utc, "%Y-%m-%d %H:%M").date() + timedelta(days=1)
    return begin, end


def fetch(fire, force: bool) -> dict[str, object]:
    path = DESTINATION / "fires" / f"{fire.fire_id}.json.gz"
    begin, end = window(fire)
    entry = {"fire_id": fire.fire_id, "latitude": fire.centroid_latitude, "longitude": fire.centroid_longitude, "start_date": begin.isoformat(), "end_date": end.isoformat()}
    if path.exists() and not force:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            payload = json.load(source)
    else:
        url = f"{API_URL}?" + urlencode(
            {
                "latitude": fire.centroid_latitude,
                "longitude": fire.centroid_longitude,
                "start_date": begin.isoformat(),
                "end_date": end.isoformat(),
                "hourly": ",".join(VARIABLES),
                "models": MODEL,
                "wind_speed_unit": "kmh",
                "timezone": "UTC",
            }
        )
        for attempt in range(8):
            try:
                with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=180) as response:  # noqa: S310 - fixed public Open-Meteo URL
                    payload = json.load(response)
                break
            except HTTPError as error:
                if error.code != 429 or attempt == 7:
                    raise
                time.sleep(65)  # the free service limits requests per minute and per hour
            except OSError:
                if attempt == 7:
                    raise
                time.sleep(5 * (attempt + 1))
        with gzip.open(path, "wt", encoding="utf-8") as destination:
            json.dump(payload, destination)
        time.sleep(0.6)
    hours = len(payload["hourly"]["time"])
    entry.update(
        {
            "grid_latitude": payload["latitude"],
            "grid_longitude": payload["longitude"],
            "grid_elevation_m": payload.get("elevation"),
            "hours": hours,
            "hours_missing_wind": sum(value is None for value in payload["hourly"]["wind_speed_10m"]),
        }
    )
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, help="Only the first N fires, for a trial run.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    log = action_log("5a download weather")
    fires = pd.read_csv(INTERIM / "spread_fires_with_progression.csv", dtype={"fire_id": str})
    fires = fires[fires.detections > 0]
    if args.limit:
        fires = fires.head(args.limit)
    (DESTINATION / "fires").mkdir(parents=True, exist_ok=True)
    log.info(f"START: hourly {MODEL} weather from Open-Meteo for {len(fires)} fires with satellite detections; variables {', '.join(VARIABLES)}")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        entries = list(pool.map(lambda fire: fetch(fire, args.force), fires.itertuples()))
    manifest = {
        "source_name": "ERA5 / ERA5-Land reanalysis via the Open-Meteo historical weather API",
        "source_url": API_URL,
        "model": MODEL,
        "variables": VARIABLES,
        "units": {"temperature_2m": "°C", "dew_point_2m": "°C", "relative_humidity_2m": "%", "precipitation": "mm in the preceding hour", "wind_speed_10m": "km/h", "wind_direction_10m": "degrees the wind blows from"},
        "license": "Open-Meteo data CC BY 4.0; contains modified Copernicus Climate Change Service information (ERA5, ERA5-Land)",
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "availability_semantics": {"use": "retrospective hindcast only", "operational_eligible": False},
        "fires": entries,
    }
    if not args.limit:
        MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    total_hours = sum(entry["hours"] for entry in entries)
    log.info(f"DONE: {len(entries)} fires, {total_hours:,} hourly records, {sum(entry['hours_missing_wind'] for entry in entries):,} hours without wind")


if __name__ == "__main__":
    main()
