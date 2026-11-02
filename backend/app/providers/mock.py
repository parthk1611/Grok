"""MockProvider: serves the contracts/examples payloads (contract v0.2.0), clearly labelled MOCK.

Nothing here is a model result or real weather. Payloads keep their
``model_version`` of ``example-not-a-real-model``. Documented, deliberate
adjustments (all visible in the API via the X-Mock-Data header):
  * RELAY_MOCK_SCENARIO overlays stale_flag / stale_level / age_minutes to demo the
    cache_fallback.md levels (fresh, stale, expired) and the 503 no_cache path;
  * the weather example's lat/lon/cell_id and the spread example's polygon
    position follow the request, so a map click lands where the user clicked.
"""
from __future__ import annotations

import copy
import re

from ..cache_policy import DATA_AGE_THRESHOLDS, DATA_AGE_THRESHOLDS_TBD, DEFAULT_POLICY, StaleLevel
from ..contracts import Contracts
from ..errors import NoCachedData, NotFound
from .base import DataProvider, Served, SpreadRequest

_SCENARIO_LEVEL = {"fresh": StaleLevel.FRESH, "stale": StaleLevel.STALE,
                   "expired": StaleLevel.EXPIRED, "no_cache": StaleLevel.NO_CACHE}
_SCENARIO_AGE_MIN = {"fresh": 10.0, "stale": 120.0, "expired": 720.0}
_SOURCES = ("hrdps_geomet_wms", "cwfis_firewx_stns_current", "msc_swob_realtime")


def _shift(coords, dlon: float, dlat: float):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0] + dlon, 6), round(coords[1] + dlat, 6)]
    return [_shift(c, dlon, dlat) for c in coords]


class MockProvider(DataProvider):
    mode = "mock"
    is_mock = True

    def __init__(self, contracts: Contracts, scenario: str = "fresh"):
        if scenario not in _SCENARIO_LEVEL:
            raise ValueError(f"unknown mock scenario {scenario!r}")
        self.contracts = contracts
        self.scenario = scenario
        self.level = _SCENARIO_LEVEL[scenario]
        self._ign = contracts.example("ignition")
        self._spread = contracts.example("spread")
        self._wx = contracts.example("weather")

    # -- helpers ---------------------------------------------------------
    def _require_cache(self):
        if self.level is StaleLevel.NO_CACHE:
            raise NoCachedData("MOCK scenario 'no_cache': no cached data exists (simulated).")

    def _stale_reason(self) -> str | None:
        if self.level in (StaleLevel.FRESH,):
            return None
        return (f"MOCK scenario '{self.scenario}': simulated {self.level.value} feed "
                f"(age {_SCENARIO_AGE_MIN[self.scenario]:.0f} min)")

    # -- DataProvider ----------------------------------------------------
    def _overlay_staleness(self, rec: dict) -> None:
        """Top-level stale_level/age_minutes (contract v0.2.0); stale_flag must equal level != fresh."""
        if self.level is not StaleLevel.FRESH:
            rec["stale_level"] = self.level.value
            rec["age_minutes"] = _SCENARIO_AGE_MIN[self.scenario]

    def _ignition_record(self) -> dict:
        rec = copy.deepcopy(self._ign)
        if self.level is not StaleLevel.FRESH:
            self._overlay_staleness(rec)
            rec["stale_flag"] = True
            rec["stale_reason"] = self._stale_reason()
        return rec

    def ignition_for_date(self, date: str, top_k: int | None) -> Served:
        self._require_cache()
        if date != self._ign["date"]:
            raise NotFound(f"MockProvider only holds the example date {self._ign['date']}")
        recs = [self._ignition_record()]
        return Served(recs[: top_k] if top_k else recs, self.level, self._ign["data_timestamp"])

    def ignition_for_cell(self, cell_id: str, date: str) -> Served:
        self._require_cache()
        if date != self._ign["date"] or cell_id != self._ign["cell_id"]:
            raise NotFound(f"MockProvider only holds cell {self._ign['cell_id']} on {self._ign['date']}")
        return Served(self._ignition_record(), self.level, self._ign["data_timestamp"])

    def run_spread(self, req: SpreadRequest) -> Served:
        self._require_cache()
        out = copy.deepcopy(self._spread)
        ex = out["ignition_point"]
        dlon, dlat = req.longitude - ex["longitude"], req.latitude - ex["latitude"]
        for f in out["features"]:
            f["geometry"]["coordinates"] = _shift(f["geometry"]["coordinates"], dlon, dlat)
        out["ignition_point"] = {"longitude": req.longitude, "latitude": req.latitude}
        out["start_time"] = req.start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if req.n_runs is not None:
            out["n_runs"] = req.n_runs
        if req.seed is not None:
            out["seed"] = req.seed
        if self.level is not StaleLevel.FRESH:
            self._overlay_staleness(out)
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
        if self.level is not StaleLevel.FRESH:
            rec["stale_level"] = self.level.value
            rec["age_minutes"] = _SCENARIO_AGE_MIN[self.scenario]
            rec["from_cache"] = True
        return Served(rec, self.level, rec["valid_time"])

    def ingestion_status(self) -> dict:
        p = DEFAULT_POLICY
        none = self.level is StaleLevel.NO_CACHE
        failures = {"fresh": 0, "stale": 4, "expired": 24, "no_cache": 3}[self.scenario]
        sources = []
        for s in _SOURCES:
            sources.append({
                "source": s,
                "last_success_retrieved_at": None if none else self._wx["retrieved_at"],
                "last_source_time": None if none else self._wx["valid_time"],
                "last_error": None if failures == 0 else "MOCK: simulated upstream timeout",
                "stale_level": self.level.value,
                "consecutive_failures": failures,
            })
        return {
            "mock": True,
            "note": "MOCK values; times below are the contracts/examples placeholders",
            "sources": sources,
            "cache_policy": {
                "proposal": True,
                "poll_interval_minutes": p.poll_interval_minutes,
                "fresh_max_minutes": p.fresh_max_minutes,
                "stale_max_minutes": p.stale_max_minutes,
                "fetch_timeout_seconds": p.fetch_timeout_seconds,
                "data_age_thresholds_tbd": DATA_AGE_THRESHOLDS_TBD,
                "data_age": {k: {"fresh_max_minutes": v.fresh_max_minutes, "stale_max_minutes": v.stale_max_minutes}
                             for k, v in DATA_AGE_THRESHOLDS.items()},
            },
        }
