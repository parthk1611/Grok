#!/usr/bin/env python3
"""Join time-safe explanatory features to the Ontario 10 km ignition panel.

The label panel is immutable input.  This builder streams one local calendar
day at a time, preserves every input row and label column, and refuses source
records at or after the issue date.  CWFIS and NFDB history are explicitly
retrospective features: neither has the per-record historic availability ledger
needed to make an operational forecast claim.
"""

from __future__ import annotations

import argparse
import bz2
import csv
import gzip
import heapq
import json
import math
import os
import tempfile
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEATURE_SET_VERSION = "ignition-feature-v1.0.0"
LOCAL_TIMEZONE = ZoneInfo("America/Toronto")
FIRE_SEASON_START = (5, 1)
FIRE_SEASON_END = (10, 31)

DEFAULT_GRID = PROJECT_ROOT / "data/interim/ontario_fire_region_10km_grid.csv"
DEFAULT_LABELS = PROJECT_ROOT / "data/processed/ontario_fire_region_10km_daily_ignition_labels_2010_2019.csv.gz"
DEFAULT_EVENTS = PROJECT_ROOT / "data/interim/ontario_fire_region_nfdb_ignitions_2010_2019.csv"
DEFAULT_TERRAIN = PROJECT_ROOT / "data/interim/cdem_terrain_10km_summary.csv"
DEFAULT_CWFIS = PROJECT_ROOT / "data/raw/cwfis/cwfis_fwi2010sv3.0_ll.csv.bz2"
DEFAULT_CWFIS_MANIFEST = PROJECT_ROOT / "data/raw/cwfis/manifest.json"
DEFAULT_SOURCE_REGISTRY = PROJECT_ROOT / "data/metadata/ignition_feature_source_registry_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/processed/ontario_fire_region_10km_daily_ignition_features_v1.csv.gz"
DEFAULT_AUDIT = PROJECT_ROOT / "data/processed/ontario_fire_region_10km_daily_ignition_features_v1_audit.json"

LABEL_FIELDS = ["grid_id", "ignition_date", "ignition", "ignition_count", "large_fire_200ha"]
GRID_FIELDS = ["grid_id", "grid_x_m", "grid_y_m", "centroid_longitude", "centroid_latitude"]
TERRAIN_FIELDS = [
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
CWFIS_NUMERIC_FIELDS = ("temp", "rh", "ws", "precip", "ffmc", "dmc", "dc", "isi", "bui", "fwi", "dsr")
CWFIS_OUTPUT_FIELDS = {
    "temp": "cwfis_temp_c",
    "rh": "cwfis_rh_pct",
    "ws": "cwfis_wind_speed_kmh",
    "precip": "cwfis_precip_mm",
    "ffmc": "cwfis_ffmc",
    "dmc": "cwfis_dmc",
    "dc": "cwfis_dc",
    "isi": "cwfis_isi",
    "bui": "cwfis_bui",
    "fwi": "cwfis_fwi",
    "dsr": "cwfis_dsr",
}


@dataclass(frozen=True)
class GridCell:
    grid_id: str
    x_m: int
    y_m: int
    longitude: float
    latitude: float


@dataclass(frozen=True)
class Station:
    key: tuple[str, str, str, str]
    longitude: float
    latitude: float
    first_seen: date


@dataclass(frozen=True)
class StationChoice:
    key: tuple[str, str, str, str]
    distance_km: float
    first_seen: date


@dataclass(frozen=True)
class CwfisRecord:
    values: dict[str, float | None]
    options: str
    calcstatus: int | None


@dataclass(frozen=True)
class LabelProfile:
    row_count: int
    dates: list[date]
    fields: list[str]


def parse_date(value: str, context: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Invalid ISO date for {context}: {value!r}") from error


def parse_float(value: str | None) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def expected_fire_season_dates() -> list[date]:
    dates: list[date] = []
    for year in range(2010, 2020):
        current = date(year, *FIRE_SEASON_START)
        end = date(year, *FIRE_SEASON_END)
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)
    return dates


def load_grid(path: Path) -> dict[str, GridCell]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != GRID_FIELDS:
            raise ValueError(f"Unexpected grid schema in {path}: {reader.fieldnames}")
        grid: dict[str, GridCell] = {}
        for row in reader:
            grid_id = row["grid_id"]
            if grid_id in grid:
                raise ValueError(f"Duplicate grid_id in grid dimension: {grid_id}")
            grid[grid_id] = GridCell(
                grid_id=grid_id,
                x_m=int(row["grid_x_m"]),
                y_m=int(row["grid_y_m"]),
                longitude=float(row["centroid_longitude"]),
                latitude=float(row["centroid_latitude"]),
            )
    if not grid:
        raise ValueError("Grid dimension is empty.")
    return grid


def load_terrain(path: Path, grid_ids: set[str]) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != TERRAIN_FIELDS:
            raise ValueError(f"Unexpected terrain schema in {path}: {reader.fieldnames}")
        terrain: dict[str, dict[str, str]] = {}
        for row in reader:
            grid_id = row["grid_id"]
            if grid_id in terrain:
                raise ValueError(f"Duplicate terrain grid_id: {grid_id}")
            terrain[grid_id] = row
    missing = grid_ids - set(terrain)
    extra = set(terrain) - grid_ids
    if missing or extra:
        raise ValueError(f"Terrain spatial alignment failed: missing={len(missing)}, extra={len(extra)}")
    if any(row["terrain_feature_version"] != FEATURE_SET_VERSION for row in terrain.values()):
        raise ValueError("Terrain feature version does not match the ignition feature set version.")
    return terrain


def scan_labels(path: Path, grid_ids: set[str]) -> LabelProfile:
    """Validate the complete input panel before any feature output is written."""
    dates: list[date] = []
    current_date: date | None = None
    daily_grid_ids: set[str] = set()
    previous_key: tuple[str, str] | None = None
    row_count = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != LABEL_FIELDS:
            raise ValueError(f"Unexpected label schema in {path}: {reader.fieldnames}")
        for row in reader:
            grid_id = row["grid_id"]
            issue_date = parse_date(row["ignition_date"], "label")
            key = (issue_date.isoformat(), grid_id)
            if previous_key is not None and key <= previous_key:
                raise ValueError("Labels must be strictly ordered by ignition_date, grid_id to prove key uniqueness.")
            previous_key = key
            if grid_id not in grid_ids:
                raise ValueError(f"Label row uses grid_id absent from grid dimension: {grid_id}")
            if row["ignition"] not in {"0", "1"}:
                raise ValueError(f"Invalid binary ignition label at {key}: {row['ignition']!r}")
            if parse_int(row["ignition_count"]) is None or int(row["ignition_count"]) < 0:
                raise ValueError(f"Invalid ignition_count at {key}: {row['ignition_count']!r}")
            if row["large_fire_200ha"] not in {"0", "1"}:
                raise ValueError(f"Invalid large_fire_200ha label at {key}: {row['large_fire_200ha']!r}")
            if current_date != issue_date:
                if current_date is not None and daily_grid_ids != grid_ids:
                    raise ValueError(f"Label row universe changed on {current_date}: {len(daily_grid_ids)} cells")
                current_date = issue_date
                dates.append(issue_date)
                daily_grid_ids = set()
            if grid_id in daily_grid_ids:
                raise ValueError(f"Duplicate label key: {key}")
            daily_grid_ids.add(grid_id)
            row_count += 1
    if current_date is not None and daily_grid_ids != grid_ids:
        raise ValueError(f"Label row universe changed on {current_date}: {len(daily_grid_ids)} cells")
    if dates != expected_fire_season_dates():
        raise ValueError("Label date coverage must be every May 1–October 31 date from 2010 through 2019.")
    expected_rows = len(grid_ids) * len(dates)
    if row_count != expected_rows:
        raise ValueError(f"Label row count mismatch: expected {expected_rows}, found {row_count}")
    return LabelProfile(row_count=row_count, dates=dates, fields=LABEL_FIELDS)


def iter_label_days(path: Path, selected_dates: set[date]):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        current_date: date | None = None
        rows: list[dict[str, str]] = []
        for row in reader:
            issue_date = parse_date(row["ignition_date"], "label")
            if current_date is None:
                current_date = issue_date
            if issue_date != current_date:
                if current_date in selected_dates:
                    yield current_date, rows
                current_date, rows = issue_date, []
            rows.append(row)
        if current_date is not None and current_date in selected_dates:
            yield current_date, rows


def haversine_km(a_lon: float, a_lat: float, b_lon: float, b_lat: float) -> float:
    radius_km = 6_371.0088
    lat1, lat2 = math.radians(a_lat), math.radians(b_lat)
    d_lat, d_lon = lat2 - lat1, math.radians(b_lon - a_lon)
    value = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return radius_km * 2 * math.asin(math.sqrt(value))


def station_key(row: dict[str, str]) -> tuple[str, str, str, str] | None:
    longitude, latitude = parse_float(row.get("lon")), parse_float(row.get("lat"))
    if longitude is None or latitude is None:
        return None
    return ((row.get("aes") or "").strip(), (row.get("wmo") or "").strip(), f"{longitude:.5f}", f"{latitude:.5f}")


def load_station_catalog(path: Path, grid: dict[str, GridCell]) -> dict[tuple[str, str, str, str], Station]:
    lon_values = [cell.longitude for cell in grid.values()]
    lat_values = [cell.latitude for cell in grid.values()]
    # A generous five-degree buffer retains border stations while avoiding a
    # needless all-Canada distance matrix for every grid cell.
    bounds = (min(lon_values) - 5, max(lon_values) + 5, min(lat_values) - 5, max(lat_values) + 5)
    stations: dict[tuple[str, str, str, str], Station] = {}
    with bz2.open(path, "rt", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        required = {"rep_date", "aes", "wmo", "lon", "lat"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"CWFIS schema is missing station fields: {sorted(required - set(reader.fieldnames or []))}")
        for row in reader:
            key = station_key(row)
            if key is None:
                continue
            longitude, latitude = float(key[2]), float(key[3])
            if not (bounds[0] <= longitude <= bounds[1] and bounds[2] <= latitude <= bounds[3]):
                continue
            observed = parse_date((row["rep_date"] or "")[:10], "CWFIS rep_date")
            previous = stations.get(key)
            if previous is None or observed < previous.first_seen:
                stations[key] = Station(key, longitude, latitude, observed)
    if not stations:
        raise ValueError("No CWFIS stations were found near the Ontario grid.")
    return stations


def nearest_station_choices(
    grid: dict[str, GridCell], stations: dict[tuple[str, str, str, str], Station], count: int
) -> dict[str, list[StationChoice]]:
    choices: dict[str, list[StationChoice]] = {}
    for grid_id, cell in grid.items():
        nearby = heapq.nsmallest(
            count,
            (
                StationChoice(station.key, haversine_km(cell.longitude, cell.latitude, station.longitude, station.latitude), station.first_seen)
                for station in stations.values()
            ),
            key=lambda choice: choice.distance_km,
        )
        if not nearby:
            raise ValueError(f"No CWFIS stations were selectable for {grid_id}")
        choices[grid_id] = nearby
    return choices


def read_cwfis_days(path: Path, required_dates: set[date], allowed_stations: set[tuple[str, str, str, str]]):
    """Yield only needed CWFIS days, in source order, with one record per station."""
    current_date: date | None = None
    current_records: dict[tuple[str, str, str, str], CwfisRecord] = {}
    with bz2.open(path, "rt", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        required = {"rep_date", "aes", "wmo", "lon", "lat", "opts", "calcstatus", *CWFIS_NUMERIC_FIELDS}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"CWFIS schema is missing feature fields: {sorted(required - set(reader.fieldnames or []))}")
        for row in reader:
            observed = parse_date((row["rep_date"] or "")[:10], "CWFIS rep_date")
            if current_date is None:
                current_date = observed
            if observed != current_date:
                if current_date in required_dates:
                    yield current_date, current_records
                current_date, current_records = observed, {}
            if observed not in required_dates:
                continue
            key = station_key(row)
            if key is None or key not in allowed_stations:
                continue
            record = CwfisRecord(
                values={field: parse_float(row.get(field)) for field in CWFIS_NUMERIC_FIELDS},
                options=(row.get("opts") or "").strip(),
                calcstatus=parse_int(row.get("calcstatus")),
            )
            existing = current_records.get(key)
            if existing is None or (existing.values["fwi"] is None and record.values["fwi"] is not None):
                current_records[key] = record
    if current_date is not None and current_date in required_dates:
        yield current_date, current_records


class CwfisDayLookup:
    def __init__(self, source):
        self.source = iter(source)
        self.next_item = next(self.source, None)
        self.next_item_was_returned = False

    def records_for(self, requested: date) -> dict[tuple[str, str, str, str], CwfisRecord]:
        # Do not advance beyond the final requested source date. The public
        # archive contains concatenated historical extracts with a 2015 overlap
        # that is not globally date sorted; unneeded trailing rows must not make
        # an otherwise complete earlier build fail.
        if self.next_item_was_returned:
            self.next_item = next(self.source, None)
            self.next_item_was_returned = False
        while self.next_item is not None and self.next_item[0] < requested:
            self.next_item = next(self.source, None)
        if self.next_item is not None and self.next_item[0] == requested:
            records = self.next_item[1]
            self.next_item_was_returned = True
            return records
        return {}


def weighted_value(selected: list[tuple[StationChoice, CwfisRecord]], field: str) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for choice, record in selected:
        value = record.values[field]
        if value is None:
            continue
        weight = 1 / max(choice.distance_km, 1.0) ** 2
        numerator += value * weight
        denominator += weight
    return None if denominator == 0 else numerator / denominator


def make_cwfis_features(
    observation_date: date,
    records: dict[tuple[str, str, str, str], CwfisRecord],
    choices: list[StationChoice],
) -> dict[str, str]:
    selected = [
        (choice, records[choice.key])
        for choice in choices
        if choice.first_seen <= observation_date and choice.key in records and records[choice.key].values["fwi"] is not None
    ]
    output = {
        "cwfis_source_id": "cwfis_fwi_2010s_v3_ll",
        "cwfis_observation_date": observation_date.isoformat(),
        "cwfis_station_count": str(len(selected)),
        "cwfis_nearest_station_km": "" if not selected else f"{min(choice.distance_km for choice, _ in selected):.6f}",
        "cwfis_observation_coverage_fraction": f"{len(selected) / len(choices):.6f}",
        "cwfis_imputed_station_fraction": "",
        "cwfis_calcstatus_nonzero_fraction": "",
        "cwfis_missing": "1" if not selected else "0",
    }
    for raw_name, output_name in CWFIS_OUTPUT_FIELDS.items():
        value = weighted_value(selected, raw_name)
        output[output_name] = "" if value is None else f"{value:.6f}"
    if selected:
        imputed = sum("IDW" in record.options or "M=" in record.options for _, record in selected)
        statuses = [record.calcstatus for _, record in selected if record.calcstatus is not None]
        output["cwfis_imputed_station_fraction"] = f"{imputed / len(selected):.6f}"
        output["cwfis_calcstatus_nonzero_fraction"] = "" if not statuses else f"{sum(value != 0 for value in statuses) / len(statuses):.6f}"
    return output


def load_events(path: Path, grid: dict[str, GridCell], panel_dates: list[date]) -> dict[date, list[str]]:
    by_date: dict[date, list[str]] = {}
    panel_start, panel_end = panel_dates[0], panel_dates[-1]
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        required = {"ignition_date", "grid_id"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"Event source lacks {sorted(required)}: {path}")
        for row in reader:
            event_date = parse_date(row["ignition_date"], "event")
            grid_id = row["grid_id"]
            if grid_id not in grid:
                raise ValueError(f"Event grid_id is absent from grid dimension: {grid_id}")
            if panel_start <= event_date <= panel_end:
                by_date.setdefault(event_date, []).append(grid_id)
    return by_date


class FireHistory:
    """Completed-window counts; events are inserted only after their date ends."""

    def __init__(self, grid: dict[str, GridCell]) -> None:
        self.grid = grid
        self.by_coordinate = {(cell.x_m, cell.y_m): grid_id for grid_id, cell in grid.items()}
        self.windows = {30: (deque(), Counter(), Counter()), 365: (deque(), Counter(), Counter())}
        self.last_same_cell: dict[str, date] = {}

    def add(self, event_date: date, grid_id: str) -> None:
        cell = self.grid[grid_id]
        neighbourhood = [
            neighbour_id
            for x_delta in (-10_000, 0, 10_000)
            for y_delta in (-10_000, 0, 10_000)
            if (neighbour_id := self.by_coordinate.get((cell.x_m + x_delta, cell.y_m + y_delta))) is not None
        ]
        for queue, same_counts, neighbourhood_counts in self.windows.values():
            queue.append((event_date, grid_id, neighbourhood))
            same_counts[grid_id] += 1
            for neighbour_id in neighbourhood:
                neighbourhood_counts[neighbour_id] += 1
        self.last_same_cell[grid_id] = event_date

    def advance(self, issue_date: date, event_dates: deque[tuple[date, str]]) -> None:
        while event_dates and event_dates[0][0] < issue_date:
            event_date, grid_id = event_dates.popleft()
            self.add(event_date, grid_id)
        for days, (queue, same_counts, neighbourhood_counts) in self.windows.items():
            cutoff = issue_date - timedelta(days=days)
            while queue and queue[0][0] < cutoff:
                _, grid_id, neighbourhood = queue.popleft()
                same_counts[grid_id] -= 1
                for neighbour_id in neighbourhood:
                    neighbourhood_counts[neighbour_id] -= 1

    def values(self, grid_id: str, issue_date: date) -> dict[str, str]:
        output = {}
        for days, (_, same_counts, neighbourhood_counts) in self.windows.items():
            output[f"fire_history_same_cell_{days}d"] = str(same_counts[grid_id])
            output[f"fire_history_neighborhood_{days}d"] = str(neighbourhood_counts[grid_id])
        prior = self.last_same_cell.get(grid_id)
        output["fire_history_days_since_same_cell"] = "" if prior is None else str((issue_date - prior).days)
        return output


def calendar_features(issue_date: date, latitude: float) -> dict[str, str]:
    day_of_year = issue_date.timetuple().tm_yday
    phase = 2 * math.pi * day_of_year / (366 if issue_date.year % 4 == 0 else 365)
    declination = math.radians(-23.44) * math.cos(2 * math.pi * (day_of_year + 10) / 365.2422)
    cosine_hour_angle = -math.tan(math.radians(latitude)) * math.tan(declination)
    hour_angle = math.acos(max(-1.0, min(1.0, cosine_hour_angle)))
    day_length_hours = 24 * hour_angle / math.pi
    issue_time_utc = datetime(issue_date.year, issue_date.month, issue_date.day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    return {
        "issue_time_utc": issue_time_utc.isoformat().replace("+00:00", "Z"),
        "calendar_day_of_year": str(day_of_year),
        "calendar_doy_sin": f"{math.sin(phase):.6f}",
        "calendar_doy_cos": f"{math.cos(phase):.6f}",
        "calendar_is_weekend": str(int(issue_date.weekday() >= 5)),
        "calendar_day_length_hours": f"{day_length_hours:.6f}",
    }


def output_fields() -> list[str]:
    return LABEL_FIELDS + [
        "feature_set_version",
        "issue_time_utc",
        "calendar_day_of_year",
        "calendar_doy_sin",
        "calendar_doy_cos",
        "calendar_is_weekend",
        "calendar_day_length_hours",
        "terrain_elevation_mean_m",
        "terrain_slope_deg",
        "terrain_aspect_sin",
        "terrain_aspect_cos",
        "terrain_ruggedness_m",
        "terrain_sample_count",
        "terrain_coverage_fraction",
        "terrain_source_id",
        "terrain_source_vintage_end_year",
        "cwfis_source_id",
        "cwfis_observation_date",
        "cwfis_station_count",
        "cwfis_nearest_station_km",
        "cwfis_observation_coverage_fraction",
        "cwfis_imputed_station_fraction",
        "cwfis_calcstatus_nonzero_fraction",
        "cwfis_missing",
        *CWFIS_OUTPUT_FIELDS.values(),
        "cwfis_precip_7d_mm",
        "cwfis_precip_7d_coverage_days",
        "fire_history_source_id",
        "fire_history_same_cell_30d",
        "fire_history_neighborhood_30d",
        "fire_history_same_cell_365d",
        "fire_history_neighborhood_365d",
        "fire_history_days_since_same_cell",
    ]


def selected_panel_dates(profile: LabelProfile, start: str | None, end: str | None) -> list[date]:
    start_date = parse_date(start, "--start-date") if start else profile.dates[0]
    end_date = parse_date(end, "--end-date") if end else profile.dates[-1]
    selected = [value for value in profile.dates if start_date <= value <= end_date]
    if not selected:
        raise ValueError("The selected date range has no panel dates.")
    return selected


def source_metadata(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def display_path(path: Path) -> str:
    """Keep project artifacts concise while allowing fixture/external inputs."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def build(args: argparse.Namespace) -> dict[str, object]:
    grid = load_grid(args.grid)
    profile = scan_labels(args.labels, set(grid))
    dates = selected_panel_dates(profile, args.start_date, args.end_date)
    selected_dates = set(dates)
    terrain = load_terrain(args.terrain, set(grid))
    registry = source_metadata(args.source_registry)
    if registry is None or registry.get("feature_set_version") != FEATURE_SET_VERSION:
        raise ValueError("Feature source registry is missing or has the wrong feature_set_version.")
    cwfis_manifest = source_metadata(args.cwfis_manifest)
    if cwfis_manifest is not None and cwfis_manifest.get("source_id") != "cwfis_fwi_2010s_v3_ll":
        raise ValueError("CWFIS manifest source_id is not recognized.")

    stations = load_station_catalog(args.cwfis, grid)
    choices = nearest_station_choices(grid, stations, args.station_count)
    allowed_stations = {choice.key for cell_choices in choices.values() for choice in cell_choices}
    required_source_dates = {
        issue_date - timedelta(days=lag)
        for issue_date in dates
        for lag in range(1, 8)
    }
    cwfis_lookup = CwfisDayLookup(read_cwfis_days(args.cwfis, required_source_dates, allowed_stations))
    events_by_date = load_events(args.events, grid, profile.dates)
    pending_events = deque(
        (event_date, grid_id)
        for event_date in sorted(events_by_date)
        for grid_id in events_by_date[event_date]
    )
    history = FireHistory(grid)

    if args.output.exists() and not args.force:
        raise FileExistsError(f"Refusing to overwrite {args.output}; use --force after reviewing it.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    feature_fields = output_fields()
    weather_window: deque[tuple[date, dict[str, dict[str, str]]]] = deque()
    last_weather_date: date | None = None
    row_count = 0
    weather_missing_rows = 0
    weather_available_rows = 0
    weather_date_violations = 0
    output_previous_key: tuple[str, str] | None = None

    with tempfile.NamedTemporaryFile("wb", dir=args.output.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with gzip.open(temporary_path, "wt", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=feature_fields)
            writer.writeheader()
            for issue_date, label_rows in iter_label_days(args.labels, selected_dates):
                window_start = issue_date - timedelta(days=7)
                window_end = issue_date - timedelta(days=1)
                if last_weather_date is None or window_start > last_weather_date + timedelta(days=1):
                    weather_window.clear()
                    last_weather_date = window_start - timedelta(days=1)
                while last_weather_date < window_end:
                    last_weather_date += timedelta(days=1)
                    records = cwfis_lookup.records_for(last_weather_date)
                    weather_window.append(
                        (
                            last_weather_date,
                            {
                                grid_id: make_cwfis_features(last_weather_date, records, cell_choices)
                                for grid_id, cell_choices in choices.items()
                            },
                        )
                    )
                while weather_window and weather_window[0][0] < window_start:
                    weather_window.popleft()
                if not weather_window or weather_window[-1][0] != window_end:
                    raise AssertionError("CWFIS rolling window did not end at the prior day.")
                today_weather = weather_window[-1][1]
                history.advance(issue_date, pending_events)

                precip_7d: dict[str, tuple[str, str]] = {}
                for grid_id in grid:
                    precip_values = [
                        parse_float(weather[grid_id]["cwfis_precip_mm"])
                        for _, weather in weather_window
                    ]
                    available = [value for value in precip_values if value is not None]
                    precip_7d[grid_id] = (
                        "" if len(available) != 7 else f"{sum(available):.6f}",
                        str(len(available)),
                    )

                for label in label_rows:
                    grid_id = label["grid_id"]
                    weather = dict(today_weather[grid_id])
                    if weather["cwfis_observation_date"] >= issue_date.isoformat():
                        weather_date_violations += 1
                    if weather["cwfis_missing"] == "1":
                        weather_missing_rows += 1
                    else:
                        weather_available_rows += 1
                    row = dict(label)
                    row.update(calendar_features(issue_date, grid[grid_id].latitude))
                    row.update(
                        {
                            "feature_set_version": FEATURE_SET_VERSION,
                            **{field: terrain[grid_id][field] for field in TERRAIN_FIELDS if field not in {"grid_id", "terrain_feature_version"}},
                            **weather,
                            "cwfis_precip_7d_mm": precip_7d[grid_id][0],
                            "cwfis_precip_7d_coverage_days": precip_7d[grid_id][1],
                            "fire_history_source_id": "nfdb_ontario_ignition_labels",
                            **history.values(grid_id, issue_date),
                        }
                    )
                    key = (row["ignition_date"], row["grid_id"])
                    if output_previous_key is not None and key <= output_previous_key:
                        raise AssertionError("Output feature keys are not strictly unique and ordered.")
                    output_previous_key = key
                    writer.writerow(row)
                    row_count += 1
        os.replace(temporary_path, args.output)
    finally:
        temporary_path.unlink(missing_ok=True)

    expected_rows = len(grid) * len(dates)
    if row_count != expected_rows:
        raise AssertionError(f"Output row count changed: expected {expected_rows}, wrote {row_count}")
    terrain_missing_cells = sum(not terrain[grid_id]["terrain_elevation_mean_m"] for grid_id in grid)
    audit = {
        "feature_set_version": FEATURE_SET_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "retrospective_hindcast_not_operational_forecast",
        "inputs": {
            "grid": display_path(args.grid),
            "labels": display_path(args.labels),
            "events": display_path(args.events),
            "terrain": display_path(args.terrain),
            "cwfis": display_path(args.cwfis),
            "cwfis_manifest": None if cwfis_manifest is None else display_path(args.cwfis_manifest),
            "source_registry": display_path(args.source_registry),
        },
        "date_coverage": {
            "panel_start": dates[0].isoformat(),
            "panel_end": dates[-1].isoformat(),
            "panel_dates": len(dates),
            "cwfis_required_observation_start": min(required_source_dates).isoformat(),
            "cwfis_required_observation_end": max(required_source_dates).isoformat(),
        },
        "counts": {
            "input_label_rows": profile.row_count,
            "output_rows": row_count,
            "expected_selected_rows": expected_rows,
            "grid_cells": len(grid),
            "cwfis_station_catalog": len(stations),
            "cwfis_station_candidates_per_cell": args.station_count,
            "selected_event_records": sum(len(values) for values in events_by_date.values()),
        },
        "missingness": {
            "cwfis_rows_missing": weather_missing_rows,
            "cwfis_rows_available": weather_available_rows,
            "terrain_elevation_missing_rows": terrain_missing_cells * len(dates),
        },
        "validation": {
            "input_row_universe_validated": True,
            "output_row_count_matches_selected_panel": row_count == expected_rows,
            "input_and_output_key_uniqueness_validated": True,
            "terrain_grid_alignment_validated": True,
            "cwfis_observation_before_issue_violations": weather_date_violations,
            "past_fire_history_includes_issue_or_future_date": False,
            "prohibited_spread_or_post_event_predictors_included": False,
        },
        "source_semantics": {
            "cwfis": "CWFIS archive values are t-1 retrospective hindcast features; per-record available-at timestamps are unavailable.",
            "fire_history": "Only label-source records dated strictly before the issue date are counted; source reporting availability is retrospective.",
            "terrain": "CDEM is static terrain with vintage end year 2011; no land-cover, fuel, road, settlement, perimeter, or burn-product proxy is substituted.",
        },
    }
    if weather_date_violations:
        raise AssertionError("Leakage boundary failure: CWFIS observation date is not strictly before issue date.")
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--terrain", type=Path, default=DEFAULT_TERRAIN)
    parser.add_argument("--cwfis", type=Path, default=DEFAULT_CWFIS)
    parser.add_argument("--cwfis-manifest", type=Path, default=DEFAULT_CWFIS_MANIFEST)
    parser.add_argument("--source-registry", type=Path, default=DEFAULT_SOURCE_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--start-date", help="Optional inclusive panel date for a reproducibility subset.")
    parser.add_argument("--end-date", help="Optional inclusive panel date for a reproducibility subset.")
    parser.add_argument("--station-count", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.station_count < 1:
        raise ValueError("--station-count must be positive.")
    audit = build(args)
    print(f"Wrote {audit['counts']['output_rows']:,} feature rows: {args.output}")
    print(f"Wrote feature audit: {args.audit}")


if __name__ == "__main__":
    main()
