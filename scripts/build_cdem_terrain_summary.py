#!/usr/bin/env python3
"""Summarize legacy CDEM terrain over the existing 10 km ignition grid.

The public CDEM elevation API accepts a LINESTRING and returns values at each
vertex.  Batching the nine equal-area sample points from each EPSG:3978 cell is
both reproducible and substantially gentler on the service than one request per
point.  This is deliberately a static-terrain extractor: it is not a source of
fuel, roads, vegetation, or disturbance information.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRID = PROJECT_ROOT / "data/interim/ontario_fire_region_10km_grid.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/cdem_terrain_10km_summary.csv"
DEFAULT_AUDIT = PROJECT_ROOT / "data/interim/cdem_terrain_10km_summary_audit.json"
DEFAULT_CACHE = PROJECT_ROOT / "data/raw/cdem/cdem_terrain_10km_3x3_cache.json"
ELEVATION_API = "https://geogratis.gc.ca/services/elevation/cdem/profile"
FEATURE_VERSION = "ignition-feature-v1.0.0"
CELL_SIZE_M = 10_000
SAMPLE_OFFSETS_M = (-CELL_SIZE_M / 3, 0.0, CELL_SIZE_M / 3)

# EPSG:3978 — copied from the label-grid builder so the terrain samples share
# exactly the existing grid's equal-area coordinate system.
SEMI_MAJOR_AXIS_M = 6_378_137.0
INVERSE_FLATTENING = 298.257222101
ECCENTRICITY = math.sqrt(2 / INVERSE_FLATTENING - 1 / INVERSE_FLATTENING**2)
CENTRAL_MERIDIAN_RAD = math.radians(-95)
LATITUDE_OF_ORIGIN_RAD = math.radians(49)
STANDARD_PARALLEL_1_RAD = math.radians(49)
STANDARD_PARALLEL_2_RAD = math.radians(77)


def m(phi: float) -> float:
    return math.cos(phi) / math.sqrt(1 - ECCENTRICITY**2 * math.sin(phi) ** 2)


def t(phi: float) -> float:
    sin_phi = math.sin(phi)
    return math.tan(math.pi / 4 - phi / 2) / (
        ((1 - ECCENTRICITY * sin_phi) / (1 + ECCENTRICITY * sin_phi)) ** (ECCENTRICITY / 2)
    )


N = math.log(m(STANDARD_PARALLEL_1_RAD) / m(STANDARD_PARALLEL_2_RAD)) / math.log(
    t(STANDARD_PARALLEL_1_RAD) / t(STANDARD_PARALLEL_2_RAD)
)
F = m(STANDARD_PARALLEL_1_RAD) / (N * t(STANDARD_PARALLEL_1_RAD) ** N)
RHO_0 = SEMI_MAJOR_AXIS_M * F * t(LATITUDE_OF_ORIGIN_RAD) ** N


def unproject(x: float, y: float) -> tuple[float, float]:
    """Convert Canada Atlas Lambert metres to longitude/latitude."""
    rho = math.copysign(math.hypot(x, RHO_0 - y), N)
    theta = math.atan2(x, RHO_0 - y)
    t_value = (rho / (SEMI_MAJOR_AXIS_M * F)) ** (1 / N)
    phi = math.pi / 2 - 2 * math.atan(t_value)
    for _ in range(12):
        sin_phi = math.sin(phi)
        next_phi = math.pi / 2 - 2 * math.atan(
            t_value * ((1 - ECCENTRICITY * sin_phi) / (1 + ECCENTRICITY * sin_phi)) ** (ECCENTRICITY / 2)
        )
        if abs(next_phi - phi) < 1e-12:
            phi = next_phi
            break
        phi = next_phi
    return math.degrees(CENTRAL_MERIDIAN_RAD + theta / N), math.degrees(phi)


def load_grid(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    required = {"grid_id", "grid_x_m", "grid_y_m", "centroid_longitude", "centroid_latitude"}
    if not rows or set(rows[0]) != required:
        raise ValueError(f"Grid must have exactly {sorted(required)}: {path}")
    identifiers = [row["grid_id"] for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Grid has duplicate grid_id values.")
    return sorted(rows, key=lambda row: row["grid_id"])


def sample_points(rows: list[dict[str, str]]) -> tuple[list[tuple[str, float, float, float, float]], dict[str, list[int]]]:
    """Return nine equal-area EPSG:3978 samples per cell and their indexes."""
    points: list[tuple[str, float, float, float, float]] = []
    indexes: dict[str, list[int]] = {}
    for row in rows:
        grid_id = row["grid_id"]
        x0, y0 = int(row["grid_x_m"]), int(row["grid_y_m"])
        indexes[grid_id] = []
        for y_offset in SAMPLE_OFFSETS_M:
            for x_offset in SAMPLE_OFFSETS_M:
                longitude, latitude = unproject(
                    x0 + CELL_SIZE_M / 2 + x_offset,
                    y0 + CELL_SIZE_M / 2 + y_offset,
                )
                indexes[grid_id].append(len(points))
                points.append((grid_id, x_offset, y_offset, longitude, latitude))
    return points, indexes


def fetch_batch(points: list[tuple[str, float, float, float, float]], retries: int = 4) -> list[float | None]:
    path = "LINESTRING(" + ",".join(f"{longitude:.6f} {latitude:.6f}" for *_, longitude, latitude in points) + ")"
    request = Request(
        f"{ELEVATION_API}?{urlencode({'path': path})}",
        headers={"User-Agent": "COMP-385-ignition-feature-builder/1.0 (+educational-research)"},
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=90) as response:  # noqa: S310 - fixed public NRCan endpoint
                payload = json.load(response)
            vertices = [item for item in payload if item.get("vertex")]
            if len(vertices) != len(points):
                raise ValueError(f"CDEM API returned {len(vertices)} vertices for {len(points)} requested points")
            values: list[float | None] = []
            for item in vertices:
                altitude = item.get("altitude")
                values.append(None if altitude is None else float(altitude))
            return values
        except Exception as error:  # pragma: no cover - network retry path
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"CDEM request failed after {retries} attempts: {last_error}")


def summarize(values: list[float | None]) -> dict[str, str]:
    valid = [value for value in values if value is not None]
    if not valid:
        return {
            "terrain_elevation_mean_m": "",
            "terrain_slope_deg": "",
            "terrain_aspect_sin": "",
            "terrain_aspect_cos": "",
            "terrain_ruggedness_m": "",
            "terrain_sample_count": "0",
            "terrain_coverage_fraction": "0.000000",
        }

    mean = sum(valid) / len(valid)
    ruggedness = math.sqrt(sum((value - mean) ** 2 for value in valid) / len(valid))
    # The samples are ordered south-to-north, then west-to-east. A slope needs
    # complete opposing sample columns/rows; report it as missing otherwise.
    slope_fields = {"terrain_slope_deg": "", "terrain_aspect_sin": "", "terrain_aspect_cos": ""}
    if len(valid) == 9:
        south, middle, north = values[0:3], values[3:6], values[6:9]
        west = (south[0], middle[0], north[0])
        east = (south[2], middle[2], north[2])
        dz_dx = (sum(east) / 3 - sum(west) / 3) / (2 * CELL_SIZE_M / 3)
        dz_dy = (sum(north) / 3 - sum(south) / 3) / (2 * CELL_SIZE_M / 3)
        slope_fields["terrain_slope_deg"] = f"{math.degrees(math.atan(math.hypot(dz_dx, dz_dy))):.6f}"
        # Down-slope bearing, represented continuously for modelling.
        aspect = math.atan2(-dz_dx, -dz_dy)
        slope_fields["terrain_aspect_sin"] = f"{math.sin(aspect):.6f}"
        slope_fields["terrain_aspect_cos"] = f"{math.cos(aspect):.6f}"
    return {
        "terrain_elevation_mean_m": f"{mean:.6f}",
        "terrain_ruggedness_m": f"{ruggedness:.6f}",
        "terrain_sample_count": str(len(valid)),
        "terrain_coverage_fraction": f"{len(valid) / len(values):.6f}",
        **slope_fields,
    }


def write_atomic_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        writer = csv.DictWriter(temporary, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary_path, path)


def write_atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        json.dump(payload, temporary, indent=2)
        temporary.write("\n")
    os.replace(temporary_path, path)


def load_cache(path: Path, point_count: int, batch_size: int) -> dict[str, list[float | None]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("point_count") != point_count or payload.get("batch_size") != batch_size:
        raise ValueError(f"CDEM cache shape does not match this grid/batch size: {path}")
    batches = payload.get("batches")
    if not isinstance(batches, dict):
        raise ValueError(f"CDEM cache batches are invalid: {path}")
    return {str(key): values for key, values in batches.items() if isinstance(values, list)}


def write_summary_audit(
    audit_path: Path,
    grid_path: Path,
    output_path: Path,
    grid_rows: list[dict[str, str]],
    points: list[tuple[str, float, float, float, float]],
    output_rows: list[dict[str, str]],
    api_batches: int | None,
) -> None:
    coverage = [float(row["terrain_coverage_fraction"]) for row in output_rows]
    audit = {
        "feature_set_version": FEATURE_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": {
            "source_id": "nrcan_cdem_1945_2011",
            "elevation_api": ELEVATION_API,
            "vintage_end_year": 2011,
            "sampling": "3 by 3 equal-area EPSG:3978 cell-centre quadrature samples",
            "historical_semantics": "static terrain only",
        },
        "input_grid": str(grid_path.relative_to(PROJECT_ROOT)),
        "output": str(output_path.relative_to(PROJECT_ROOT)),
        "counts": {
            "grid_rows": len(grid_rows),
            "requested_samples": len(points),
            "returned_samples": sum(int(row["terrain_sample_count"]) for row in output_rows),
            "api_batches": api_batches,
            "full_coverage_cells": sum(value == 1 for value in coverage),
            "cells_with_no_coverage": sum(value == 0 for value in coverage),
        },
        "quality": {
            "minimum_cell_coverage_fraction": min(coverage),
            "maximum_cell_coverage_fraction": max(coverage),
            "missing_slope_cells": sum(not row["terrain_slope_deg"] for row in output_rows),
        },
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")


def load_existing_summary(path: Path, grid_ids: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        expected_fields = [
            "grid_id",
            "terrain_elevation_mean_m",
            "terrain_slope_deg",
            "terrain_aspect_sin",
            "terrain_aspect_cos",
            "terrain_ruggedness_m",
            "terrain_sample_count",
            "terrain_coverage_fraction",
            "terrain_source_id",
            "terrain_source_vintage_end_year",
            "terrain_feature_version",
        ]
        if reader.fieldnames != expected_fields:
            raise ValueError(f"Unexpected terrain summary schema in {path}: {reader.fieldnames}")
        rows = list(reader)
    identifiers = [row["grid_id"] for row in rows]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) != grid_ids:
        raise ValueError("Existing terrain summary does not align one-to-one with the grid.")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="Resumable, ignored raw CDEM API responses.")
    # The service rejects request bodies/URLs above its proxy limit.  One
    # two hundred vertices stays below that limit while still using its documented
    # multi-vertex profile endpoint.
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--pause-seconds", type=float, default=0.05)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--audit-existing", action="store_true", help="Recompute the audit from an existing summary without querying CDEM.")
    args = parser.parse_args()

    if args.batch_size < 9:
        raise ValueError("--batch-size must be at least 9.")
    if args.output.exists() and not args.force and not args.audit_existing:
        raise FileExistsError(f"Refusing to overwrite {args.output}; use --force after reviewing it.")

    grid = load_grid(args.grid)
    points, indexes = sample_points(grid)
    if args.audit_existing:
        output_rows = load_existing_summary(args.output, {row["grid_id"] for row in grid})
        write_summary_audit(args.audit, args.grid, args.output, grid, points, output_rows, api_batches=None)
        print(f"Recomputed terrain audit: {args.audit}")
        return
    cached_batches = load_cache(args.cache, len(points), args.batch_size)
    elevations: list[float | None] = []
    for start in range(0, len(points), args.batch_size):
        batch_key = str(start)
        expected_count = len(points[start : start + args.batch_size])
        values = cached_batches.get(batch_key)
        if values is None:
            values = fetch_batch(points[start : start + args.batch_size])
            if len(values) != expected_count:
                raise AssertionError(f"CDEM cache batch {batch_key} has {len(values)} values; expected {expected_count}")
            cached_batches[batch_key] = values
            write_atomic_json(
                args.cache,
                {
                    "source_id": "nrcan_cdem_1945_2011",
                    "elevation_api": ELEVATION_API,
                    "point_count": len(points),
                    "batch_size": args.batch_size,
                    "batches": cached_batches,
                },
            )
        elif len(values) != expected_count:
            raise ValueError(f"CDEM cache batch {batch_key} has {len(values)} values; expected {expected_count}")
        elevations.extend(values)
        if (start // args.batch_size + 1) % 25 == 0:
            print(f"CDEM batches complete: {start // args.batch_size + 1}", flush=True)
        if start + args.batch_size < len(points):
            time.sleep(args.pause_seconds)

    fields = [
        "grid_id",
        "terrain_elevation_mean_m",
        "terrain_slope_deg",
        "terrain_aspect_sin",
        "terrain_aspect_cos",
        "terrain_ruggedness_m",
        "terrain_sample_count",
        "terrain_coverage_fraction",
        "terrain_source_id",
        "terrain_source_vintage_end_year",
        "terrain_feature_version",
    ]
    output_rows = []
    for grid_row in grid:
        grid_id = grid_row["grid_id"]
        row = summarize([elevations[index] for index in indexes[grid_id]])
        row.update(
            {
                "grid_id": grid_id,
                "terrain_source_id": "nrcan_cdem_1945_2011",
                "terrain_source_vintage_end_year": "2011",
                "terrain_feature_version": FEATURE_VERSION,
            }
        )
        output_rows.append(row)
    write_atomic_csv(args.output, fields, output_rows)

    write_summary_audit(args.audit, args.grid, args.output, grid, points, output_rows, api_batches=len(cached_batches))
    print(f"Wrote {len(output_rows):,} terrain summaries: {args.output}")
    print(f"Wrote terrain audit: {args.audit}")


if __name__ == "__main__":
    main()
