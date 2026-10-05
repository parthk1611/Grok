#!/usr/bin/env python3
"""Download the official Ontario Crown Protection Area boundary as GeoJSON."""

from __future__ import annotations

import hashlib
import json
import urllib.request
import struct
import zipfile
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESTINATION = PROJECT_ROOT / "data/raw/ontario_fire_management"
OUTPUT = DESTINATION / "crown_protection_area.geojson"
MANIFEST = DESTINATION / "crown_protection_area_manifest.json"
ARCHIVE = DESTINATION / "FIREMAA.zip"
ARCHIVE_URL = "https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Packages/FIREMAA.zip"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_dbf(contents: bytes) -> list[dict[str, str]]:
    """Read the attribute records needed from a dBase III shapefile table."""
    record_count, header_length, record_length = struct.unpack_from("<xxxxIHH", contents)
    fields: list[tuple[str, int]] = []
    offset = 32
    while contents[offset] != 0x0D:
        name = contents[offset : offset + 11].split(b"\x00")[0].decode("ascii")
        fields.append((name, contents[offset + 16]))
        offset += 32

    records: list[dict[str, str]] = []
    for record_index in range(record_count):
        start = header_length + record_index * record_length
        record = contents[start : start + record_length]
        if record[:1] == b"*":
            records.append({})
            continue
        position = 1
        values: dict[str, str] = {}
        for name, length in fields:
            values[name] = record[position : position + length].decode("latin-1").strip()
            position += length
        records.append(values)
    return records


def parse_polygons(contents: bytes) -> list[list[list[list[float]]]]:
    """Read Polygon records from a .shp file, preserving record order."""
    polygons: list[list[list[list[float]]]] = []
    offset = 100
    while offset < len(contents):
        _, content_words = struct.unpack_from(">II", contents, offset)
        record = contents[offset + 8 : offset + 8 + content_words * 2]
        offset += 8 + content_words * 2
        shape_type = struct.unpack_from("<I", record)[0]
        if shape_type == 0:
            polygons.append([])
            continue
        if shape_type not in {5, 15, 25}:
            raise ValueError(f"Unexpected shapefile geometry type: {shape_type}")
        part_count, point_count = struct.unpack_from("<II", record, 36)
        parts = list(struct.unpack_from(f"<{part_count}I", record, 44))
        point_offset = 44 + 4 * part_count
        points = [
            list(struct.unpack_from("<dd", record, point_offset + point_index * 16))
            for point_index in range(point_count)
        ]
        polygons.append([points[start : parts[index + 1] if index + 1 < part_count else point_count] for index, start in enumerate(parts)])
    return polygons


def point_in_ring(point: list[float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for index, (x1, y1) in enumerate(ring):
        x2, y2 = ring[index - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def ring_area(ring: list[list[float]]) -> float:
    return abs(
        sum(
            ring[index - 1][0] * point[1] - point[0] * ring[index - 1][1]
            for index, point in enumerate(ring)
        )
        / 2
    )


def rings_to_multipolygon(rings: list[list[list[float]]]) -> list[list[list[list[float]]]]:
    """Reconstruct polygon holes from containment, independent of ring winding."""
    if not rings:
        return []
    areas = [ring_area(ring) for ring in rings]
    parents: list[int | None] = []
    for index, ring in enumerate(rings):
        containing = [
            candidate
            for candidate, outer in enumerate(rings)
            if areas[candidate] > areas[index] and point_in_ring(ring[0], outer)
        ]
        parents.append(min(containing, key=lambda candidate: areas[candidate]) if containing else None)

    def depth(index: int) -> int:
        result = 0
        seen = {index}
        while parents[index] is not None:
            index = parents[index]
            if index in seen:
                raise ValueError("Boundary geometry has cyclic ring containment.")
            seen.add(index)
            result += 1
        return result

    multipolygon: list[list[list[list[float]]]] = []
    outer_to_polygon: dict[int, list[list[list[float]]]] = {}
    for index, ring in enumerate(rings):
        if depth(index) % 2 == 0:
            polygon = [ring]
            multipolygon.append(polygon)
            outer_to_polygon[index] = polygon
    for index, ring in enumerate(rings):
        if depth(index) % 2 == 1:
            parent = parents[index]
            if parent is not None:
                outer_to_polygon[parent].append(ring)
    return multipolygon


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
        raise ValueError("Boundary shapefile geometry and attribute record counts differ.")

    features = []
    for attributes_row, rings in zip(attributes, shapes, strict=True):
        if attributes_row.get("PROT_CODE") != "CPA":
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "AGREEMENT_NAME": attributes_row["AGREE_NAME"],
                    "PROTECTION_TYPE_CODE": attributes_row["PROT_CODE"],
                    "FIRE_MANAGEMENT_HQ": attributes_row["MGMT_HQ"],
                    "GEOMETRY_UPDATE_DATETIME": attributes_row["GEO_UPT_DT"],
                },
                "geometry": {"type": "MultiPolygon", "coordinates": rings_to_multipolygon(rings)},
            }
        )
    if not features:
        raise ValueError("Official Ontario shapefile contains no Crown Protection Area features.")
    boundary = {"type": "FeatureCollection", "features": features}
    boundary_contents = (json.dumps(boundary, separators=(",", ":")) + "\n").encode()
    OUTPUT.write_bytes(boundary_contents)
    MANIFEST.write_text(
        json.dumps(
            {
                "source_name": "Ontario Fire Management Agreement Area",
                "source_url": ARCHIVE_URL,
                "selection": "PROT_CODE = 'CPA'",
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
    print(f"Wrote {len(features)} CPA features: {OUTPUT}")
    print(f"Wrote manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
