"""FastAPI app factory. Routes follow contracts/openapi.yaml (0.2.1-draft).

Fail-closed ideas ported (re-implemented) from the unmerged branch
parth/iteration-1-prototype, prototype_iteration_1/scripts/capstone_app.py:
strict query validation (exact parameter set, single values, regex plus a real
calendar check), a probability-never-guessed rule (a provider failure or a
schema-invalid payload becomes 503, never a made-up value), and loopback bind by default.
Freshness-by-age and atomic cache writes follow live_workflow.py (see
cache_policy.py and atomic_io.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
import uuid
from datetime import date as _date
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import Settings
from .contracts import ContractViolation, Contracts
from .errors import ApiError, ConfirmationRequired, NoCachedData, NotFound, error_body, retry_after
from .logging_setup import configure_logging, request_id_var
from .providers import DataProvider, MockProvider, Served, SpreadRequest
from .timestamps import ImplausibleTimestamp, TimestampPolicy, check_source_time

log = logging.getLogger("relay.api")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CELL_RE = re.compile(r"^ofr10km_-?\d{1,9}_-?\d{1,9}$")          # matches the contract pattern, bounded digits
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
MAX_BODY_BYTES = 4096
GZIP_MIN_BYTES = 1024
# Manual weather override fields and sanity bounds (same as BEACON's form; contract bounds are wider).
OVERRIDE_FIELDS = {
    "wind_speed_kmh": (0.0, 200.0, True),
    "wind_direction_deg": (0.0, 360.0, True),
    "ffmc": (0.0, 101.0, False),
    "bui": (0.0, 500.0, False),
    "temp_c": (-50.0, 50.0, False),
    "rh_pct": (0.0, 100.0, False),
    "precip_mm": (0.0, 500.0, False),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- request validation helpers (all raise ApiError 400) -------------------

def _query(request: Request, allowed: set[str], required: set[str]) -> dict[str, str]:
    q: dict[str, str] = {}
    for k, v in request.query_params.multi_items():
        if k not in allowed:
            raise ApiError(400, "invalid_request", f"Unknown query parameter '{k}'.")
        if k in q:
            raise ApiError(400, "invalid_request", f"Query parameter '{k}' must appear once.")
        q[k] = v
    missing = sorted(required - q.keys())
    if missing:
        raise ApiError(400, "invalid_request", f"Missing query parameter(s): {', '.join(missing)}.")
    return q


def _parse_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ApiError(400, "invalid_request", "date must be YYYY-MM-DD (local America/Toronto date).")
    try:
        if _date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise ApiError(400, "invalid_request", "date is not a valid calendar date.") from None
    return value


def _parse_cell(value: str) -> str:
    if not CELL_RE.match(value):
        raise ApiError(400, "invalid_request", "cell_id must look like ofr10km_{x0}_{y0}.")
    return value


def _parse_float(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ApiError(400, "invalid_request", f"{name} must be a number.")
    try:
        f = float(value)
    except ValueError:
        raise ApiError(400, "invalid_request", f"{name} must be a number.") from None
    if f != f or f in (float("inf"), float("-inf")):
        raise ApiError(400, "invalid_request", f"{name} must be finite.")
    return f


def _parse_override(value) -> dict:
    if not isinstance(value, dict):
        raise ApiError(400, "invalid_request", "weather_override must be a JSON object.")
    extra = set(value) - OVERRIDE_FIELDS.keys()
    if extra:
        raise ApiError(400, "invalid_request", f"Unknown weather_override field(s): {', '.join(sorted(extra))}.")
    out = {}
    for k, (lo, hi, required) in OVERRIDE_FIELDS.items():
        if k not in value or value[k] is None:
            if required:
                raise ApiError(400, "invalid_request", f"weather_override.{k} is required.")
            continue
        v = value[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
            raise ApiError(400, "invalid_request", f"weather_override.{k} must be a number {lo:g}..{hi:g}.")
        out[k] = v
    return out


def _check_domain(lon: float, lat: float, s: Settings) -> None:
    if not (s.bbox_lon[0] <= lon <= s.bbox_lon[1] and s.bbox_lat[0] <= lat <= s.bbox_lat[1]):
        raise ApiError(400, "out_of_domain",
                       "Point is outside the Ontario coverage box "
                       f"(lon {s.bbox_lon[0]}..{s.bbox_lon[1]}, lat {s.bbox_lat[0]}..{s.bbox_lat[1]}).")


def create_app(settings: Settings | None = None, provider: DataProvider | None = None,
               now_fn=_utcnow) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    contracts = Contracts(settings.contracts_dir)
    if provider is None:
        if settings.data_mode != "mock":
            raise RuntimeError("RELAY_DATA_MODE=live: LiveProvider is not implemented yet (T2.6/T2.7); "
                               "refusing to start rather than serve guesses.")
        mock_clock = (lambda: settings.mock_now) if settings.mock_now else now_fn
        provider = MockProvider(contracts, settings.mock_scenario, now_fn=mock_clock)

    # No generated docs / openapi.json: contracts/openapi.yaml is the contract.
    app = FastAPI(title="COMP-385 wildfire decision-support API", version=__version__,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.provider, app.state.contracts = settings, provider, contracts
    # gzip only when the client sends Accept-Encoding: gzip and the body is >= 1 KiB (ignition lists).
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_BYTES)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Request-ID"],
                           expose_headers=["X-Request-ID", "X-Data-Mode", "X-Mock-Data", "X-Stale-Level",
                                           "X-Data-Timestamp", "Retry-After"])

    def cache_time() -> str | None:
        """Newest cached data time for error bodies; None only when nothing was ever cached."""
        try:
            return provider.last_cache_time()
        except Exception:
            log.exception("last_cache_time lookup failed", extra={"event": "cache_time_failure"})
            return None

    def err_response(status: int, reason: str, message: str, details=None, last_cache_time=None) -> JSONResponse:
        if last_cache_time is None:
            last_cache_time = cache_time()
        body = error_body(status, reason, message, request_id_var.get(), contracts.disclaimer, details,
                          last_cache_time)
        resp = JSONResponse(body, status_code=status)
        ra = retry_after(reason)
        if ra is not None:
            resp.headers["Retry-After"] = str(ra)
        resp.headers["X-Data-Mode"] = provider.mode
        resp.headers["X-Mock-Data"] = "true" if provider.is_mock else "false"
        return resp

    def finish(payload, status: int = 200, served: Served | None = None) -> JSONResponse:
        resp = JSONResponse(payload, status_code=status)
        resp.headers["X-Data-Mode"] = provider.mode
        resp.headers["X-Mock-Data"] = "true" if provider.is_mock else "false"
        if served is not None:
            resp.headers["X-Stale-Level"] = served.stale_level.value
            if served.data_timestamp:
                resp.headers["X-Data-Timestamp"] = served.data_timestamp
        return resp

    def checked(kind: str, served: Served) -> Served:
        """Fail closed: a schema-invalid provider payload is never served."""
        if settings.validate_responses:
            try:
                items = served.payload if isinstance(served.payload, list) else [served.payload]
                for it in items:
                    contracts.validate(kind, it)
            except ContractViolation as exc:
                log.error("provider payload failed contract validation", extra={"event": "contract_violation", "reason": str(exc)})
                raise ApiError(503, "invalid_provider_payload",
                               "The data provider produced a payload that violates the contract; nothing is served.") from None
        return served

    def call(fn, *a):
        try:
            return fn(*a)
        except NoCachedData as exc:
            raise ApiError(503, "no_cached_data", str(exc), last_cache_time=exc.last_cache_time) from None
        except NotFound as exc:
            raise ApiError(404, "not_found", str(exc)) from None
        except ConfirmationRequired as exc:
            raise ApiError(409, "confirmation_required", str(exc)) from None
        except ApiError:
            raise
        except Exception:
            log.exception("provider failure", extra={"event": "provider_failure"})
            raise ApiError(503, "provider_unavailable", "The data provider failed; nothing is served.") from None

    # ---- middleware: request IDs + structured access log --------------------
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID", "")
        rid = rid if REQUEST_ID_RE.match(rid) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        request.state.request_id = rid
        t0 = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                log.exception("unhandled error", extra={"event": "unhandled"})
                response = err_response(500, "internal_error", "Internal error; nothing is served.")
            response.headers["X-Request-ID"] = rid
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            log.info("request", extra={
                "event": "access", "method": request.method, "path": request.url.path,
                "status": response.status_code, "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                "data_mode": provider.mode, "stale_level": response.headers.get("X-Stale-Level")})
            return response
        finally:
            request_id_var.reset(token)

    # ---- error handlers (one error model) -----------------------------------
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return err_response(exc.status, exc.reason, exc.message, exc.details, exc.last_cache_time)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        known = {404: "route_not_found", 405: "method_not_allowed"}
        if exc.status_code in known:
            return err_response(exc.status_code, known[exc.status_code], str(exc.detail))
        if exc.status_code < 500:      # closed reason enum: any other client error is invalid_request
            return err_response(400, "invalid_request", str(exc.detail))
        return err_response(500, "internal_error", "Internal error; nothing is served.")

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return err_response(400, "invalid_request", "Request validation failed.")

    # ---- routes -------------------------------------------------------------
    @app.get("/healthz")
    async def healthz():
        return finish({"status": "ok", "mode": provider.mode, "mock": provider.is_mock,
                       "contract_version": contracts.contract_version, "api_version": __version__,
                       "time": _iso(now_fn())})

    def _ignition_date(value: str) -> str:
        return "latest" if value == "latest" else _parse_date(value)

    @app.get("/api/v1/ignition/dates")
    async def ignition_dates(request: Request):
        _query(request, set(), set())
        dates = call(provider.ignition_dates)
        return finish({"dates": dates, "latest": dates[-1] if dates else None, "mock": provider.is_mock,
                       "generated_at": _iso(now_fn()), "api_version": __version__,
                       "disclaimer": contracts.disclaimer})

    @app.get("/api/v1/ignition")
    async def ignition_list(request: Request):
        q = _query(request, {"date", "top_k", "envelope"}, {"date"})
        day = _ignition_date(q["date"])
        top_k = None
        if "top_k" in q:
            if not re.fullmatch(r"\d{1,5}", q["top_k"]) or not 1 <= int(q["top_k"]) <= 10000:
                raise ApiError(400, "invalid_request", "top_k must be an integer 1..10000.")
            top_k = int(q["top_k"])
        envelope = q.get("envelope", "false")
        if envelope not in ("true", "false"):
            raise ApiError(400, "invalid_request", "envelope must be true or false.")
        served = checked("ignition", call(provider.ignition_for_date, day, top_k))
        if envelope == "false":
            return finish(served.payload, served=served)
        recs = served.payload
        body = {"mode": recs[0]["mode"] if recs else "retrospective",
                "stale_level": served.stale_level.value,
                "age_minutes": max((r["age_minutes"] for r in recs), default=0),
                "data_timestamp": served.data_timestamp,
                "requested_date": day}
        if provider.is_mock:
            body["mock"] = True
        body["records"] = recs
        return finish(body, served=served)

    @app.get("/api/v1/ignition/{cell_id}")
    async def ignition_cell(cell_id: str, request: Request):
        cell = _parse_cell(cell_id)
        q = _query(request, {"date"}, {"date"})
        served = checked("ignition", call(provider.ignition_for_cell, cell, _ignition_date(q["date"])))
        return finish(served.payload, served=served)

    @app.post("/api/v1/spread")
    async def spread(request: Request):
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise ApiError(400, "invalid_request", f"Request body exceeds {MAX_BODY_BYTES} bytes.")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "invalid_request", "Body must be valid JSON.") from None
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_request", "Body must be a JSON object.")
        extra = set(body) - {"longitude", "latitude", "start_time", "n_runs", "seed", "confirm_degraded",
                             "weather_override"}
        if extra:
            raise ApiError(400, "invalid_request", f"Unknown field(s): {', '.join(sorted(extra))}.")
        for f in ("longitude", "latitude", "start_time"):
            if f not in body:
                raise ApiError(400, "invalid_request", f"Missing field: {f}.")
        if isinstance(body["longitude"], str) or isinstance(body["latitude"], str):
            raise ApiError(400, "invalid_request", "longitude and latitude must be JSON numbers.")
        lon, lat = _parse_float(body["longitude"], "longitude"), _parse_float(body["latitude"], "latitude")
        _check_domain(lon, lat, settings)
        policy = TimestampPolicy(max_future=timedelta(hours=settings.spread_start_max_future_hours))
        try:
            start = check_source_time(body["start_time"], now_fn(), policy)
        except ImplausibleTimestamp as exc:
            raise ApiError(400, "invalid_request", f"start_time rejected ({exc.reason}): ISO 8601 UTC with Z, "
                           f"2000-01-01 .. now+{settings.spread_start_max_future_hours:g} h.") from None
        n_runs, seed = settings.spread_default_runs, secrets.randbelow(2**31)   # seed is always echoed
        if "n_runs" in body:
            v = body["n_runs"]
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= settings.spread_max_runs:
                raise ApiError(400, "invalid_request", f"n_runs must be an integer 1..{settings.spread_max_runs}.")
            n_runs = v
        if "seed" in body:
            v = body["seed"]
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 2**31 - 1:
                raise ApiError(400, "invalid_request", "seed must be an integer 0..2147483647.")
            seed = v
        confirm = body.get("confirm_degraded", False)
        if not isinstance(confirm, bool):
            raise ApiError(400, "invalid_request", "confirm_degraded must be a JSON boolean.")
        override = None
        if body.get("weather_override") is not None:
            override = _parse_override(body["weather_override"])
        # Synchronous run with a hard limit (Team Lead: 60 s; BLAZE measured ~4 s / 200 synthetic runs).
        # The 200 body is the bare spread_output contract, so an async job+poll mode can be added later
        # (POST ... with `Prefer: respond-async` -> 202 + job URL) without changing this response.
        # NOTE: a timed-out worker thread cannot be killed; the client gets 504 and the result is dropped.
        try:
            served = await asyncio.wait_for(
                asyncio.to_thread(call, provider.run_spread,
                                  SpreadRequest(lon, lat, start, n_runs, seed, confirm, override)),
                timeout=settings.spread_timeout_seconds)
        except asyncio.TimeoutError:
            log.error("spread run exceeded time limit", extra={"event": "spread_timeout"})
            raise ApiError(504, "spread_timeout",
                           f"The spread run did not finish within {settings.spread_timeout_seconds:g} s; "
                           "nothing is served.") from None
        served = checked("spread", served)
        return finish(served.payload, served=served)

    @app.get("/api/v1/weather/latest")
    async def weather_latest(request: Request):
        q = _query(request, {"lat", "lon", "cell_id"}, set())
        by_point = {"lat", "lon"} & q.keys()
        if "cell_id" in q and by_point:
            raise ApiError(400, "invalid_request", "Use either cell_id or lat+lon, not both.")
        if "cell_id" in q:
            served = call(provider.latest_weather, None, None, _parse_cell(q["cell_id"]))
        elif by_point == {"lat", "lon"}:
            lat, lon = _parse_float(q["lat"], "lat"), _parse_float(q["lon"], "lon")
            _check_domain(lon, lat, settings)
            served = call(provider.latest_weather, lat, lon, None)
        else:
            raise ApiError(400, "invalid_request", "Provide cell_id, or both lat and lon.")
        return finish(checked("weather", served).payload, served=served)

    @app.get("/api/v1/ingestion/status")
    async def ingestion_status():
        payload = call(provider.ingestion_status, now_fn())
        payload = {**payload, "generated_at": _iso(now_fn())}
        return finish(payload)

    return app


def get_app() -> FastAPI:  # `uvicorn backend.app.main:get_app --factory`
    return create_app()
