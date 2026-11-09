"""Common error model (ErrorResponse in contracts/openapi.yaml, 0.2.1-draft).

`reason` is a CLOSED enum (REASONS). `last_cache_time` is filled from the cache on every
error and is null only when nothing was ever cached. `Retry-After` (seconds) is sent on 503/504
reasons where a later retry may succeed. The error shape is closed (no extra fields).
"""
from __future__ import annotations

# reason -> (HTTP status, retry may help later, Retry-After seconds or None); only status and
# Retry-After reach the client.
REASONS: dict[str, tuple[int, bool, int | None]] = {
    "invalid_request": (400, False, None),
    "out_of_domain": (400, False, None),
    "not_found": (404, False, None),
    "route_not_found": (404, False, None),
    "method_not_allowed": (405, False, None),
    "confirmation_required": (409, False, None),   # resend with confirm_degraded or weather_override
    "internal_error": (500, False, None),
    "no_cached_data": (503, True, 900),            # next 15-minute poll may fill the cache
    "provider_unavailable": (503, True, 60),
    "invalid_provider_payload": (503, False, None),  # fail closed; needs an operator, not a retry
    "spread_timeout": (504, True, 60),
}
STATUSES = sorted({s for s, _, _ in REASONS.values()})
REASONS_503 = tuple(r for r, (s, _, _) in REASONS.items() if s == 503)
REASONS_504 = ("spread_timeout",)


class ApiError(Exception):
    def __init__(self, status: int, reason: str, message: str, details: list | None = None,
                 last_cache_time: str | None = None):
        if reason not in REASONS:
            raise ValueError(f"reason {reason!r} is not in the closed enum")
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.details = details
        self.last_cache_time = last_cache_time


class NoCachedData(Exception):
    """Provider has never had a successful fetch: the only data case that returns 503 by design."""

    def __init__(self, message: str = "No cached data exists yet; no successful ingestion has completed.",
                 last_cache_time: str | None = None):
        super().__init__(message)
        self.last_cache_time = last_cache_time


class NotFound(Exception):
    pass


class ConfirmationRequired(Exception):
    """Spread inputs are expired and the request has neither confirm_degraded:true nor weather_override."""


def error_body(status: int, reason: str, message: str, request_id: str, disclaimer: str,
               details: list | None = None, last_cache_time: str | None = None) -> dict:
    """ErrorResponse in openapi.yaml. `error` is the human message; `last_cache_time` is
    always present (null only when nothing was ever cached)."""
    body = {"status": status, "error": message, "reason": reason, "last_cache_time": last_cache_time,
            "request_id": request_id, "disclaimer": disclaimer}
    if details:
        body["details"] = details
    return body


def retry_after(reason: str) -> int | None:
    return REASONS[reason][2]
