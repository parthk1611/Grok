"""Timestamp sanity checks for upstream source times.

Why: the live MSC ``swob-realtime`` collection returned a record dated
2101-06-16 when sorted by date (RELAY recon, 2026-11-02). Ingestion must never
let an implausible source time become "newest data". Pure functions, no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


class ImplausibleTimestamp(ValueError):
    """Raised when a source time is malformed, in the far past, or in the future."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class TimestampPolicy:
    """Bounds for plausible source times. Values are proposals (RELAY)."""

    # Allow small clock skew between the upstream and us.
    max_future: timedelta = timedelta(hours=2)
    # Nothing before this can be a live feed time (live feed is 2020s data).
    min_time: datetime = datetime(2000, 1, 1, tzinfo=timezone.utc)


DEFAULT_POLICY = TimestampPolicy()


def parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 UTC timestamp with a ``Z`` suffix (the contract format)."""
    if not isinstance(value, str) or not _ISO_UTC.match(value):
        raise ImplausibleTimestamp("malformed", "timestamp must be ISO 8601 UTC with a Z suffix")
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ImplausibleTimestamp("malformed", f"invalid timestamp: {exc}") from exc


def check_source_time(value: "str | datetime", now: datetime,
                      policy: TimestampPolicy = DEFAULT_POLICY) -> datetime:
    """Return the parsed aware-UTC time, or raise :class:`ImplausibleTimestamp`.

    Rejects: malformed or naive values, times before ``policy.min_time``, and
    times more than ``policy.max_future`` after ``now``.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ImplausibleTimestamp("naive", "timestamp has no timezone")
        ts = value.astimezone(timezone.utc)
    else:
        ts = parse_utc(value)
    if ts < policy.min_time:
        raise ImplausibleTimestamp("too_old", f"timestamp {ts.isoformat()} is before {policy.min_time.date()}")
    if ts > now + policy.max_future:
        raise ImplausibleTimestamp("in_future", f"timestamp {ts.isoformat()} is more than {policy.max_future} in the future")
    return ts


def is_plausible(value: "str | datetime", now: datetime,
                 policy: TimestampPolicy = DEFAULT_POLICY) -> bool:
    try:
        check_source_time(value, now, policy)
    except ImplausibleTimestamp:
        return False
    return True


def latest_plausible(values, now: datetime, policy: TimestampPolicy = DEFAULT_POLICY):
    """Newest plausible time among ``values`` (implausible ones ignored), or None."""
    best = None
    for v in values:
        try:
            ts = check_source_time(v, now, policy)
        except ImplausibleTimestamp:
            continue
        if best is None or ts > best:
            best = ts
    return best
