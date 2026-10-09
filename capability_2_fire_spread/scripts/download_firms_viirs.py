#!/usr/bin/env python3
"""Download NASA FIRMS VIIRS 375 m active-fire detections for Ontario.

Step 3 of the fire-spread build.  FIRMS publishes one archive file per country
per year for each VIIRS satellite.  Each Canada file is streamed and only the
rows inside the Ontario box are written, so the national files are never stored.
The manifest records each source file's size and checksum, and how many rows
were kept.

Detections are validation data: they show where a fire was at a satellite
pass.  They must never be used as ignition predictors.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from spread_common import RAW, USER_AGENT, action_log


DESTINATION = RAW / "firms"
MANIFEST = DESTINATION / "manifest.json"
BASE_URL = "https://firms.modaps.eosdis.nasa.gov/data/country"
# FIRMS names: viirs-snpp is Suomi NPP (from January 2012); viirs-jpss1 is NOAA-20 (from 2018).
SENSORS = {"viirs-snpp": 2012, "viirs-jpss1": 2018}
LAST_YEAR = 2025
# A box around the Ontario Fire Region with a margin for fires on its edge.
ONTARIO_BOX = {"west": -96.0, "east": -74.0, "south": 41.0, "north": 57.5}
REQUIRED_FIELDS = {"latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence", "frp", "daynight", "type"}


def download_year(sensor: str, year: int) -> dict[str, object] | None:
    url = f"{BASE_URL}/{sensor}/{year}/{sensor}_{year}_Canada.csv"
    path = DESTINATION / f"{sensor}_{year}_ontario.csv.gz"
    digest = hashlib.sha256()
    source_bytes = source_rows = kept = 0
    try:
        response = urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=300)  # noqa: S310 - fixed public NASA URL
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    with tempfile.NamedTemporaryFile("wb", dir=DESTINATION, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with response, gzip.open(temporary_path, "wt", encoding="utf-8", newline="") as destination:

            class Tee(io.RawIOBase):
                def readable(self) -> bool:
                    return True

                def readinto(self, buffer) -> int:
                    nonlocal source_bytes
                    chunk = response.read(len(buffer))
                    digest.update(chunk)
                    source_bytes += len(chunk)
                    buffer[: len(chunk)] = chunk
                    return len(chunk)

            reader = csv.DictReader(io.TextIOWrapper(io.BufferedReader(Tee(), 1024 * 1024), encoding="utf-8", newline=""))
            if reader.fieldnames is None or not REQUIRED_FIELDS.issubset(reader.fieldnames):
                raise ValueError(f"Unexpected FIRMS schema in {url}: {reader.fieldnames}")
            writer = csv.DictWriter(destination, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                source_rows += 1
                longitude, latitude = float(row["longitude"]), float(row["latitude"])
                if ONTARIO_BOX["west"] <= longitude <= ONTARIO_BOX["east"] and ONTARIO_BOX["south"] <= latitude <= ONTARIO_BOX["north"]:
                    writer.writerow(row)
                    kept += 1
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {
        "sensor": sensor,
        "year": year,
        "source_url": url,
        "source_bytes": source_bytes,
        "source_sha256": digest.hexdigest(),
        "source_rows_canada": source_rows,
        "rows_kept_ontario_box": kept,
        "filename": path.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    log = action_log("3 download FIRMS")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    jobs = [(sensor, year) for sensor, first in SENSORS.items() for year in range(first, LAST_YEAR + 1)]
    log.info(f"START: FIRMS VIIRS yearly Canada files, {len(jobs)} sensor-years, kept rows inside box {ONTARIO_BOX}")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda job: download_year(*job), jobs))
    entries = []
    for (sensor, year), entry in zip(jobs, results):
        if entry is None:
            log.info(f"{sensor} {year}: no yearly file published yet")
            continue
        entries.append(entry)
        log.info(f"{sensor} {year}: read {entry['source_rows_canada']:,} Canada detections ({entry['source_bytes'] / 1_048_576:.1f} MB), kept {entry['rows_kept_ontario_box']:,} in the Ontario box")
    manifest = {
        "source_name": "NASA FIRMS VIIRS 375 m active fire detections, country yearly archive",
        "publisher": "NASA LANCE / FIRMS",
        "source_page": "https://firms.modaps.eosdis.nasa.gov/country/",
        "citation": "NASA FIRMS, VIIRS 375 m Active Fire product (VNP14IMGT / VJ114IMGT), doi:10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002",
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "spatial_filter": ONTARIO_BOX,
        "use": "validation only; never an ignition predictor",
        "files": entries,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log.info(f"DONE: {len(entries)} files, {sum(entry['rows_kept_ontario_box'] for entry in entries):,} Ontario detections, {sum(entry['source_bytes'] for entry in entries) / 1_048_576:.0f} MB read from NASA")


if __name__ == "__main__":
    main()
