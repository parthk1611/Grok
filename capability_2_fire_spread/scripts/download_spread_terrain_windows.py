#!/usr/bin/env python3
"""Read an elevation window around each fire from the national MRDEM.

Step 6c of the fire-spread build.  The Medium Resolution Digital Elevation
Model (MRDEM-30, CanElevation Series, Open Government Licence – Canada) is one
cloud-optimised GeoTIFF for all of Canada.  Only the blocks covering each
fire's window are read over HTTP.  Each window is the bare-earth terrain model
(DTM) resampled bilinearly from 30 m (EPSG:3979) onto exactly the same 90 m
EPSG:3978 grid as that fire's fuel window, so the two line up cell for cell.

Needs rasterio (see requirements-spread.txt).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT

from spread_common import RAW, action_log


SOURCE_URL = "https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif"
DESTINATION = RAW / "fuel_terrain"
MANIFEST = DESTINATION / "terrain_windows_manifest.json"
NODATA = -32767.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="Only the first N fires, for a trial run.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    log = action_log("6c download terrain windows")
    windows = json.loads((DESTINATION / "fuel_windows_manifest.json").read_text(encoding="utf-8"))["windows"]
    if args.limit:
        windows = windows[: args.limit]
    log.info(f"START: MRDEM-30 DTM windows for {len(windows)} fires from {SOURCE_URL}, warped to the fuel-window grids (EPSG:3978, 90 m, bilinear)")
    entries = []
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="3"):
        with rasterio.open(SOURCE_URL) as source:
            for number, window in enumerate(windows, start=1):
                path = DESTINATION / "windows" / f"{window['fire_id']}_mrdem_dtm_90m.tif"
                transform = from_origin(window["west"], window["north"], window["pixel_size_m"], window["pixel_size_m"])
                if not path.exists() or args.force:
                    with WarpedVRT(source, crs="EPSG:3978", transform=transform, width=window["columns"], height=window["rows"], resampling=Resampling.bilinear, nodata=NODATA) as warped:
                        elevation = warped.read(1).astype("float32")
                    profile = {"driver": "GTiff", "dtype": "float32", "count": 1, "crs": "EPSG:3978", "transform": transform, "width": window["columns"], "height": window["rows"], "nodata": NODATA, "compress": "deflate", "predictor": 3}
                    with rasterio.open(path, "w", **profile) as destination:
                        destination.write(elevation, 1)
                else:
                    with rasterio.open(path) as existing:
                        elevation = existing.read(1)
                valid = elevation != NODATA
                entries.append(
                    {
                        "fire_id": window["fire_id"],
                        "filename": path.name,
                        "columns": window["columns"],
                        "rows": window["rows"],
                        "valid_share": round(float(valid.mean()), 4),
                        "elevation_min_m": round(float(elevation[valid].min()), 1) if valid.any() else None,
                        "elevation_max_m": round(float(elevation[valid].max()), 1) if valid.any() else None,
                    }
                )
                if number % 100 == 0:
                    log.info(f"{number} of {len(windows)} terrain windows read")
    manifest = {
        "source_name": "Medium Resolution Digital Elevation Model (MRDEM-30), CanElevation Series, digital terrain model",
        "publisher": "Natural Resources Canada",
        "source_page": "https://open.canada.ca/data/en/dataset/18752265-bda3-498c-a4ba-9dfe68cb98da",
        "source_url": SOURCE_URL,
        "license": "Open Government Licence – Canada",
        "source_grid": "EPSG:3979, 30 m",
        "window_grid": "EPSG:3978, 90 m, identical to the fuel windows; bilinear resampling",
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "windows": entries,
    }
    if not args.limit:
        MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    incomplete = sum(entry["valid_share"] < 1 for entry in entries)
    log.info(f"DONE: {len(entries)} terrain windows; {incomplete} have some cells without elevation")


if __name__ == "__main__":
    main()
