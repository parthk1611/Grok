#!/usr/bin/env python3
"""Build Ontario Fire Region 10 km grid cells and daily ignition labels."""

from __future__ import annotations

import csv
import gzip
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from shapely import covers, prepare, union_all
from shapely.geometry import Point, Polygon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = PROJECT_ROOT / "data/raw/ontario_fire_region/fire_response_plan_sectors.geojson"
EVENTS = PROJECT_ROOT / "data/interim/ontario_nfdb_ignitions_2010_2019.csv"
GRID = PROJECT_ROOT / "data/interim/ontario_fire_region_10km_grid.csv"
FILTERED_EVENTS = PROJECT_ROOT / "data/interim/ontario_fire_region_nfdb_ignitions_2010_2019.csv"
LABELS = PROJECT_ROOT / "data/processed/ontario_fire_region_10km_daily_ignition_labels_2010_2019.csv.gz"
AUDIT = PROJECT_ROOT / "data/processed/ontario_fire_region_10km_daily_ignition_labels_2010_2019_audit.json"

CELL_SIZE_M = 10_000
FIRE_SEASON_START = (5, 1)
FIRE_SEASON_END = (10, 31)

# EPSG:3978 — NAD83 / Canada Atlas Lambert. The transform is implemented here
# so the data build remains reproducible without a system GIS dependency.
SEMI_MAJOR_AXIS_M = 6_378_137.0
INVERSE_FLATTENING = 298.257222101
ECCENTRICITY = math.sqrt(2 / INVERSE_FLATTENING - 1 / INVERSE_FLATTENING**2)
CENTRAL_MERIDIAN_RAD = math.radians(-95)
LATITUDE_OF_ORIGIN_RAD = math.radians(49)
STANDARD_PARALLEL_1_RAD = math.radians(49)
STANDARD_PARALLEL_2_RAD = math.radians(77)
FALSE_EASTING_M = 0
FALSE_NORTHING_M = 0


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


def project(longitude: float, latitude: float) -> tuple[float, float]:
    """Convert WGS84-like longitude/latitude to Canada Atlas Lambert metres."""
    phi = math.radians(latitude)
    lam = math.radians(longitude)
    rho = SEMI_MAJOR_AXIS_M * F * t(phi) ** N
    theta = N * (lam - CENTRAL_MERIDIAN_RAD)
    return (
        FALSE_EASTING_M + rho * math.sin(theta),
        FALSE_NORTHING_M + RHO_0 - rho * math.cos(theta),
    )


def unproject(x: float, y: float) -> tuple[float, float]:
    """Convert Canada Atlas Lambert metres to longitude/latitude."""
    dx = x - FALSE_EASTING_M
    dy = RHO_0 - (y - FALSE_NORTHING_M)
    rho = math.copysign(math.hypot(dx, dy), N)
    theta = math.atan2(dx, dy)
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
    longitude = math.degrees(CENTRAL_MERIDIAN_RAD + theta / N)
    return longitude, math.degrees(phi)


def load_study_area():
    """Return a prepared EPSG:3978 union of Ontario Fire Region sectors."""
    boundary = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    if boundary.get("type") != "FeatureCollection":
        raise ValueError("Study boundary must be a GeoJSON FeatureCollection.")

    polygons = []
    for feature in boundary["features"]:
        properties = feature.get("properties", {})
        if properties.get("RESPONSE_SECTOR") == "OFR":
            raise ValueError("Study boundary must exclude Outside Fire Region features.")
        geometry = feature["geometry"]
        raw_polygons = (
            [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
            if geometry["type"] == "MultiPolygon"
            else None
        )
        if raw_polygons is None:
            raise ValueError(f"Unsupported boundary geometry: {geometry['type']}")
        for raw_polygon in raw_polygons:
            projected_rings = [
                [project(longitude, latitude) for longitude, latitude in ring] for ring in raw_polygon
            ]
            polygon = Polygon(projected_rings[0], projected_rings[1:])
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if not polygon.is_empty:
                polygons.append(polygon)
    if not polygons:
        raise ValueError("Study boundary has no valid polygons.")
    study_area = union_all(polygons)
    if study_area.is_empty:
        raise ValueError("Study boundary union is empty.")
    prepare(study_area)
    return study_area


def cell_origin(value: float) -> int:
    return math.floor(value / CELL_SIZE_M) * CELL_SIZE_M


def cell_id(x0: int, y0: int) -> str:
    return f"ofr10km_{x0}_{y0}"


def grid_candidate_origins(study_area) -> list[tuple[int, int]]:
    min_x, min_y, max_x, max_y = study_area.bounds
    return [
        (x0, y0)
        for x0 in range(cell_origin(min_x), cell_origin(max_x) + CELL_SIZE_M, CELL_SIZE_M)
        for y0 in range(cell_origin(min_y), cell_origin(max_y) + CELL_SIZE_M, CELL_SIZE_M)
    ]


def fire_season_dates() -> list[date]:
    dates: list[date] = []
    for year in range(2010, 2020):
        current = date(year, *FIRE_SEASON_START)
        end = date(year, *FIRE_SEASON_END)
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)
    return dates


def load_events(
    study_area,
) -> tuple[list[dict[str, str]], dict[tuple[str, str], list[dict[str, str]]], Counter[str]]:
    selected: list[dict[str, str]] = []
    labels: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    audit: Counter[str] = Counter()
    with EVENTS.open(encoding="utf-8", newline="") as source:
        for event in csv.DictReader(source):
            audit["input_events"] += 1
            event_date = date.fromisoformat(event["ignition_date"])
            if not (FIRE_SEASON_START <= (event_date.month, event_date.day) <= FIRE_SEASON_END):
                audit["excluded_outside_fire_season"] += 1
                continue
            x, y = project(float(event["longitude"]), float(event["latitude"]))
            if not covers(study_area, Point(x, y)):
                audit["excluded_outside_fire_region"] += 1
                continue
            x0, y0 = cell_origin(x), cell_origin(y)
            event["grid_id"] = cell_id(x0, y0)
            event["grid_x_m"] = str(x0)
            event["grid_y_m"] = str(y0)
            selected.append(event)
            labels[(event["grid_id"], event["ignition_date"])].append(event)
            audit["included_events"] += 1
    return selected, labels, audit


def build_grid(
    study_area,
    event_grid_ids: set[str],
) -> list[tuple[str, int, int, float, float]]:
    cells: list[tuple[str, int, int, float, float]] = []
    for x0, y0 in grid_candidate_origins(study_area):
        identifier = cell_id(x0, y0)
        centre_x, centre_y = x0 + CELL_SIZE_M / 2, y0 + CELL_SIZE_M / 2
        if identifier not in event_grid_ids and not covers(study_area, Point(centre_x, centre_y)):
            continue
        longitude, latitude = unproject(centre_x, centre_y)
        cells.append((identifier, x0, y0, longitude, latitude))
    return sorted(cells)


def main() -> None:
    if not BOUNDARY.exists():
        raise FileNotFoundError(f"Download the Ontario Fire Region boundary first: {BOUNDARY}")
    if not EVENTS.exists():
        raise FileNotFoundError(f"Prepare Ontario NFDB events first: {EVENTS}")

    GRID.parent.mkdir(parents=True, exist_ok=True)
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    study_area = load_study_area()
    selected_events, labels_by_cell_day, audit = load_events(study_area)
    cells = build_grid(study_area, {event["grid_id"] for event in selected_events})

    with FILTERED_EVENTS.open("w", encoding="utf-8", newline="") as destination:
        fields = list(selected_events[0]) if selected_events else []
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected_events)

    with GRID.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=["grid_id", "grid_x_m", "grid_y_m", "centroid_longitude", "centroid_latitude"],
        )
        writer.writeheader()
        for identifier, x0, y0, longitude, latitude in cells:
            writer.writerow(
                {
                    "grid_id": identifier,
                    "grid_x_m": x0,
                    "grid_y_m": y0,
                    "centroid_longitude": f"{longitude:.6f}",
                    "centroid_latitude": f"{latitude:.6f}",
                }
            )

    with tempfile.NamedTemporaryFile(
        mode="wb", dir=LABELS.parent, prefix=f"{LABELS.name}.", suffix=".tmp", delete=False
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        with gzip.open(temporary_path, "wt", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=["grid_id", "ignition_date", "ignition", "ignition_count", "large_fire_200ha"],
            )
            writer.writeheader()
            for current_date in fire_season_dates():
                date_text = current_date.isoformat()
                for identifier, *_ in cells:
                    events = labels_by_cell_day.get((identifier, date_text), [])
                    writer.writerow(
                        {
                            "grid_id": identifier,
                            "ignition_date": date_text,
                            "ignition": int(bool(events)),
                            "ignition_count": len(events),
                            "large_fire_200ha": int(
                                any(event["large_fire_200ha"] == "1" for event in events)
                            ),
                        }
                    )
        os.replace(temporary_path, LABELS)
    finally:
        temporary_path.unlink(missing_ok=True)

    audit.update(
        {
            "grid_cells": len(cells),
            "fire_season_days": len(fire_season_dates()),
            "label_rows": len(cells) * len(fire_season_dates()),
            "positive_cell_days": len(labels_by_cell_day),
            "large_fire_positive_cell_days": sum(
                any(event["large_fire_200ha"] == "1" for event in events)
                for events in labels_by_cell_day.values()
            ),
        }
    )
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": {
            "boundary": "Ontario Fire Response Plan Area: RES_SECTOR != OFR",
            "projection": "EPSG:3978 (NAD83 / Canada Atlas Lambert)",
            "cell_size_m": CELL_SIZE_M,
            "years_inclusive": [2010, 2019],
            "fire_season": "May 1 through October 31 inclusive",
        },
        "cell_inclusion": (
            "Grid-cell centroid is in the managed Fire Region, plus any edge cell containing a selected NFDB ignition."
        ),
        "counts": dict(audit),
        "outputs": {
            "grid": str(GRID.relative_to(PROJECT_ROOT)),
            "events": str(FILTERED_EVENTS.relative_to(PROJECT_ROOT)),
            "daily_labels": str(LABELS.relative_to(PROJECT_ROOT)),
        },
    }
    AUDIT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cells):,} Ontario Fire Region grid cells.")
    print(f"Wrote {audit['included_events']:,} selected ignition events.")
    print(f"Wrote {audit['label_rows']:,} daily label rows: {LABELS}")
    print(f"Wrote audit report: {AUDIT}")


if __name__ == "__main__":
    main()
