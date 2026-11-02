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


@dataclass(frozen=True)
class Served:
    """What a provider hands the API layer: a payload plus how stale its inputs are."""

    payload: object                 # dict or list of dicts, contract-shaped
    stale_level: StaleLevel
    data_timestamp: str | None      # newest input time actually used (ISO UTC Z), not serving time


class DataProvider(ABC):
    mode: str = "unknown"           # 'mock' | 'live'
    is_mock: bool = False

    # Providers raise NotFound (404) or NoCachedData (503, only when nothing is cached at all).
    @abstractmethod
    def ignition_for_date(self, date: str, top_k: int | None) -> Served: ...

    @abstractmethod
    def ignition_for_cell(self, cell_id: str, date: str) -> Served: ...

    @abstractmethod
    def run_spread(self, req: SpreadRequest) -> Served: ...

    @abstractmethod
    def latest_weather(self, lat: float | None, lon: float | None, cell_id: str | None) -> Served: ...

    @abstractmethod
    def ingestion_status(self) -> dict: ...
