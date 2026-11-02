"""Common error model for 400/404/503 (and 500). Body shape: ErrorResponse in openapi.yaml."""
from __future__ import annotations

REASONS_503 = ("no_cached_data", "provider_unavailable", "invalid_provider_payload")
REASONS_504 = ("spread_timeout",)


class ApiError(Exception):
    def __init__(self, status: int, reason: str, message: str, details: list | None = None,
                 last_cache_time: str | None = None):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.details = details
        self.last_cache_time = last_cache_time


class NoCachedData(Exception):
    """Provider has never had a successful fetch: the only case that returns 503 by design."""

    def __init__(self, message: str = "No cached data exists yet; no successful ingestion has completed.",
                 last_cache_time: str | None = None):
        super().__init__(message)
        self.last_cache_time = last_cache_time


class NotFound(Exception):
    pass


def error_body(status: int, reason: str, message: str, request_id: str, disclaimer: str,
               details: list | None = None, last_cache_time: str | None = None) -> dict:
    """ErrorResponse in openapi.yaml. `error` is the human message; `last_cache_time` is
    always present (null when nothing was ever cached) as Team Lead required."""
    body = {"status": status, "error": message, "reason": reason, "last_cache_time": last_cache_time,
            "request_id": request_id, "disclaimer": disclaimer}
    if details:
        body["details"] = details
    return body
