#!/usr/bin/env python3
"""Download a fuel-type window around each fire.

Step 6a of the fire-spread build.  The national FBP Fuel Types 2024 map (30 m,
EPSG:3978, Open Government Licence – Canada) is served by the Canadian Wildland
Fire Information System as a web coverage, so a box around each fire can be
requested directly instead of downloading the national raster.  Each window is
the fire's perimeter box plus a margin, sampled at 90 m by nearest neighbour on
the map's own pixel lattice, and saved as the GeoTIFF the server returns.

The map shows fuels as of 2024.  For an older fire it can show the burn scar
that fire left, so these windows describe today's fuel, not the fuel that burned.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from spread_common import INTERIM, RAW, USER_AGENT, action_log, load_perimeters_3978


SERVICE = "https://cwfis.cfs.nrcan.gc.ca/geoserver/public/wcs"
COVERAGE = "public:cffdrs_fbp_fuel_types"
DESTINATION = RAW / "fuel_terrain"
MANIFEST = DESTINATION / "fuel_windows_manifest.json"
# Lattice of the source raster: 30 m pixels, this west edge and north edge.
SOURCE_WEST, SOURCE_NORTH, SOURCE_PIXEL_M = -2_341_500.0, 2_851_423.977366649, 30.0
WINDOW_PIXEL_M = 90.0
MARGIN_M = 3_000.0


def window_for(bounds: tuple[float, float, float, float]) -> dict[str, float | int]:
    """The perimeter box plus margin, snapped outward to 90 m steps of the source lattice."""
    west, south, east, north = bounds
    left = SOURCE_WEST + math.floor((west - MARGIN_M - SOURCE_WEST) / WINDOW_PIXEL_M) * WINDOW_PIXEL_M
    top = SOURCE_NORTH - math.floor((SOURCE_NORTH - (north + MARGIN_M)) / WINDOW_PIXEL_M) * WINDOW_PIXEL_M
    columns = math.ceil((east + MARGIN_M - left) / WINDOW_PIXEL_M)
    rows = math.ceil((top - (south - MARGIN_M)) / WINDOW_PIXEL_M)
    return {"west": left, "north": top, "east": left + columns * WINDOW_PIXEL_M, "south": top - rows * WINDOW_PIXEL_M, "columns": columns, "rows": rows}


def fetch(fire_id: str, window: dict[str, float | int], force: bool) -> dict[str, object]:
    path = DESTINATION / "windows" / f"{fire_id}_fbp_fuel_90m.tif"
    if not path.exists() or force:
        url = f"{SERVICE}?" + urlencode(
            {
                "service": "WCS",
                "version": "1.0.0",
                "request": "GetCoverage",
                "coverage": COVERAGE,
                "crs": "EPSG:3978",
                "bbox": f"{window['west']},{window['south']},{window['east']},{window['north']}",
                "width": window["columns"],
                "height": window["rows"],
                "interpolation": "nearest neighbor",
                "format": "GeoTIFF",
            }
        )
        for attempt in range(5):
            try:
                with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=300) as response:  # noqa: S310 - fixed public CWFIS URL
                    contents = response.read()
                if contents[:2] not in (b"II", b"MM"):
                    raise OSError(f"server returned {contents[:120]!r} instead of a GeoTIFF")
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))
        path.write_bytes(contents)
    return {"fire_id": fire_id, "filename": path.name, "bytes": path.stat().st_size, "pixel_size_m": WINDOW_PIXEL_M, **window}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, help="Only the first N fires, for a trial run.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    log = action_log("6a download fuel windows")
    fires = pd.read_csv(INTERIM / "spread_fires_with_progression.csv", dtype={"fire_id": str})
    fires = fires[fires.detections > 0]
    if args.limit:
        fires = fires.head(args.limit)
    perimeters = load_perimeters_3978()
    (DESTINATION / "windows").mkdir(parents=True, exist_ok=True)
    windows = {fire_id: window_for(perimeters[fire_id].bounds) for fire_id in fires.fire_id}
    largest = max(windows.values(), key=lambda window: window["columns"] * window["rows"])
    log.info(f"START: FBP Fuel Types 2024 windows for {len(windows)} fires from {SERVICE}; {WINDOW_PIXEL_M:.0f} m pixels, {MARGIN_M:.0f} m margin; largest window {largest['columns']} x {largest['rows']} pixels")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        entries = list(pool.map(lambda item: fetch(item[0], item[1], args.force), windows.items()))
    manifest = {
        "source_name": "CFFDRS Fire Behaviour Prediction (FBP) Fuel Types 2024, 30 m",
        "publisher": "Natural Resources Canada, Canadian Forest Service",
        "source_page": "https://open.canada.ca/data/en/dataset/4e66dd2f-5cd0-42fd-b82c-a430044b31de",
        "service_url": SERVICE,
        "coverage": COVERAGE,
        "license": "Open Government Licence – Canada",
        "coordinate_system": "EPSG:3978",
        "resampling": "nearest neighbour from 30 m to 90 m on the source pixel lattice",
        "fuel_map_year": 2024,
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "windows": entries,
    }
    if not args.limit:
        MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    log.info(f"DONE: {len(entries)} fuel windows, {sum(entry['bytes'] for entry in entries) / 1_048_576:.1f} MB")


if __name__ == "__main__":
    main()
