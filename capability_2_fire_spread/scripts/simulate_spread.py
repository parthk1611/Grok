#!/usr/bin/env python3
"""Wind-driven fire-spread simulator on a 90 m grid.

The fire spreads from burning cells to their neighbours.  How fast it crosses
to a neighbour comes from the Canadian FBP rate-of-spread equations for the
two cells' fuel types, that hour's wind, fine fuel moisture and Buildup Index,
an ellipse that stretches the fire downwind, and the slope between the cells.
Each cell's arrival time is the earliest time fire can reach it by any path,
found with a shortest-travel-time search, so the fire flows around lakes and
through faster fuels.

`FireCase` loads one fire's grids and weather from the spread dataset.
`simulate` runs the fire forward from a set of burning cells.  Needs numpy,
pandas, scipy, shapely and pyproj; loading a real fire also needs rasterio.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import shapely
from pyproj import Geod, Transformer
from scipy import ndimage

from fbp_spread import MAP_CODES, directional_rate, ellipse_rates, hourly_ffmc
from spread_common import INTERIM, PROCESSED, RAW, load_perimeters_3978


CELL_375_M = 375.0
# Sixteen neighbours: the eight adjacent cells and the eight knight moves, as (row step, column step).
MOVES = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1), (-2, -1), (-2, 1), (2, -1), (2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2)]
MAX_SLOPE = 0.7


@dataclass
class Settings:
    """The simulator's adjustable settings."""

    ros_scale: float = 1.0  # multiplies every rate of spread
    flank_scale: float = 1.0  # further multiplies the flank and back rates: below 1 the fire is narrower than the FBP ellipse
    wind_scale: float = 1.0  # multiplies the 10 m wind speed before it is used
    repair_burn_scar: bool = True  # replace the 2024 map's regrowth inside the perimeter with the fuel around it
    # False: the day's fine fuel moisture code holds all day, so only wind changes hour to hour.
    # True: moisture follows hourly temperature and humidity, which nearly stops spread overnight.
    hourly_moisture: bool = False


@dataclass
class FireCase:
    fire_id: str
    west: float
    north: float
    pixel: float
    fuel: np.ndarray  # fuel-map codes after any burn-scar repair
    fuel_as_mapped: np.ndarray
    elevation: np.ndarray
    weather: pd.DataFrame  # hourly, with ffmc, bui and day_of_year added
    convergence_deg: float  # true bearing of grid north
    ignition_cell: tuple[int, int] | None
    arrival: pd.DataFrame  # observed 375 m cells with first_observation and first_time_utc
    observation_times: pd.Series  # observation number -> time_utc
    cell375: np.ndarray = field(repr=False, default=None)  # per 90 m cell: index of its 375 m cell
    cell375_keys: list[tuple[int, int]] = field(repr=False, default=None)

    @property
    def shape(self) -> tuple[int, int]:
        return self.fuel.shape

    def mask_from_cells(self, cells: pd.DataFrame) -> np.ndarray:
        """Boolean 90 m mask of the given observed 375 m cells."""
        lookup = {key: index for index, key in enumerate(self.cell375_keys)}
        wanted = np.zeros(len(self.cell375_keys), dtype=bool)
        for key in zip(cells.cell_x, cells.cell_y):
            if key in lookup:
                wanted[lookup[key]] = True
        return wanted[self.cell375]

    def burned_375(self, burned: np.ndarray, minimum_share: float = 0.5) -> np.ndarray:
        """Per 375 m cell: whether at least `minimum_share` of its 90 m cells are burned."""
        total = np.bincount(self.cell375.ravel(), minlength=len(self.cell375_keys))
        hit = np.bincount(self.cell375.ravel(), weights=burned.ravel(), minlength=len(self.cell375_keys))
        return hit >= minimum_share * np.maximum(total, 1)


WATER_CODE = 102


def _repair_burn_scar(codes: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """Give land inside the perimeter the fuel of the nearest burnable cell outside it.

    The 2024 map shows what has grown back, or non-fuel, where the fire burned.
    Water inside the perimeter is left as water: lakes were there before the fire.
    """
    burnable = np.isin(codes, list(MAP_CODES))
    source = burnable & ~inside
    target = inside & (codes != WATER_CODE)
    if not source.any() or not target.any():
        return codes
    nearest = ndimage.distance_transform_edt(~source, return_distances=False, return_indices=True)
    repaired = codes.copy()
    repaired[target] = codes[nearest[0][target], nearest[1][target]]
    return repaired


def load_case(fire_id: str, settings: Settings | None = None, shared: dict | None = None) -> FireCase:
    """Load one fire's grids, weather and satellite record.  `shared` caches the big tables between fires."""
    import rasterio  # only needed to read the grids, so `simulate` runs without it

    settings = settings or Settings()
    shared = shared if shared is not None else {}
    if "windows" not in shared:
        shared["windows"] = {w["fire_id"]: w for w in json.loads((RAW / "fuel_terrain/fuel_windows_manifest.json").read_text(encoding="utf-8"))["windows"]}
        shared["perimeters"] = load_perimeters_3978()
        shared["fires"] = pd.read_csv(PROCESSED / "ontario_fire_spread_fires_v1.csv", dtype={"fire_id": str}).set_index("fire_id")
        shared["hourly"] = dict(tuple(pd.read_csv(PROCESSED / "ontario_fire_spread_weather_hourly_v1.csv.gz", dtype={"fire_id": str}, parse_dates=["time_utc", "time_local"]).groupby("fire_id")))
        shared["daily"] = dict(tuple(pd.read_csv(PROCESSED / "ontario_fire_spread_weather_daily_v1.csv", dtype={"fire_id": str}).groupby("fire_id")))
        shared["arrival"] = dict(tuple(pd.read_csv(PROCESSED / "ontario_fire_spread_arrival_cells_v1.csv.gz", dtype={"fire_id": str}, parse_dates=["first_time_utc"]).groupby("fire_id")))
        master = pd.read_csv(PROCESSED / "ontario_fire_spread_master_v1.csv", dtype={"fire_id": str}, usecols=["fire_id", "observation", "time_utc"], parse_dates=["time_utc"])
        shared["observations"] = dict(tuple(master.groupby("fire_id")))
    window = shared["windows"][fire_id]
    fire = shared["fires"].loc[fire_id]
    rows, columns, pixel = window["rows"], window["columns"], window["pixel_size_m"]
    with rasterio.open(RAW / "fuel_terrain/windows" / window["filename"]) as source:
        mapped = source.read(1).astype(np.int32)
    with rasterio.open(RAW / "fuel_terrain/windows" / f"{fire_id}_mrdem_dtm_90m.tif") as source:
        elevation = source.read(1).astype(np.float32)
    elevation[elevation < -1000] = np.nan
    elevation = np.where(np.isnan(elevation), np.nanmean(elevation), elevation)

    x = window["west"] + (np.arange(columns) + 0.5) * pixel
    y = window["north"] - (np.arange(rows) + 0.5) * pixel
    grid_x, grid_y = np.meshgrid(x, y)
    fuel = mapped
    if settings.repair_burn_scar:
        perimeter = shared["perimeters"][fire_id]
        shapely.prepare(perimeter)
        fuel = _repair_burn_scar(mapped, shapely.contains_xy(perimeter, grid_x, grid_y))

    keys_x, keys_y = np.floor(grid_x / CELL_375_M).astype(np.int64), np.floor(grid_y / CELL_375_M).astype(np.int64)
    combined = (keys_x - keys_x.min()) * (keys_y.max() - keys_y.min() + 1) + (keys_y - keys_y.min())
    unique, inverse = np.unique(combined, return_inverse=True)
    span = keys_y.max() - keys_y.min() + 1
    cell375_keys = [(int(value // span + keys_x.min()), int(value % span + keys_y.min())) for value in unique]

    weather = shared["hourly"][fire_id].sort_values("time_utc").reset_index(drop=True).copy()
    daily = shared["daily"][fire_id].set_index("date")
    local_date = weather.time_local.dt.strftime("%Y-%m-%d")
    weather["bui"] = local_date.map(daily.bui).ffill().bfill()
    weather["day_of_year"] = weather.time_local.dt.dayofyear
    # Hourly fine fuel moisture.  The daily code describes mid-afternoon, so the hourly series is
    # re-seeded from it at 16:00 each day and the two never drift apart.
    daily_ffmc = local_date.map(daily.ffmc).ffill().bfill()
    codes, previous = [], float(daily_ffmc.iloc[0])
    for row, daily_value, hour in zip(weather.itertuples(), daily_ffmc, weather.time_local.dt.hour):
        previous = float(daily_value) if hour == 16 else hourly_ffmc(row.temp_c, row.rh_pct, row.wind_speed_kmh, row.precip_mm, previous)
        codes.append(previous)
    weather["ffmc"] = codes
    weather["ffmc_daily"] = daily_ffmc.values

    to_geographic = Transformer.from_crs("EPSG:3978", "EPSG:4326", always_xy=True)
    to_grid = Transformer.from_crs("EPSG:4326", "EPSG:3978", always_xy=True)
    centre_x, centre_y = window["west"] + columns * pixel / 2, window["north"] - rows * pixel / 2
    lon0, lat0 = to_geographic.transform(centre_x, centre_y)
    lon1, lat1 = to_geographic.transform(centre_x, centre_y + 1_000)
    convergence = Geod(ellps="GRS80").inv(lon0, lat0, lon1, lat1)[0]
    ignition = None
    if pd.notna(fire.ignition_longitude):
        ix, iy = to_grid.transform(fire.ignition_longitude, fire.ignition_latitude)
        row, column = int((window["north"] - iy) // pixel), int((ix - window["west"]) // pixel)
        if 0 <= row < rows and 0 <= column < columns:
            ignition = (row, column)
    return FireCase(
        fire_id=fire_id, west=window["west"], north=window["north"], pixel=pixel, fuel=fuel, fuel_as_mapped=mapped, elevation=elevation,
        weather=weather, convergence_deg=convergence, ignition_cell=ignition, arrival=shared["arrival"][fire_id],
        observation_times=shared["observations"][fire_id].set_index("observation").time_utc.sort_index(),
        cell375=inverse.reshape(rows, columns), cell375_keys=cell375_keys,
    )  # fmt: skip


def simulate(
    case: FireCase, burning: np.ndarray, start_utc: pd.Timestamp, hours: float, settings: Settings | None = None, active: np.ndarray | None = None
) -> np.ndarray:
    """Minutes after `start_utc` at which fire reaches each cell; infinity where it does not within `hours`.

    `burning` marks the cells already burned at the start; they cannot burn
    again.  Fire spreads from the edge of that area, or only from the part of
    the edge inside `active` when it is given (the stretch of perimeter still
    alight).  Weather is taken hour by hour; a move between two cells uses the
    hour in which it begins.
    """
    settings = settings or Settings()
    rows, columns = case.shape
    codes = sorted(set(np.unique(case.fuel).tolist()) & set(MAP_CODES))
    code_index = {code: index for index, code in enumerate(codes)}
    fuel_index = np.full(case.fuel.shape, -1, dtype=np.int32)
    for code, index in code_index.items():
        fuel_index[case.fuel == code] = index
    fuel_flat = fuel_index.ravel().tolist()
    elevation_flat = case.elevation.ravel().tolist()
    distances = [math.hypot(dr, dc) * case.pixel for dr, dc in MOVES]
    grid_bearings = [math.degrees(math.atan2(dc, -dr)) % 360 for dr, dc in MOVES]

    weather_times = case.weather.time_utc.values
    first_hour = int(np.searchsorted(weather_times, np.datetime64(start_utc.floor("h")), side="left"))
    minute_offset = (start_utc - start_utc.floor("h")).total_seconds() / 60.0
    moisture_column = "ffmc" if settings.hourly_moisture or "ffmc_daily" not in case.weather else "ffmc_daily"
    weather_rows = case.weather[["wind_speed_kmh", "wind_direction_deg", moisture_column, "bui", "day_of_year"]].values.tolist()
    rate_cache: dict[int, list[list[float]] | None] = {}

    def rates_for(hour: int) -> list[list[float]] | None:
        """Per fuel type, the spread rate (m/min) along each of the sixteen moves in this hour."""
        if hour not in rate_cache:
            if not 0 <= hour < len(weather_rows):
                rate_cache[hour] = None
            else:
                wind, wind_from, ffmc, bui, day_of_year = weather_rows[hour]
                wind *= settings.wind_scale
                head_grid_bearing = (wind_from + 180.0 - case.convergence_deg) % 360
                table = []
                for code in codes:
                    head, back, flank = ellipse_rates(code, ffmc, bui, wind, int(day_of_year))
                    back, flank = back * settings.flank_scale, flank * settings.flank_scale
                    table.append([settings.ros_scale * directional_rate(head, back, flank, bearing - head_grid_bearing) for bearing in grid_bearings])
                rate_cache[hour] = table
        return rate_cache[hour]

    limit = hours * 60.0
    arrival = [math.inf] * (rows * columns)
    burning_flat = burning.ravel()
    # Only the edge of the burning area can spread, so only those cells start the search.
    edge = burning & ~ndimage.binary_erosion(burning, structure=np.ones((3, 3), dtype=bool), border_value=0)
    if active is not None and (edge & active).any():
        edge &= active
    heap = [(0.0, int(index)) for index in np.flatnonzero(edge.ravel())]
    for index in np.flatnonzero(burning_flat):
        arrival[int(index)] = 0.0
    heapq.heapify(heap)
    while heap:
        time_now, cell = heapq.heappop(heap)
        if time_now > arrival[cell] or time_now > limit:
            continue
        table = rates_for(first_hour + int((time_now + minute_offset) // 60))
        source_fuel = fuel_flat[cell]
        if table is None or source_fuel < 0:
            continue
        source_rates = table[source_fuel]
        row, column = divmod(cell, columns)
        for move, (dr, dc) in enumerate(MOVES):
            next_row, next_column = row + dr, column + dc
            if not (0 <= next_row < rows and 0 <= next_column < columns):
                continue
            neighbour = next_row * columns + next_column
            target_fuel = fuel_flat[neighbour]
            if target_fuel < 0:
                continue
            rate_here, rate_there = source_rates[move], table[target_fuel][move]
            if rate_here <= 0 or rate_there <= 0:
                continue
            rise = (elevation_flat[neighbour] - elevation_flat[cell]) / distances[move]
            uphill = math.exp(3.533 * min(rise, MAX_SLOPE) ** 1.2) if rise > 0 else 1.0
            # half the move in each cell's fuel
            arrive = time_now + distances[move] / 2.0 * (1.0 / rate_here + 1.0 / rate_there) / uphill
            if arrive < arrival[neighbour] and arrive <= limit:
                arrival[neighbour] = arrive
                heapq.heappush(heap, (arrive, neighbour))
    return np.array(arrival, dtype=np.float32).reshape(rows, columns)


def weather_at(case: FireCase, moment: pd.Timestamp) -> dict[str, float]:
    """The hourly weather record in force at a moment."""
    index = int(np.searchsorted(case.weather.time_utc.values, np.datetime64(moment.floor("h")), side="left"))
    row = case.weather.iloc[min(max(index, 0), len(case.weather) - 1)]
    return {"wind_speed_kmh": float(row.wind_speed_kmh), "wind_direction_deg": float(row.wind_direction_deg), "ffmc": float(row.ffmc), "bui": float(row.bui), "temp_c": float(row.temp_c), "rh_pct": float(row.rh_pct)}
