#!/usr/bin/env python3
"""Download official Ontario Fire Region response-plan sectors as GeoJSON."""

from __future__ import annotations

import hashlib
import json
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from download_cpa_boundary import parse_dbf, parse_polygons, rings_to_multipolygon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESTINATION = PROJECT_ROOT / "data/raw/ontario_fire_region"
OUTPUT = DESTINATION / "fire_response_plan_sectors.geojson"
MANIFEST = DESTINATION / "fire_response_plan_sectors_manifest.json"
ARCHIVE = DESTINATION / "FIRERESP.zip"
ARCHIVE_URL = "https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Packages/FIRERESP.zip"


GDB_LAYER = "FIRE_RESPONSE_SECTOR"
SOURCE_REVISION_NOTE = (
    "Upstream FIRERESP.zip changed from an Esri Shapefile (archive sha256 1368d14e5faa3503ce4c8d2de20da553e9dffcc74e1309e24378e711ffa8f4a9, "
    "fields RES_SECTOR/BASE_RESP/GEO_UPD_DT/EFF_DATE) to a File Geodatabase "
    "(Non_Sensitive.gdb, layer FIRE_RESPONSE_SECTOR, fields RESPONSE_SECTOR/BASE_RESPONSIBLE/"
    "GEOMETRY_UPDATE_DATETIME/EFFECTIVE_DATETIME)."
)

PREVIOUS_REVISIONS = [
    {
        "archive_format": "shapefile",
        "archive_sha256": "1368d14e5faa3503ce4c8d2de20da553e9dffcc74e1309e24378e711ffa8f4a9",
        "geojson_sha256": "37efcdb2054ba98d884938a90fdae6309bc4f0f8d2c7f6fab4da0d12a9522263",
        "feature_count": 15,
        "downloaded_at_utc": "2026-10-02T17:52:46.890282+00:00",
        "status": "superseded upstream; archive no longer available at source_url",
    }
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _format_datetime(value) -> str:
    """Render a geodatabase datetime the way a text field would carry it (ISO, no zone)."""
    text = str(value)
    return "" if text in {"NaT", "None", "nan"} else text.replace("T", " ").split(".")[0]


def read_geodatabase_sectors(archive_path: Path) -> list[tuple[dict[str, str], list]]:
    """Read (properties, MultiPolygon coordinates) from the geodatabase layer inside the zip.

    Returns properties keyed with the legacy shapefile names (RES_SECTOR, BASE_RESP,
    GEO_UPD_DT, EFF_DATE) so the selection and output code is unchanged.
    """
    import pyogrio.raw  # lazy import: only needed for the geodatabase source format
    from shapely import from_wkb
    from shapely.geometry import MultiPolygon, mapping

    with zipfile.ZipFile(archive_path) as archive:
        gdb_dirs = sorted({str(PurePosixPath(n).parts[0]) for n in archive.namelist() if n.split("/")[0].lower().endswith(".gdb")})
    if len(gdb_dirs) != 1:
        raise ValueError(f"Expected exactly one .gdb directory in archive, found {gdb_dirs}")
    meta, _, geometries, field_values = pyogrio.raw.read(f"/vsizip/{archive_path}/{gdb_dirs[0]}", layer=GDB_LAYER)
    if str(meta["crs"]) != "EPSG:4269":
        raise ValueError(f"Unexpected CRS {meta['crs']}; the grid build treats coordinates as NAD83 lon/lat.")
    columns = dict(zip(list(meta["fields"]), field_values, strict=True))
    result = []
    for index, wkb in enumerate(geometries):
        geometry = from_wkb(wkb)
        if geometry.geom_type == "Polygon":
            geometry = MultiPolygon([geometry])
        if geometry.geom_type != "MultiPolygon":
            raise ValueError(f"Unexpected geometry type {geometry.geom_type}")
        coordinates = [[[list(point) for point in ring] for ring in polygon] for polygon in mapping(geometry)["coordinates"]]
        row = {
            "RES_SECTOR": str(columns["RESPONSE_SECTOR"][index]).strip(),
            "BASE_RESP": str(columns["BASE_RESPONSIBLE"][index]).strip(),
            "GEO_UPD_DT": _format_datetime(columns["GEOMETRY_UPDATE_DATETIME"][index]),
            "EFF_DATE": _format_datetime(columns["EFFECTIVE_DATETIME"][index]),
        }
        result.append((row, coordinates))
    return result


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "ai-capstone/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        archive_contents = response.read()
    ARCHIVE.write_bytes(archive_contents)

    with zipfile.ZipFile(ARCHIVE) as archive:
        names = archive.namelist()
        legacy = any(name.lower().endswith(".shp") for name in names)
        if legacy:
            dbf_name = next(name for name in names if name.lower().endswith(".dbf"))
            shp_name = next(name for name in names if name.lower().endswith(".shp"))
            attributes = parse_dbf(archive.read(dbf_name))
            shapes = [rings_to_multipolygon(rings) for rings in parse_polygons(archive.read(shp_name))]
    if legacy:
        if len(attributes) != len(shapes):
            raise ValueError("Fire response geometry and attribute record counts differ.")
        records = list(zip(attributes, shapes, strict=True))
    else:
        records = read_geodatabase_sectors(ARCHIVE)

    features = []
    for row, coordinates in records:
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
                "geometry": {"type": "MultiPolygon", "coordinates": coordinates},
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
                "archive_format": "file_geodatabase" if not legacy else "shapefile",
                **(
                    {
                        "gdb_layer": GDB_LAYER,
                        "source_revision_note": SOURCE_REVISION_NOTE,
                        "previous_revisions": PREVIOUS_REVISIONS,
                        "grid_equivalence": "10 km grid rebuilt from this revision is byte-identical to the tracked grid (sha256 b22741d83b64c2b78c19fbf182848566644a23fad4aeef3301ff25ef7c6c3f35)",
                    }
                    if not legacy
                    else {}
                ),
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
