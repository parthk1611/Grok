#!/usr/bin/env python3
"""Point-start API for the FBP fire-spread simulator.

`run_point` answers "if a fire starts at this point at this time, where is it
after 1, 3 and 6 hours?".  It wraps `simulate_spread.simulate` without changing
it, and it is built for a live, forward-looking use, so it never touches
information that would not be known at the start time:

* it reads the fuel map as mapped (`FireCase.fuel_as_mapped`), never the grid
  that `load_case` repairs with the fire's final perimeter, and the
  `repair_burn_scar` setting is forced off;
* it does not look at `FireCase.arrival` (the satellite record of the real fire);
* the weather is whatever the case carries from `start_utc` on, or the
  `weather_override` you pass.  Where that weather comes from (forecast or
  reanalysis) is the caller's responsibility and should be labelled as such.

Coordinates: `ignition_xy` is EPSG:3978 (metres), the grid's CRS, or give
`lonlat=(lon, lat)` in EPSG:4326.  Bearings are degrees clockwise from true north.

`seed` / `rng` are a placeholder for the Monte Carlo layer: they are recorded in
the result and a generator is built, but nothing is perturbed yet, so the run is
fully deterministic whatever the seed.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from pyproj import Transformer

from fbp_spread import MAP_CODES
from simulate_spread import FireCase, Settings, simulate


DEFAULT_HORIZONS = (1, 3, 6)
# Weather columns a scalar override may set.  `ffmc` also sets `ffmc_daily`, because the simulator
# reads the daily code unless `Settings.hourly_moisture` is on.
OVERRIDABLE = ("wind_speed_kmh", "wind_direction_deg", "ffmc", "bui", "day_of_year")
_TO_GRID = Transformer.from_crs("EPSG:4326", "EPSG:3978", always_xy=True)


@dataclass
class PointResult:
    """One point-start run.  Grids have the case's shape (rows, columns)."""

    arrival_min: np.ndarray  # minutes after start at which fire reaches each cell; inf if not within the longest horizon
    masks: dict[float, np.ndarray]  # horizon (hours) -> boolean burned mask, nested by construction
    area_ha: dict[float, float]  # horizon (hours) -> burned area
    ignition_cell: tuple[int, int]
    ignition_burnable: bool  # False: the start cell is water or non-fuel, so nothing burns
    start_utc: pd.Timestamp
    horizons: tuple[float, ...]
    settings: Settings
    seed: int | None
    convergence_deg: float
    pixel: float
    uses_final_perimeter: bool = False  # always False on this path
    fuel_source: str = "fuel_as_mapped"


def _as_utc_naive(moment) -> pd.Timestamp:
    stamp = pd.Timestamp(moment)
    return stamp.tz_convert("UTC").tz_localize(None) if stamp.tzinfo is not None else stamp


def locate_cell(case: FireCase, ignition_xy: tuple[float, float] | None = None, lonlat: tuple[float, float] | None = None) -> tuple[int, int]:
    """The (row, column) of the grid cell holding a point; ValueError when the point is off the grid."""
    if (ignition_xy is None) == (lonlat is None):
        raise ValueError("give exactly one of ignition_xy (EPSG:3978) or lonlat (EPSG:4326)")
    x, y = _TO_GRID.transform(*lonlat) if lonlat is not None else ignition_xy
    row, column = int(math.floor((case.north - y) / case.pixel)), int(math.floor((x - case.west) / case.pixel))
    rows, columns = case.shape
    if not (0 <= row < rows and 0 <= column < columns):
        raise ValueError(f"ignition point ({x:.1f}, {y:.1f}) is outside the grid")
    return row, column


def apply_weather_override(weather: pd.DataFrame, override: dict | pd.DataFrame | None) -> pd.DataFrame:
    """A copy of the hourly weather with `override` applied.

    A dict of scalars (see `OVERRIDABLE`) is held constant for every hour.  A
    DataFrame replaces the weather outright and needs the simulator's columns.
    """
    if override is None:
        return weather.copy()
    if isinstance(override, pd.DataFrame):
        needed = {"time_utc", "wind_speed_kmh", "wind_direction_deg", "ffmc", "bui", "day_of_year"}
        if not needed <= set(override.columns):
            raise ValueError(f"weather override is missing columns {sorted(needed - set(override.columns))}")
        replaced = override.sort_values("time_utc").reset_index(drop=True).copy()
        if "ffmc_daily" not in replaced:
            replaced["ffmc_daily"] = replaced["ffmc"]
        return replaced
    unknown = set(override) - set(OVERRIDABLE)
    if unknown:
        raise ValueError(f"unknown weather override keys {sorted(unknown)}; allowed: {OVERRIDABLE}")
    replaced = weather.copy()
    for key, value in override.items():
        replaced[key] = value
        if key == "ffmc":
            replaced["ffmc_daily"] = value
    return replaced


def run_point(
    case: FireCase,
    start_utc,
    ignition_xy: tuple[float, float] | None = None,
    lonlat: tuple[float, float] | None = None,
    horizons: tuple[float, ...] = DEFAULT_HORIZONS,
    settings: Settings | None = None,
    weather_override: dict | pd.DataFrame | None = None,
    seed: int | None = None,
    rng: np.random.Generator | None = None,
) -> PointResult:
    """Spread a fire from one point for the longest of `horizons` hours and cut it at each horizon.

    Raises ValueError when the point is off the grid or the weather does not
    cover the whole run (the simulator itself would silently stop spreading).
    """
    horizons = tuple(sorted(float(h) for h in horizons))
    if not horizons or horizons[0] <= 0:
        raise ValueError("horizons must be positive hours")
    start = _as_utc_naive(start_utc)
    settings = dataclasses.replace(settings or Settings(), repair_burn_scar=False)
    row, column = locate_cell(case, ignition_xy, lonlat)
    rng = rng if rng is not None else np.random.default_rng(seed)  # placeholder: not consumed until the Monte Carlo layer
    del rng

    weather = apply_weather_override(case.weather, weather_override)
    last_needed = start + pd.Timedelta(hours=horizons[-1])
    covered_from, covered_to = weather.time_utc.min(), weather.time_utc.max() + pd.Timedelta(hours=1)
    if start.floor("h") < covered_from or last_needed > covered_to:
        raise ValueError(f"weather covers {covered_from} to {covered_to} UTC but the run needs {start.floor('h')} to {last_needed}")

    # Never use the burn-scar-repaired grid (it is built from the final perimeter).
    run_case = dataclasses.replace(case, fuel=case.fuel_as_mapped, weather=weather)
    burnable = int(case.fuel_as_mapped[row, column]) in MAP_CODES
    burning = np.zeros(case.shape, dtype=bool)
    burning[row, column] = True
    arrival = simulate(run_case, burning, start, hours=horizons[-1], settings=settings)

    masks, areas = {}, {}
    for hours in horizons:
        mask = np.isfinite(arrival) & (arrival <= hours * 60.0) if burnable else np.zeros(case.shape, dtype=bool)
        masks[hours] = mask
        areas[hours] = float(mask.sum()) * case.pixel**2 / 10_000.0
    return PointResult(
        arrival_min=arrival, masks=masks, area_ha=areas, ignition_cell=(row, column), ignition_burnable=burnable, start_utc=start,
        horizons=horizons, settings=settings, seed=seed, convergence_deg=case.convergence_deg, pixel=case.pixel,
    )  # fmt: skip


def head_bearing_deg(result: PointResult, hours: float, minimum_offset_ratio: float = 0.05) -> float | None:
    """True bearing (deg clockwise from north) from the ignition to the burned area's centre of mass.

    None when the burned area is empty or centred on the ignition (offset below
    `minimum_offset_ratio` of the mean radius), i.e. no head direction exists.
    """
    mask = result.masks[hours]
    if mask.sum() < 2:
        return None
    rows, columns = np.nonzero(mask)
    d_row, d_col = rows.mean() - result.ignition_cell[0], columns.mean() - result.ignition_cell[1]
    radius = math.sqrt(mask.sum() / math.pi)
    if math.hypot(d_row, d_col) < minimum_offset_ratio * radius:
        return None
    return (math.degrees(math.atan2(d_col, -d_row)) + result.convergence_deg) % 360


def length_to_breadth(result: PointResult, hours: float) -> float | None:
    """Ratio of the burned area's principal standard deviations (long axis over short axis); 1 is round."""
    mask = result.masks[hours]
    if mask.sum() < 3:
        return None
    eigenvalues = np.linalg.eigvalsh(np.cov(np.array(np.nonzero(mask), dtype=float)))
    return float(math.sqrt(eigenvalues[1] / max(eigenvalues[0], 1e-12)))
