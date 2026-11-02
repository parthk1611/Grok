"""Time and grid alignment helpers (ATLAS data lane, alignment v1).

Rules implemented here (Team Lead, 2026-11-02; see ``contracts/grid_spec.md``):

* Civil time is ``America/Toronto`` *with* daylight saving for every cell.
* A label window starts at local midnight and ends at the next local midnight
  (half-open), so it is 23, 24 or 25 hours long.
* Raw timestamps are stored in UTC; the local date is *derived* from them.
  No function here uses a hard-coded UTC offset.  The only offset arithmetic is
  ECCC "LST" (local standard time), whose standard offset is read from the IANA
  zone rules (``utcoffset() - dst()``), never typed in.
* Dynamic inputs must be available strictly before the target day's local
  midnight (the t-1 rule used by the ignition panel).

Everything is stdlib-only except ``lonlat_to_grid_id`` which imports ``pyproj``
lazily.

TODO(contracts/v0): the grid constants below mirror ``contracts/grid_spec.md``
(grid_version ``ofr10km-v1``).  When Team Lead changes that spec, update these
constants and ``tests/test_source_alignment.py`` together; do not edit the
contract files from this lane.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LOCAL_TZ_NAME = "America/Toronto"
LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
UTC = timezone.utc

# --- grid constants (mirror contracts/grid_spec.md, grid_version ofr10km-v1) ---
GRID_VERSION = "ofr10km-v1"
GRID_CRS = "EPSG:3978"
GRID_CELL_M = 10_000
GRID_PATH = PROJECT_ROOT / "data/interim/ontario_fire_region_10km_grid.csv"
GRID_ID_PATTERN = re.compile(r"^ofr10km_-?[0-9]+_-?[0-9]+$")  # same as live_weather_record.cell_id
# Same shape the live_weather_record schema requires for valid_time/retrieved_at.
UTC_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


class AlignmentError(ValueError):
    """A timestamp, key or grid value cannot be aligned safely."""


class LeakageError(AlignmentError):
    """A row or observation would not have been available at issue time."""


# --------------------------------------------------------------------------- UTC
def _require_aware(value: datetime, name: str = "timestamp") -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AlignmentError(f"{name} is naive; store raw timestamps as timezone-aware UTC")
    return value


def to_utc(value: datetime) -> datetime:
    """Return an aware datetime converted to UTC (naive input is rejected)."""
    return _require_aware(value).astimezone(UTC)


def parse_utc(text: str) -> datetime:
    """Parse an ISO 8601 string that carries ``Z`` or an explicit offset into UTC.

    Strings without a zone are rejected rather than guessed.
    """
    if not isinstance(text, str):
        raise TypeError("timestamp text must be a string")
    cleaned = text.strip()
    if cleaned.endswith(("Z", "z")):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise AlignmentError(f"unparseable timestamp {text!r}") from exc
    if parsed.tzinfo is None:
        raise AlignmentError(f"timestamp {text!r} has no zone; refusing to assume UTC")
    return parsed.astimezone(UTC)


def format_utc_z(value: datetime) -> str:
    """ISO 8601 UTC with a ``Z`` suffix, as the contracts require."""
    text = to_utc(value).strftime("%Y-%m-%dT%H:%M:%S")
    assert UTC_Z_PATTERN.match(text + "Z")
    return text + "Z"


# ----------------------------------------------------------------- local dates
def utc_to_local_date(value: datetime) -> date:
    """The ``America/Toronto`` civil date containing a UTC instant."""
    return to_utc(value).astimezone(LOCAL_TZ).date()


def local_midnight_utc(day: date) -> datetime:
    """UTC instant of 00:00 ``America/Toronto`` on ``day`` (DST aware).

    Toronto's transitions happen at 02:00, so local midnight always exists and is
    unambiguous.
    """
    if isinstance(day, datetime):
        raise TypeError("pass a date, not a datetime")
    local = datetime(day.year, day.month, day.day, tzinfo=LOCAL_TZ)
    result = local.astimezone(UTC)
    # Guard the assumption above instead of silently trusting it.
    if result.astimezone(LOCAL_TZ).replace(tzinfo=None) != datetime(day.year, day.month, day.day):
        raise AlignmentError(f"local midnight of {day} does not exist")
    return result


def issue_time_utc(target_date: date) -> datetime:
    """The panel's issue time: local midnight starting ``target_date``."""
    return local_midnight_utc(target_date)


def label_window_utc(target_date: date) -> tuple[datetime, datetime]:
    """Half-open ``[start, end)`` UTC window of one local label day."""
    return local_midnight_utc(target_date), local_midnight_utc(target_date + timedelta(days=1))


def label_window_hours(target_date: date) -> float:
    start, end = label_window_utc(target_date)
    return (end - start).total_seconds() / 3600.0


def in_label_window(value: datetime, target_date: date) -> bool:
    start, end = label_window_utc(target_date)
    return start <= to_utc(value) < end


# ------------------------------------------------------------- availability/t-1
def assert_t_minus_1_cutoff(observation_date: date, target_date: date) -> None:
    """Reject an observation dated on or after the target date (needs t-1 or earlier)."""
    if observation_date > target_date - timedelta(days=1):
        kind = "same-day" if observation_date == target_date else "future"
        raise LeakageError(
            f"{kind} observation {observation_date} cannot inform target {target_date}; "
            f"latest allowed is {target_date - timedelta(days=1)}"
        )


def assert_available_before_issue(available_at_utc: datetime, target_date: date) -> None:
    """Require an observation's availability instant to be <= the issue instant.

    ``available_at_utc`` for an interval observation is the interval *end*.
    """
    available = to_utc(available_at_utc)
    issue = issue_time_utc(target_date)
    if available > issue:
        raise LeakageError(
            f"available at {format_utc_z(available)} which is after issue time "
            f"{format_utc_z(issue)} for {target_date}"
        )


def assert_rows_before_cutoff(
    rows: Iterable[Mapping[str, Any]],
    observation_key: str,
    target_key: str,
    *,
    max_reported: int = 5,
) -> int:
    """Check every row's observation date is <= its target date - 1 day.

    Values may be ``date`` objects or ``YYYY-MM-DD`` strings.  Returns the number
    of rows checked and raises ``LeakageError`` listing the first offenders.
    """
    offenders: list[str] = []
    count = 0
    for index, row in enumerate(rows):
        count += 1
        observed = _as_date(row[observation_key])
        target = _as_date(row[target_key])
        try:
            assert_t_minus_1_cutoff(observed, target)
        except LeakageError as exc:
            offenders.append(f"row {index}: {exc}")
    if offenders:
        raise LeakageError(f"{len(offenders)} of {count} rows violate t-1; " + "; ".join(offenders[:max_reported]))
    return count


def assert_no_future_rows(
    rows: Iterable[Mapping[str, Any]],
    timestamp_key: str,
    target_date: date,
    *,
    max_reported: int = 5,
) -> int:
    """Every row's UTC ``timestamp_key`` must be before the target day's local midnight."""
    issue = issue_time_utc(target_date)
    offenders: list[str] = []
    count = 0
    for index, row in enumerate(rows):
        count += 1
        stamp = row[timestamp_key]
        stamp = parse_utc(stamp) if isinstance(stamp, str) else to_utc(stamp)
        if stamp >= issue:
            offenders.append(f"row {index}: {format_utc_z(stamp)}")
    if offenders:
        raise LeakageError(
            f"{len(offenders)} of {count} rows are at or after issue {format_utc_z(issue)}: "
            + "; ".join(offenders[:max_reported])
        )
    return count


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        raise TypeError("expected a date or YYYY-MM-DD string, not a datetime")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise AlignmentError(f"bad date {value!r}") from exc


# ------------------------------------------------- two-age staleness (cache_fallback)
# contracts/cache_fallback.md v0.2.0 judges staleness on two ages and reports the worse:
#   1. fetch health  = serving time - retrieved_at   (fresh <= 30 min, stale <= 6 h, else expired)
#   2. data age      = serving time - valid_time (observations) or source_run_time (model runs),
#                      with PER-SOURCE thresholds that are still TBD in the contract.
# The fetch thresholds below are the contract's *proposed* defaults.  Data-age thresholds have no
# default on purpose: callers must pass them until Team Lead fixes the numbers.
# TODO(contracts/v0): replace with the approved per-source data-age thresholds when published.
STALE_LEVELS = ("fresh", "stale", "expired")
FETCH_FRESH_MAX_MINUTES = 30.0
FETCH_STALE_MAX_MINUTES = 6 * 60.0


def age_minutes_between(now_utc: datetime, earlier_utc: datetime, what: str = "timestamp") -> float:
    """Minutes from ``earlier_utc`` to ``now_utc``; a timestamp in the future is an error."""
    delta = (to_utc(now_utc) - to_utc(earlier_utc)).total_seconds() / 60.0
    if delta < 0:
        raise AlignmentError(f"{what} {format_utc_z(earlier_utc)} is after serving time {format_utc_z(now_utc)}")
    return delta


def level_for_age(age_minutes: float, fresh_max_minutes: float, stale_max_minutes: float) -> str:
    if fresh_max_minutes > stale_max_minutes:
        raise ValueError("fresh threshold must not exceed stale threshold")
    if age_minutes <= fresh_max_minutes:
        return "fresh"
    return "stale" if age_minutes <= stale_max_minutes else "expired"


def staleness(
    *,
    now_utc: datetime,
    kind: str,
    retrieved_at_utc: datetime,
    valid_time_utc: datetime,
    source_run_time_utc: datetime | None,
    data_age_fresh_max_minutes: float,
    data_age_stale_max_minutes: float,
) -> dict[str, Any]:
    """Two-age staleness for one live weather record (pure; no I/O).

    Data age uses ``source_run_time`` for forecasts (required) and ``valid_time`` for
    observations.  A forecast whose ``valid_time`` has already passed is at least ``stale``
    (cache_fallback.md).  ``age_minutes`` is the age of whichever component set the final
    level (data age on a tie), and both ages are returned for logging.
    """
    if kind not in ("observation", "forecast"):
        raise ValueError("kind must be 'observation' or 'forecast'")
    fetch_age = age_minutes_between(now_utc, retrieved_at_utc, "retrieved_at")
    if kind == "forecast":
        if source_run_time_utc is None:
            raise AlignmentError("a forecast record needs source_run_time to judge data age")
        data_age = age_minutes_between(now_utc, source_run_time_utc, "source_run_time")
    else:
        data_age = age_minutes_between(now_utc, valid_time_utc, "valid_time")
    fetch_level = level_for_age(fetch_age, FETCH_FRESH_MAX_MINUTES, FETCH_STALE_MAX_MINUTES)
    data_level = level_for_age(data_age, data_age_fresh_max_minutes, data_age_stale_max_minutes)
    level = max(fetch_level, data_level, key=STALE_LEVELS.index)
    if kind == "forecast" and to_utc(valid_time_utc) < to_utc(now_utc) and level == "fresh":
        level = "stale"  # past forecast hour is an observation substitute
    age = fetch_age if STALE_LEVELS.index(fetch_level) > STALE_LEVELS.index(data_level) else data_age
    return {
        "stale_level": level,
        "stale_flag": level != "fresh",  # contract: true exactly when stale_level != fresh
        "age_minutes": age,
        "fetch_age_minutes": fetch_age,
        "data_age_minutes": data_age,
        "fetch_level": fetch_level,
        "data_level": data_level,
    }


# ------------------------------------------------------------------------ ECCC
def standard_offset(zone_name: str, day: date) -> timedelta:
    """Local *standard* (non-DST) UTC offset for an IANA zone on ``day``.

    Read from the tz database as ``utcoffset - dst``; nothing is hard-coded.
    """
    zone = ZoneInfo(zone_name)
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=zone)
    dst = noon.dst() or timedelta(0)
    return noon.utcoffset() - dst


def parse_eccc_lst(text: str) -> datetime:
    """Parse an ECCC ``Date/Time (LST)`` value (``YYYY-MM-DD HH:MM``) as a naive LST wall time."""
    cleaned = text.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    raise AlignmentError(f"unparseable ECCC LST value {text!r}")


def eccc_lst_to_utc(lst_wall_time: datetime, station_zone: str = LOCAL_TZ_NAME) -> datetime:
    """Convert an ECCC local-standard-time wall clock to UTC.

    ECCC bulk CSV ``Date/Time (LST)`` and API ``LOCAL_DATE`` are local *standard*
    time, which does not shift for DST, so in summer they run one hour behind
    ``America/Toronto`` civil time.  ``station_zone`` defaults to the project zone;
    a Central-time station would pass its own IANA zone (open item in the
    contract: per-cell zones are not defined).
    """
    if lst_wall_time.tzinfo is not None:
        raise AlignmentError("LST wall time must be naive; it is defined by the station zone")
    offset = standard_offset(station_zone, lst_wall_time.date())
    return (lst_wall_time - offset).replace(tzinfo=UTC)


def eccc_lst_day_window_utc(lst_date: date, station_zone: str = LOCAL_TZ_NAME) -> tuple[datetime, datetime]:
    """Half-open UTC window of an ECCC climate day (LST midnight to next LST midnight)."""
    start = eccc_lst_to_utc(datetime(lst_date.year, lst_date.month, lst_date.day), station_zone)
    return start, start + timedelta(hours=24)


def eccc_daily_available_by_issue(lst_date: date, target_date: date, station_zone: str = LOCAL_TZ_NAME) -> bool:
    """Is an ECCC daily summary for ``lst_date`` complete by the target issue time?

    In daylight time the LST day ends at 01:00 local civil time, i.e. *after* the
    local-midnight issue time, so the t-1 daily record is not safe in summer; hourly
    observations up to the issue instant must be aggregated instead.
    """
    return eccc_lst_day_window_utc(lst_date, station_zone)[1] <= issue_time_utc(target_date)


def eccc_hourly_utc(properties: Mapping[str, Any], station_zone: str = LOCAL_TZ_NAME) -> datetime:
    """UTC instant of an ``api.weather.gc.ca`` climate-hourly feature.

    Uses ``UTC_DATE`` when present and cross-checks it against ``LOCAL_DATE``
    (LST); a disagreement raises rather than choosing one silently.
    """
    local = properties.get("LOCAL_DATE")
    utc_text = properties.get("UTC_DATE")
    if utc_text:
        # UTC_DATE has no suffix in the API; it is UTC by definition of the field.
        value = datetime.fromisoformat(str(utc_text).replace("Z", "")).replace(tzinfo=UTC)
        if local:
            derived = eccc_lst_to_utc(parse_eccc_lst(str(local)), station_zone)
            if derived != value:
                raise AlignmentError(
                    f"LOCAL_DATE {local} (LST) implies {format_utc_z(derived)} but UTC_DATE is {format_utc_z(value)}"
                )
        return value
    if local:
        return eccc_lst_to_utc(parse_eccc_lst(str(local)), station_zone)
    raise AlignmentError("record has neither UTC_DATE nor LOCAL_DATE")


# ------------------------------------------------------------------------ grid
@dataclass(frozen=True)
class GridCell:
    grid_id: str
    x_m: int
    y_m: int
    centroid_longitude: float
    centroid_latitude: float

    @property
    def centre_xy_m(self) -> tuple[int, int]:
        return self.x_m + GRID_CELL_M // 2, self.y_m + GRID_CELL_M // 2


def grid_id_from_xy(x_m: float, y_m: float) -> str:
    """Cell ID for an EPSG:3978 point: SW corner snapped with ``floor(v / 10000) * 10000``."""
    x0 = int(x_m // GRID_CELL_M) * GRID_CELL_M
    y0 = int(y_m // GRID_CELL_M) * GRID_CELL_M
    return f"ofr10km_{x0}_{y0}"


@lru_cache(maxsize=1)
def _transformer():
    from pyproj import Transformer  # lazy: only grid projection needs it

    return Transformer.from_crs("EPSG:4326", GRID_CRS, always_xy=True)


def lonlat_to_grid_id(longitude: float, latitude: float) -> str:
    """Project WGS84 lon/lat to EPSG:3978 (needs ``pyproj``) and return the cell ID.

    The ID can name a cell outside the current 9,673-cell grid; join against the
    grid to test membership.
    """
    x_m, y_m = _transformer().transform(longitude, latitude)
    return grid_id_from_xy(x_m, y_m)


def load_grid(path: Path | str = GRID_PATH) -> dict[str, GridCell]:
    """Read the authoritative grid CSV and validate IDs against the ID scheme."""
    grid: dict[str, GridCell] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for line in csv.DictReader(handle):
            cell = GridCell(
                grid_id=line["grid_id"],
                x_m=int(line["grid_x_m"]),
                y_m=int(line["grid_y_m"]),
                centroid_longitude=float(line["centroid_longitude"]),
                centroid_latitude=float(line["centroid_latitude"]),
            )
            if not GRID_ID_PATTERN.match(cell.grid_id) or cell.grid_id != f"ofr10km_{cell.x_m}_{cell.y_m}":
                raise AlignmentError(f"grid_id {cell.grid_id!r} does not match its origin ({cell.x_m}, {cell.y_m})")
            if cell.x_m % GRID_CELL_M or cell.y_m % GRID_CELL_M:
                raise AlignmentError(f"{cell.grid_id} is off the {GRID_CELL_M} m lattice")
            if cell.grid_id in grid:
                raise AlignmentError(f"duplicate grid_id {cell.grid_id}")
            grid[cell.grid_id] = cell
    return grid


def join_grid(
    rows: Iterable[Mapping[str, Any]],
    grid: Mapping[str, GridCell],
    key: str = "grid_id",
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Left-join rows to grid attributes via ``grid_id``; input rows are not modified.

    ``strict`` raises on an unknown ``grid_id``; otherwise unknown rows keep ``None``
    grid attributes so the caller can count them.
    """
    joined: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        cell = grid.get(row[key])
        if cell is None and strict:
            raise AlignmentError(f"row {index}: grid_id {row[key]!r} is not in the grid")
        out = dict(row)
        out.update(
            grid_x_m=cell.x_m if cell else None,
            grid_y_m=cell.y_m if cell else None,
            centroid_longitude=cell.centroid_longitude if cell else None,
            centroid_latitude=cell.centroid_latitude if cell else None,
        )
        joined.append(out)
    return joined
