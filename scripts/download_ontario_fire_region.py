#!/usr/bin/env python3
"""Download official Ontario Fire Region response-plan sectors as GeoJSON."""

from __future__ import annotations

import hashlib
import json
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from download_cpa_boundary import parse_dbf, parse_polygons, rings_to_multipolygon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESTINATION = PROJECT_ROOT / "data/raw/ontario_fire_region"
OUTPUT = DESTINATION / "fire_response_plan_sectors.geojson"
MANIFEST = DESTINATION / "fire_response_plan_sectors_manifest.json"
ARCHIVE = DESTINATION / "FIRERESP.zip"
ARCHIVE_URL = "https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Packages/FIRERESP.zip"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "ai-capstone/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        archive_contents = response.read()
    ARCHIVE.write_bytes(archive_contents)

    with zipfile.ZipFile(ARCHIVE) as archive:
        dbf_name = next(name for name in archive.namelist() if name.lower().endswith(".dbf"))
        shp_name = next(name for name in archive.namelist() if name.lower().endswith(".shp"))
        attributes = parse_dbf(archive.read(dbf_name))
        shapes = parse_polygons(archive.read(shp_name))
    if len(attributes) != len(shapes):
        raise ValueError("Fire response geometry and attribute record counts differ.")

    features = []
    for row, rings in zip(attributes, shapes, strict=True):
        # OFR denotes the Outside Fire Region sector. All other official
        # response sectors jointly define the operational Ontario Fire Region.
        if row.get("RES_SECTOR") == "OFR":
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "RESPONSE_SECTOR": row["RES_SECTOR"],
                    "BASE_RESPONSE": row["BASE_RESP"],
                    "GEOMETRY_UPDATE_DATETIME": row["GEO_UPD_DT"],
                    "EFFECTIVE_DATETIME": row["EFF_DATE"],
                },
                "geometry": {"type": "MultiPolygon", "coordinates": rings_to_multipolygon(rings)},
            }
        )
    if not features:
        raise ValueError("Official Ontario source contains no in-region response sectors.")

    boundary = {"type": "FeatureCollection", "features": features}
    boundary_contents = (json.dumps(boundary, separators=(",", ":")) + "\n").encode()
    OUTPUT.write_bytes(boundary_contents)
    MANIFEST.write_text(
        json.dumps(
            {
                "source_name": "Ontario Fire Response Plan Area",
                "source_url": ARCHIVE_URL,
                "selection": "RES_SECTOR != 'OFR' (Outside Fire Region)",
                "license": "Open Government Licence – Ontario",
                "downloaded_at_utc": datetime.now(UTC).isoformat(),
                "archive_filename": ARCHIVE.name,
                "archive_sha256": sha256(archive_contents),
                "geojson_sha256": sha256(boundary_contents),
                "feature_count": len(features),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(features)} Ontario Fire Region sector features: {OUTPUT}")
    print(f"Wrote manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
