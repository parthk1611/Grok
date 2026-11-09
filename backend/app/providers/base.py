"""Pluggable data-provider interface. MockProvider now; LiveProvider later (T2.6/T2.7)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ..cache_policy import StaleLevel


@dataclass(frozen=True)
class SpreadRequest:
    longitude: float
    latitude: float
    start_time: datetime            # aware UTC
    n_runs: int | None = None
    seed: int | None = None
    confirm_degraded: bool = False          # user confirmed a run on expired inputs
    weather_override: dict | None = None    # manual weather (validated by the API layer)


@dataclass(frozen=True)
class Served:
    """What a provider hands the API layer: a payload plus how stale its inputs are."""

    payload: object                 # dict or list of dicts, contract-shaped
    stale_level: StaleLevel
    data_timestamp: str | None      # newest input time actually used (ISO UTC Z), not serving time


class DataProvider(ABC):
    mode: str = "unknown"           # 'mock' | 'live'
    is_mock: bool = False

    # Providers raise NotFound (404), NoCachedData (503, only when nothing is cached at all) or
    # ConfirmationRequired (409, expired spread inputs without confirm_degraded / weather_override).
    #
    # Ignition stale-day rule: `date` is a local date or "latest". Serve the newest map at or before
    # it; records keep their own `date` and carry stale_flag true when it is earlier than requested.
    # NotFound only when nothing exists at or before the date.
    @abstractmethod
    def ignition_dates(self) -> list[str]: ...

    @abstractmethod
    def ignition_for_date(self, date: str, top_k: int | None) -> Served: ...

    @abstractmethod
    def ignition_for_cell(self, cell_id: str, date: str) -> Served: ...

    @abstractmethod
    def last_cache_time(self) -> str | None:
        """UTC (Z) time of the newest data ever cached, or None when nothing was ever cached."""

    @abstractmethod
    def run_spread(self, req: SpreadRequest) -> Served: ...

    @abstractmethod
    def latest_weather(self, lat: float | None, lon: float | None, cell_id: str | None) -> Served: ...

    @abstractmethod
    def ingestion_status(self, now: datetime) -> dict: ...
