"""Cache / staleness levels from contracts/cache_fallback.md (v0.2.0), as pure functions.

Two separate ages are judged and ``stale_level`` is the WORSE of the two:

1. Fetch health, from ``retrieved_at`` (when our poller last succeeded):
   fresh <= 30 min, stale <= 6 h, expired beyond (:class:`CachePolicy`).
2. Data age, from ``valid_time`` (observations) or ``source_run_time`` (model
   runs such as HRDPS), with PER-SOURCE thresholds (:data:`DATA_AGE_THRESHOLDS`).

All thresholds are in this file. Fetch-health values are the proposals from
cache_fallback.md. Per-source data-age values are **TBD placeholders**: they come
from single snapshots seen on 2026-11-02 (HRDPS run ~5.5 h old; CWFIS station
report ~11.5 h old; swob ~1 min old), NOT from a measured cadence. Team Lead
reviews RELAY's 24 h cadence measurement (/workspace/relay-evidence/feed_cadence.jsonl)
before they are set. Nothing here does I/O or reads the clock; callers pass ``now``.

    no_cache  nothing cached at all -> HTTP 503 (never invent values)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class StaleLevel(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"
    NO_CACHE = "no_cache"


_ORDER = {StaleLevel.FRESH: 0, StaleLevel.STALE: 1, StaleLevel.EXPIRED: 2, StaleLevel.NO_CACHE: 3}


def worst(*levels: StaleLevel) -> StaleLevel:
    """Most degraded of the given levels."""
    return max(levels, key=_ORDER.__getitem__)


@dataclass(frozen=True)
class AgeThresholds:
    """fresh if age <= fresh_max; stale if <= stale_max; expired beyond."""

    fresh_max_minutes: float
    stale_max_minutes: float

    def __post_init__(self):
        if not (0 < self.fresh_max_minutes < self.stale_max_minutes):
            raise ValueError("require 0 < fresh_max_minutes < stale_max_minutes")


# TBD PLACEHOLDERS (see module docstring). Keys are the source names used by the
# ingestion status endpoint. Replace after the 24 h cadence review.
DATA_AGE_THRESHOLDS: Mapping[str, AgeThresholds] = MappingProxyType({
    "hrdps_geomet_wms": AgeThresholds(480, 1080),          # TBD: 6-hourly runs; seen 332 min old at probe 0
    "cwfis_firewx_stns_current": AgeThresholds(900, 2160),  # TBD: seen 692 min old at probe 0
    "msc_swob_realtime": AgeThresholds(90, 360),           # TBD: minute-level obs, seen 1.4 min old
})
DATA_AGE_THRESHOLDS_TBD = True


@dataclass(frozen=True)
class CachePolicy:
    """Fetch-health thresholds and operating knobs, one place. PROPOSAL values (v0.2.0 draft)."""

    PROPOSAL = True  # class-level marker: values below are proposals, not measured

    poll_interval_minutes: float = 15.0
    fetch_timeout_seconds: float = 20.0
    fresh_max_minutes: float = 30.0               # two missed polls
    stale_max_minutes: float = 360.0              # 6 h
    alert_after_consecutive_stale_polls: int = 3  # operator alert (Team Lead to approve)
    clock_skew_tolerance_minutes: float = 5.0     # a timestamp slightly in the future is clamped to 0
    data_age: Mapping[str, AgeThresholds] = field(default_factory=lambda: DATA_AGE_THRESHOLDS)
    # Used for a source with no entry in data_age (conservative: same as fetch health).
    default_data_age: AgeThresholds | None = None

    def __post_init__(self):
        if not (0 < self.fresh_max_minutes < self.stale_max_minutes):
            raise ValueError("require 0 < fresh_max_minutes < stale_max_minutes")

    @property
    def fetch_thresholds(self) -> AgeThresholds:
        return AgeThresholds(self.fresh_max_minutes, self.stale_max_minutes)

    def thresholds_for(self, source: str) -> AgeThresholds:
        return self.data_age.get(source) or self.default_data_age or self.fetch_thresholds


DEFAULT_POLICY = CachePolicy()


def age_minutes(now: datetime, since: datetime, policy: CachePolicy = DEFAULT_POLICY) -> float:
    """Minutes from ``since`` to ``now``. Small negative skew clamps to 0; a larger
    one means the timestamp is untrustworthy and raises ValueError."""
    if now.tzinfo is None or since.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    minutes = (now - since) / timedelta(minutes=1)
    if minutes < 0:
        if -minutes <= policy.clock_skew_tolerance_minutes:
            return 0.0
        raise ValueError("timestamp is in the future beyond clock-skew tolerance")
    return minutes


def level_for_age(age_min: float, thresholds: "CachePolicy | AgeThresholds" = DEFAULT_POLICY) -> StaleLevel:
    if age_min < 0:
        raise ValueError("age must be >= 0")
    if age_min <= thresholds.fresh_max_minutes:
        return StaleLevel.FRESH
    if age_min <= thresholds.stale_max_minutes:
        return StaleLevel.STALE
    return StaleLevel.EXPIRED


@dataclass(frozen=True)
class Decision:
    level: StaleLevel                 # worse of fetch_level and data_level (maps to stale_level)
    age_minutes: float | None         # age of the oldest input that sets `level` (maps to age_minutes)
    stale_flag: bool                  # == (level != fresh); maps to stale_flag
    serve: bool                       # False -> respond 503 (no cache at all)
    degraded: bool                    # expired: spread runs labelled degraded, user must confirm
    reason: str | None                # human-readable, no secrets
    fetch_level: StaleLevel | None = None
    fetch_age_minutes: float | None = None
    data_level: StaleLevel | None = None
    data_age_minutes: float | None = None


def decide(now: datetime, retrieved_at: datetime | None, *, source: str = "feed",
           kind: str = "observation", valid_time: datetime | None = None,
           source_run_time: datetime | None = None,
           policy: CachePolicy = DEFAULT_POLICY) -> Decision:
    """Two-age serving decision for one cached record from one ``source``.

    ``retrieved_at=None`` means the feed never succeeded -> no_cache / 503.
    Observations: data age is measured from ``valid_time``.
    Forecasts / model runs: data age is measured from ``source_run_time``, and a
    ``valid_time`` already in the past makes the record an observation substitute,
    flagged at least ``stale`` (cache_fallback.md).
    A missing data time means only fetch health is judged.
    """
    if kind not in ("observation", "forecast"):
        raise ValueError("kind must be 'observation' or 'forecast'")
    if retrieved_at is None:
        return Decision(StaleLevel.NO_CACHE, None, True, False, True,
                        f"{source}: no successful fetch has ever been cached")
    fetch_age = age_minutes(now, retrieved_at, policy)
    fetch_level = level_for_age(fetch_age, policy.fetch_thresholds)

    data_time = valid_time if kind == "observation" else source_run_time
    data_age = data_level = None
    if data_time is not None:
        data_age = age_minutes(now, data_time, policy)
        data_level = level_for_age(data_age, policy.thresholds_for(source))
    if kind == "forecast" and valid_time is not None and valid_time < now:
        data_level = worst(data_level or StaleLevel.FRESH, StaleLevel.STALE)

    level = worst(fetch_level, data_level or StaleLevel.FRESH)
    # age of the input that sets the level; if both agree (or fresh) report the older one
    if data_age is None or (_ORDER[fetch_level] > _ORDER[data_level or StaleLevel.FRESH]):
        age = fetch_age
    elif _ORDER[data_level or StaleLevel.FRESH] > _ORDER[fetch_level]:
        age = data_age
    else:
        age = max(fetch_age, data_age)
    flag = level is not StaleLevel.FRESH
    if not flag:
        reason = None
    else:
        which = []
        if fetch_level is not StaleLevel.FRESH:
            which.append(f"fetch {fetch_level.value} ({fetch_age:.0f} min since last fetch)")
        if data_level is not None and data_level is not StaleLevel.FRESH:
            which.append(f"data {data_level.value}" + (f" ({data_age:.0f} min old)" if data_age is not None else ""))
        reason = f"{source}: " + "; ".join(which)
    return Decision(level, age, flag, True, level is StaleLevel.EXPIRED, reason,
                    fetch_level, fetch_age, data_level, data_age)


def combine(decisions: "list[Decision]") -> Decision:
    """Worst decision across the feeds behind one output (the one that sets stale_level)."""
    if not decisions:
        raise ValueError("no decisions to combine")
    pick = max(decisions, key=lambda d: (_ORDER[d.level], d.age_minutes or 0.0))
    reasons = "; ".join(d.reason for d in decisions if d.reason)
    return Decision(pick.level, pick.age_minutes, pick.stale_flag, all(d.serve for d in decisions),
                    pick.degraded, reasons or None, pick.fetch_level, pick.fetch_age_minutes,
                    pick.data_level, pick.data_age_minutes)


def should_alert(level: StaleLevel, consecutive_stale_polls: int,
                 policy: CachePolicy = DEFAULT_POLICY) -> bool:
    """Operator alert rule: stale for more than N consecutive polls, or expired/no_cache."""
    if level in (StaleLevel.EXPIRED, StaleLevel.NO_CACHE):
        return True
    return level is StaleLevel.STALE and consecutive_stale_polls > policy.alert_after_consecutive_stale_polls
