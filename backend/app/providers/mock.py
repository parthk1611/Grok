"""MockProvider: serves the contracts/examples payloads (contract v0.2.0), clearly labelled MOCK.

Nothing here is a model result or real weather. Payloads keep their
``model_version`` of ``example-not-a-real-model``. Documented, deliberate
adjustments (all visible in the API via the X-Mock-Data header):
  * RELAY_MOCK_SCENARIO overlays stale_flag / stale_level / age_minutes to demo the
    cache_fallback.md levels (fresh, stale, expired) and the 503 no_cached_data path
    (the scenario name no_cache is internal; no response ever carries stale_level "no_cache");
  * the weather example's lat/lon/cell_id and the spread example's polygon
    position follow the request, so a map click lands where the user clicked;
  * a spread request with ``weather_override`` echoes the entered values in ``inputs``
    (weather_source "manual", stale_level "stale", exact MANUAL_STALE_REASON);
  * weather ``valid_time``/``retrieved_at``, spread ``run_timestamp``/``inputs.weather_valid_time``
    and the status source times are derived from the data clock (RELAY_MOCK_NOW when pinned,
    else the server clock): valid_time = clock - scenario age, retrieved_at = valid_time + 5 min,
    run_timestamp = clock, so every timestamp agrees with its age_minutes;
  * an ignition request for a later date than the example gets the example map with its
    original ``date`` and stale_flag true (stale-day rule).
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone

from ..cache_policy import (DATA_AGE_PROVISIONAL, IGNITION_DATA_AGE_SOURCE, age_minutes, level_for_age, worst, DATA_AGE_THRESHOLDS, DATA_AGE_THRESHOLDS_TBD, DEFAULT_POLICY,
                            StaleLevel)
from ..constants import MANUAL_STALE_REASON, MANUAL_WEATHER_SOURCE
from ..contracts import Contracts
from ..errors import ConfirmationRequired, NoCachedData, NotFound
from .base import DataProvider, Served, SpreadRequest

_SCENARIO_LEVEL = {"fresh": StaleLevel.FRESH, "stale": StaleLevel.STALE,
                   "expired": StaleLevel.EXPIRED, "no_cache": StaleLevel.NO_CACHE}
_SCENARIO_AGE_MIN = {"fresh": 10.0, "stale": 120.0, "expired": 720.0}
_SOURCES = ("hrdps", "cwfis_firewx_stns", "swob_realtime")   # SourceStatus.source enum (openapi.yaml)


_RETRIEVE_LAG = timedelta(minutes=5)   # example: retrieved_at - valid_time


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shift(coords, dlon: float, dlat: float):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0] + dlon, 6), round(coords[1] + dlat, 6)]
    return [_shift(c, dlon, dlat) for c in coords]


class MockProvider(DataProvider):
    mode = "mock"
    is_mock = True

    def __init__(self, contracts: Contracts, scenario: str = "fresh", ignition_dates: tuple[str, ...] | None = None,
                 now_fn=None):
        if scenario not in _SCENARIO_LEVEL:
            raise ValueError(f"unknown mock scenario {scenario!r}")
        self.contracts = contracts
        self.scenario = scenario
        self.level = _SCENARIO_LEVEL[scenario]
        self._ign = contracts.example("ignition")
        self._spread = contracts.example("spread")
        self._wx = contracts.example("weather")
        # Local dates the mock "holds" an ignition map for (default: only the example's date).
        self._dates = sorted(set(ignition_dates or (self._ign["date"],)))
        # Clock for data ages: the server clock, or RELAY_MOCK_NOW when pinned (demos/tests).
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # -- helpers ---------------------------------------------------------
    def _require_cache(self):
        if self.level is StaleLevel.NO_CACHE:
            raise NoCachedData("MOCK scenario 'no_cache': no cached data exists (simulated).")

    def _stale_reason(self) -> str | None:
        if self.level in (StaleLevel.FRESH,):
            return None
        return (f"MOCK scenario '{self.scenario}': simulated {self.level.value} feed "
                f"(age {_SCENARIO_AGE_MIN[self.scenario]:.0f} min)")

    def _weather_times(self, now: datetime | None = None) -> tuple[float, str, str]:
        """(age_minutes, valid_time, retrieved_at) for the simulated weather feed on the data clock."""
        now = now or self._now()
        age = _SCENARIO_AGE_MIN.get(self.scenario, _SCENARIO_AGE_MIN["fresh"])
        valid = now - timedelta(minutes=age)
        return age, _iso(valid), _iso(valid + _RETRIEVE_LAG)

    # -- DataProvider ----------------------------------------------------

    def last_cache_time(self) -> str | None:
        if self.level is StaleLevel.NO_CACHE:
            return None
        return max(self._weather_times()[2], self._ign["data_timestamp"])

    def ignition_dates(self) -> list[str]:
        self._require_cache()
        return list(self._dates)

    def _resolve_date(self, requested: str) -> str:
        """Newest held date at or before `requested` ("latest" = newest held date)."""
        if requested == "latest":
            return self._dates[-1]
        held = [d for d in self._dates if d <= requested]
        if not held:
            raise NotFound(f"No ignition map exists at or before {requested} "
                           f"(MockProvider holds {', '.join(self._dates)}).")
        return held[-1]

    def _ignition_record(self, requested: str, served_date: str) -> dict:
        """Data age and level come from the record's data_timestamp against the clock only, so the same
        record has the same age_minutes / stale_level / stale_flag whatever date was requested.
        The stale-day rule only adds stale_flag (and at least `stale`) when the served map is from an
        earlier day than the one requested; it never changes age_minutes."""
        rec = copy.deepcopy(self._ign)
        rec["date"] = served_date
        ts = datetime.fromisoformat(rec["data_timestamp"].replace("Z", "+00:00"))
        age = age_minutes(self._now(), ts)
        level = level_for_age(age, DEFAULT_POLICY.thresholds_for(IGNITION_DATA_AGE_SOURCE))
        reasons = []
        if level is not StaleLevel.FRESH:
            reasons.append(f"input data {level.value}: data_timestamp is {age:.0f} min old")
        if self.level is not StaleLevel.FRESH:
            age = max(age, _SCENARIO_AGE_MIN[self.scenario])
            level = worst(level, self.level)
            reasons.append(self._stale_reason())
        if requested != "latest" and served_date < requested:
            level = worst(level, StaleLevel.STALE)
            reasons.append(f"no map for {requested}; serving the latest map at or before it ({served_date})")
        rec["age_minutes"] = round(age, 1)
        rec["stale_level"] = level.value
        rec["stale_flag"] = level is not StaleLevel.FRESH
        rec["stale_reason"] = "; ".join(reasons) if reasons else None
        return rec

    def ignition_for_date(self, date: str, top_k: int | None) -> Served:
        self._require_cache()
        served_date = self._resolve_date(date)
        recs = [self._ignition_record(date, served_date)]
        recs = recs[: top_k] if top_k else recs
        level = StaleLevel(recs[0]["stale_level"])
        return Served(recs, level, self._ign["data_timestamp"])

    def ignition_for_cell(self, cell_id: str, date: str) -> Served:
        self._require_cache()
        if cell_id != self._ign["cell_id"]:
            raise NotFound(f"MockProvider only holds cell {self._ign['cell_id']}")
        served_date = self._resolve_date(date)
        rec = self._ignition_record(date, served_date)
        return Served(rec, StaleLevel(rec["stale_level"]), self._ign["data_timestamp"])

    def run_spread(self, req: SpreadRequest) -> Served:
        manual = req.weather_override is not None
        if not manual:
            self._require_cache()       # manual inputs need no weather cache
            if self.level is StaleLevel.EXPIRED and not req.confirm_degraded:
                raise ConfirmationRequired(
                    "Spread inputs are expired. Resend with confirm_degraded: true to run on them, "
                    "or with weather_override to enter wind and moisture by hand.")
        out = copy.deepcopy(self._spread)
        out["run_timestamp"] = _iso(self._now())
        ex = out["ignition_point"]
        dlon, dlat = req.longitude - ex["longitude"], req.latitude - ex["latitude"]
        for f in out["features"]:
            f["geometry"]["coordinates"] = _shift(f["geometry"]["coordinates"], dlon, dlat)
        out["ignition_point"] = {"longitude": req.longitude, "latitude": req.latitude}
        start = req.start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        out["start_time"] = start
        if req.n_runs is not None:
            out["n_runs"] = req.n_runs
        if req.seed is not None:
            out["seed"] = req.seed
        if manual:
            ov = req.weather_override
            inp = out["inputs"]
            inp.pop("weather_data_timestamp", None)
            inp.pop("weather_series", None)
            inp.update({
                "weather_source": MANUAL_WEATHER_SOURCE,
                "weather_valid_time": start,
                "wind_speed_kmh": ov["wind_speed_kmh"],
                "wind_direction_deg": ov["wind_direction_deg"],
                **{k: ov.get(k) for k in ("temp_c", "rh_pct", "precip_mm", "ffmc", "bui")},
                "stale_flag": True,
                "stale_reason": MANUAL_STALE_REASON,
            })
            out["stale_level"] = StaleLevel.STALE.value
            out["age_minutes"] = 0
            return Served(out, StaleLevel.STALE, start)
        age, valid, _ = self._weather_times()
        out["inputs"]["weather_valid_time"] = valid
        if self.level is not StaleLevel.FRESH:
            out["stale_level"] = self.level.value
            out["age_minutes"] = age
            out["inputs"]["stale_flag"] = True
            out["inputs"]["stale_reason"] = self._stale_reason()
        return Served(out, self.level, out["inputs"]["weather_valid_time"])

    def latest_weather(self, lat, lon, cell_id) -> Served:
        self._require_cache()
        rec = copy.deepcopy(self._wx)
        if lat is not None and lon is not None:
            rec["latitude"], rec["longitude"] = lat, lon
        if cell_id is not None:
            rec["cell_id"] = cell_id
        rec["quality_flags"] = ["MOCK: example payload, not a real observation"]
        age, rec["valid_time"], rec["retrieved_at"] = self._weather_times()
        rec["age_minutes"] = age
        rec["stale_level"] = self.level.value
        rec["from_cache"] = self.level is not StaleLevel.FRESH
        return Served(rec, self.level, rec["valid_time"])

    def ingestion_status(self, now: datetime) -> dict:
        """MOCK status. Poll times are derived from `now` on the 15-minute grid; ages are the
        scenario's simulated ages; success/source times are derived from `now` minus those ages."""
        p = DEFAULT_POLICY
        none = self.level is StaleLevel.NO_CACHE
        failures = {"fresh": 0, "stale": 4, "expired": 24, "no_cache": 3}[self.scenario]
        step = timedelta(minutes=p.poll_interval_minutes)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        last_attempt = midnight + step * int((now - midnight) / step)
        iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        age, valid, retrieved = (None, None, None) if none else self._weather_times(now)
        sources = []
        for s in _SOURCES:
            sources.append({
                "source": s,
                "mock": True,
                "last_attempt_at": iso(last_attempt),
                "last_success_retrieved_at": retrieved,
                "last_source_time": valid,
                "last_error": None if failures == 0 else "MOCK: simulated upstream timeout",
                "last_error_at": None if failures == 0 else iso(last_attempt),
                # never "no_cache" in a response: a source that never succeeded is expired (Team Lead)
                "stale_level": StaleLevel.EXPIRED.value if none else self.level.value,
                "stale_flag": none or self.level is not StaleLevel.FRESH,
                "age_minutes": age,
                "fetch_age_minutes": age,
                "consecutive_failures": failures,
                "next_poll_at": iso(last_attempt + step),
            })
        return {
            "mock": True,
            "note": "MOCK values: ages are simulated; success/source times are the data clock minus those ages",
            "sources": sources,
            "cache_policy": {
                "proposal": True,
                "poll_interval_minutes": p.poll_interval_minutes,
                "fresh_max_minutes": p.fresh_max_minutes,
                "stale_max_minutes": p.stale_max_minutes,
                "fetch_timeout_seconds": p.fetch_timeout_seconds,
                "data_age_thresholds_tbd": DATA_AGE_THRESHOLDS_TBD,
                "data_age_thresholds_provisional": sorted(DATA_AGE_PROVISIONAL),
                "data_age": {k: {"fresh_max_minutes": v.fresh_max_minutes, "stale_max_minutes": v.stale_max_minutes}
                             for k, v in DATA_AGE_THRESHOLDS.items()},
            },
        }
