#!/usr/bin/env python3
"""Monte Carlo burn-probability bands for the point-start spread simulator (task T1.7).

`run_monte_carlo` runs `spread_api.run_point` `n_runs` times, each with a randomly perturbed weather series and
rate-of-spread scale, and turns the runs into a GeoJSON FeatureCollection that follows
`contracts/spread_output.schema.json` (v0.2.0).

Bands
-----
For each horizon (1, 3, 6 h) the burn fraction of a cell is the share of runs that had burned it by then.
Band p10 = cells with fraction >= 0.1 (outer, widest), p50 = fraction >= 0.5, p90 = fraction >= 0.9 (inner).
Because all three bands are thresholds of the same fraction grid, p90 is inside p50 inside p10 by construction;
because each run's burned area only grows with time, every band also grows with horizon.

Perturbation (one random draw per run, applied to the whole run)
----------------------------------------------------------------
* wind speed: multiplied by exp(sigma * z), z ~ N(0, 1)  (lognormal, median 1)
* wind direction: plus N(0, sigma) degrees, wrapped to [0, 360)
* FFMC: plus N(0, sigma), clipped to the valid range [0, 101]
* ros_scale: base * exp(sigma * z)  (lognormal around the base, 0.8 by default)

The hourly weather series is kept as it is and every hour gets the same factor or offset, so a run's weather
still varies hour to hour as in the base series.  **The default sigmas are provisional round numbers, not
calibrated against observed fires** (calibration is task T2.5); the bands show sensitivity to those guesses,
not a validated probability.  The contract's `uncertainty_status` is `estimated` only when more than one run
was made and at least one sigma is above zero.

All random numbers come from one `numpy.random.Generator(seed)`; the draws for every run are made up front in a
fixed order, so the same seed gives the same output byte for byte (apart from `run_timestamp`).

Geometry
--------
Bands are made on the simulator's EPSG:3978 grid (cells are 90 m squares), merged into polygons with
`rasterio.features.shapes` (4-connectivity, holes kept), and reprojected to EPSG:4326 with pyproj
(`always_xy=True`), rounded to 6 decimals (about 0.1 m).  Every geometry is a MultiPolygon.  `area_ha` is
the number of cells times 0.81 ha, and `perimeter_km` the boundary length (outer rings and holes), both measured
in EPSG:3978.  That projection is conformal, not equal-area, so areas are off by a small scale factor that varies
across Ontario (not quantified here).  `head_direction_deg` is the true-north bearing from the ignition cell to
the band's centroid (`spread_api.head_bearing_deg`), or null when the band is empty or centred on the ignition.

Empty bands are always emitted, as `MultiPolygon` with `coordinates: []` and `area_ha: 0`, as v0.2.0 specifies.

Run `python scripts/spread_montecarlo.py --help` for the command line.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pyproj import Geod, Transformer
from rasterio import features
from rasterio.transform import Affine
from shapely.geometry import MultiPolygon, shape
from shapely.geometry.polygon import orient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spread_api as API  # noqa: E402
import simulate_spread as SIM  # noqa: E402
from fbp_spread import MAP_CODES  # noqa: E402


MODEL_VERSION = "blaze-fbp-mc-0.1.0-dev"
DISCLAIMER = "Research estimate for decision support only. It does not replace official fire alerts, warnings or evacuation orders."
BANDS = (("p10", 0.1), ("p50", 0.5), ("p90", 0.9))
CONTRACT_HORIZONS = (1, 3, 6)
MODES = ("retrospective", "hindcast", "forecast_like", "forecast")
STALE_LEVELS = ("fresh", "stale", "expired")
DEFAULT_N_RUNS = 100
_TO_4326 = Transformer.from_crs("EPSG:3978", "EPSG:4326", always_xy=True)


@dataclass(frozen=True)
class PerturbationConfig:
    """Sizes of the random perturbations.  **Provisional defaults, not calibrated** (calibration is T2.5)."""

    wind_speed_log_sigma: float = 0.25  # sigma of ln(factor on wind speed); 0.25 is roughly +-28% (1 sigma)
    wind_direction_sigma_deg: float = 15.0
    ffmc_sigma: float = 2.0  # FFMC points
    ffmc_range: tuple[float, float] = (0.0, 101.0)  # valid FFMC range; perturbed values are clipped to it
    ros_scale_base: float = 0.8  # tuned value in docs/fire_spread_simulator.md (the code default is 1.0)
    ros_scale_log_sigma: float = 0.25

    @property
    def any_perturbed(self) -> bool:
        return any(value > 0 for value in (self.wind_speed_log_sigma, self.wind_direction_sigma_deg, self.ffmc_sigma, self.ros_scale_log_sigma))

    def contract_parameters(self) -> list[dict]:
        """The `perturbation.parameters` list of the contract.  The contract has no lognormal, so the two
        multiplicative perturbations are named `ln_factor_*` and described as normal in log space."""
        return [
            {"name": "ln_factor_wind_speed_kmh", "distribution": "normal", "sigma": self.wind_speed_log_sigma},
            {"name": "wind_direction_deg", "distribution": "normal", "sigma": self.wind_direction_sigma_deg},
            {"name": "ffmc", "distribution": "normal", "sigma": self.ffmc_sigma, "low": self.ffmc_range[0], "high": self.ffmc_range[1]},
            {"name": "ln_factor_ros_scale", "distribution": "normal", "sigma": self.ros_scale_log_sigma},
            {"name": "ros_scale", "distribution": "fixed", "value": self.ros_scale_base},
        ]


@dataclass
class MonteCarloResult:
    geojson: dict  # the contract payload
    burn_fraction: dict[int, np.ndarray]  # horizon -> share of runs that burned each cell (float32, case shape)
    run_area_ha: np.ndarray  # (n_runs, n_horizons) burned area of each individual run
    draws: dict[str, np.ndarray]  # per-run factors/offsets actually applied
    edge_runs: int  # runs whose longest-horizon fire touched the grid edge (the grid was too small for them)
    timings_s: dict[str, float] = field(default_factory=dict)  # wall clock; not part of the payload


def to_json(payload: dict) -> str:
    """Canonical text of a payload (sorted keys, no whitespace variation) so equal results are equal bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _utc_z(moment) -> str:
    stamp = pd.Timestamp(moment)
    stamp = stamp.tz_convert("UTC").tz_localize(None) if stamp.tzinfo is not None else stamp
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value):
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else float(value)


def _cell_threshold(level: float, n_runs: int) -> int:
    """Smallest run count that reaches `level` (tolerant of float error, e.g. 0.9 * 10)."""
    return max(1, math.ceil(level * n_runs - 1e-9))


def mask_to_multipolygon_3978(mask: np.ndarray, case: SIM.FireCase) -> MultiPolygon | None:
    """Polygons (EPSG:3978, exterior rings counter-clockwise) of a boolean mask on the case grid; None when empty."""
    if not mask.any():
        return None
    transform = Affine(case.pixel, 0.0, case.west, 0.0, -case.pixel, case.north)
    polygons = [orient(shape(geometry), 1.0) for geometry, value in features.shapes(mask.astype(np.uint8), mask=mask, transform=transform, connectivity=4) if value == 1]
    return MultiPolygon(polygons)


def _ring(coords) -> list[list[float]]:
    return [[round(x, 6), round(y, 6)] for x, y in coords]


def multipolygon_to_geojson_geometry(geometry: MultiPolygon | None) -> dict:
    """GeoJSON MultiPolygon in EPSG:4326 for an EPSG:3978 geometry; the empty MultiPolygon for None."""
    if geometry is None:
        return {"type": "MultiPolygon", "coordinates": []}
    lonlat = shapely.transform(geometry, lambda xy: np.column_stack(_TO_4326.transform(xy[:, 0], xy[:, 1])))
    return {"type": "MultiPolygon", "coordinates": [[_ring(poly.exterior.coords)] + [_ring(hole.coords) for hole in poly.interiors] for poly in lonlat.geoms]}


def _fuel_type_label(case: SIM.FireCase, cell: tuple[int, int]) -> str | None:
    entry = MAP_CODES.get(int(case.fuel_as_mapped[cell]))
    if entry is None:
        return None
    kind, value = entry
    return value if isinstance(value, str) else (kind if value is None else f"{kind}_{value:g}pct_conifer")


def _weather_window(case: SIM.FireCase, start: pd.Timestamp, hours: float) -> pd.DataFrame:
    weather = case.weather.sort_values("time_utc")
    window = weather[(weather.time_utc >= start.floor("h")) & (weather.time_utc <= start + pd.Timedelta(hours=hours))]
    if window.empty:
        raise ValueError("the case's weather does not cover the start time")
    return window.reset_index(drop=True)


def _perturbed_weather(base: pd.DataFrame, speed_factor: float, direction_offset: float, ffmc_offset: float, ffmc_range: tuple[float, float]) -> pd.DataFrame:
    weather = base.copy()
    weather["wind_speed_kmh"] = base.wind_speed_kmh * speed_factor
    weather["wind_direction_deg"] = (base.wind_direction_deg + direction_offset) % 360.0
    for column in ("ffmc", "ffmc_daily"):
        if column in weather:
            weather[column] = (base[column] + ffmc_offset).clip(*ffmc_range)
    return weather


def _inputs_block(case, window, cell, config, weather_source, weather_valid_time, weather_data_timestamp, stale_level, stale_reason, fuel_source, settings, include_series) -> dict:
    first = window.iloc[0]
    moisture = "ffmc" if settings.hourly_moisture or "ffmc_daily" not in window else "ffmc_daily"
    inputs = {
        "weather_source": weather_source,
        "weather_valid_time": _utc_z(weather_valid_time if weather_valid_time is not None else first.time_utc),
        "wind_speed_kmh": float(first.wind_speed_kmh),
        "wind_direction_deg": float(first.wind_direction_deg) % 360.0,
        "ffmc": _number(first[moisture]),
        "bui": _number(first.bui),
        "fuel_type": _fuel_type_label(case, cell),
        "stale_flag": stale_level != "fresh",
        "stale_reason": stale_reason,
        "fuel_source": fuel_source,
    }
    for key in ("temp_c", "rh_pct"):
        if key in window:
            inputs[key] = _number(first[key])
    if weather_data_timestamp is not None:
        inputs["weather_data_timestamp"] = _utc_z(weather_data_timestamp)
    if include_series:
        inputs["weather_series"] = [
            {"valid_time": _utc_z(row.time_utc), "wind_speed_kmh": float(row.wind_speed_kmh), "wind_direction_deg": float(row.wind_direction_deg) % 360.0}
            | {key: _number(getattr(row, key)) for key in ("temp_c", "rh_pct", "ffmc") if key in window}
            for row in window.itertuples()
        ]  # the base (unperturbed) hourly weather the runs started from
    return inputs


def run_monte_carlo(
    case: SIM.FireCase,
    start_utc,
    *,
    seed: int,
    mode: str,
    weather_source: str,
    fuel_source: str,
    ignition_xy: tuple[float, float] | None = None,
    lonlat: tuple[float, float] | None = None,
    n_runs: int = DEFAULT_N_RUNS,
    config: PerturbationConfig | None = None,
    horizons: tuple[int, ...] = CONTRACT_HORIZONS,
    settings: SIM.Settings | None = None,
    stale_level: str = "fresh",
    age_minutes: float = 0.0,
    stale_reason: str | None = None,
    weather_valid_time=None,
    weather_data_timestamp=None,
    validated_horizons_h: tuple[int, ...] = (),
    model_version: str = MODEL_VERSION,
    run_timestamp=None,
    include_weather_series: bool = True,
) -> MonteCarloResult:
    """Run the ensemble and build the contract payload.  `mode`, `stale_level`, `age_minutes`, the weather and fuel
    descriptions describe the *inputs*, which only the caller knows, so they are arguments rather than defaults.
    Raises ValueError for a bad argument, an off-grid point, or weather that does not cover the run."""
    config = config or PerturbationConfig()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if stale_level not in STALE_LEVELS:
        raise ValueError(f"stale_level must be one of {STALE_LEVELS}")
    if not isinstance(n_runs, int) or n_runs < 1:
        raise ValueError("n_runs must be a positive integer")
    horizons = tuple(int(h) for h in sorted(horizons))
    if not horizons or any(h not in CONTRACT_HORIZONS for h in horizons):
        raise ValueError(f"horizons must be a subset of {CONTRACT_HORIZONS}")
    started = time.perf_counter()
    start = API._as_utc_naive(start_utc)
    cell = API.locate_cell(case, ignition_xy, lonlat)
    cell_x, cell_y = case.west + (cell[1] + 0.5) * case.pixel, case.north - (cell[0] + 0.5) * case.pixel
    lon, lat = lonlat if lonlat is not None else _TO_4326.transform(cell_x, cell_y)  # as given; the run starts at the centre of the cell holding it
    point_xy = (cell_x, cell_y)
    settings = settings or SIM.Settings()
    window = _weather_window(case, start, max(horizons))

    # All random numbers are drawn up front, in a fixed order, so results do not depend on how runs are processed.
    rng = np.random.default_rng(seed)
    z = {name: rng.standard_normal(n_runs) for name in ("wind_speed", "wind_direction", "ffmc", "ros_scale")}
    draws = {
        "wind_speed_factor": np.exp(config.wind_speed_log_sigma * z["wind_speed"]),
        "wind_direction_offset_deg": config.wind_direction_sigma_deg * z["wind_direction"],
        "ffmc_offset": config.ffmc_sigma * z["ffmc"],
        "ros_scale": config.ros_scale_base * np.exp(config.ros_scale_log_sigma * z["ros_scale"]),
    }

    counts = {h: np.zeros(case.shape, dtype=np.int32) for h in horizons}
    run_areas = np.zeros((n_runs, len(horizons)))
    edge_runs = 0
    first_result = None
    border = np.zeros(case.shape, dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    simulate_started = time.perf_counter()
    for run in range(n_runs):
        weather = _perturbed_weather(window, draws["wind_speed_factor"][run], draws["wind_direction_offset_deg"][run], draws["ffmc_offset"][run], config.ffmc_range)
        result = API.run_point(
            case, start, ignition_xy=point_xy, horizons=horizons, settings=dataclasses.replace(settings, ros_scale=float(draws["ros_scale"][run])),
            weather_override=weather, seed=seed,
        )  # fmt: skip
        first_result = first_result or result
        for index, h in enumerate(horizons):
            counts[h] += result.masks[float(h)]
            run_areas[run, index] = result.area_ha[float(h)]
        edge_runs += bool((result.masks[float(horizons[-1])] & border).any())
    simulate_s = time.perf_counter() - simulate_started

    polygonize_started = time.perf_counter()
    fractions = {h: (counts[h] / n_runs).astype(np.float32) for h in horizons}
    feature_list = []
    for h in horizons:
        for band, level in BANDS:
            mask = counts[h] >= _cell_threshold(level, n_runs)
            geometry = mask_to_multipolygon_3978(mask, case)
            band_result = dataclasses.replace(first_result, masks={float(h): mask})
            head = API.head_bearing_deg(band_result, float(h))
            properties = {
                "horizon_h": h, "band": band, "burn_probability_level": level,
                "area_ha": round(float(mask.sum()) * case.pixel**2 / 10_000.0, 4),
                "perimeter_km": 0.0 if geometry is None else round(geometry.length / 1000.0, 4),
                "head_direction_deg": None if head is None else round(head, 2),
                "validated": h in validated_horizons_h,
            }  # fmt: skip
            feature_list.append({"type": "Feature", "geometry": multipolygon_to_geojson_geometry(geometry), "properties": properties})
    polygonize_s = time.perf_counter() - polygonize_started

    payload = {
        "type": "FeatureCollection",
        "run_timestamp": _utc_z(run_timestamp if run_timestamp is not None else datetime.now(timezone.utc)),
        "model_version": model_version,
        "n_runs": n_runs,
        "seed": int(seed),
        "ignition_point": {"longitude": round(lon, 6), "latitude": round(lat, 6)},
        "start_time": _utc_z(start),
        "inputs": _inputs_block(case, window, cell, config, weather_source, weather_valid_time, weather_data_timestamp, stale_level, stale_reason, fuel_source, settings, include_weather_series),
        "validated_horizons_h": sorted(validated_horizons_h),
        "disclaimer": DISCLAIMER,
        "features": feature_list,
        "mode": mode,
        "stale_level": stale_level,
        "age_minutes": float(age_minutes),
        "uncertainty_status": "estimated" if n_runs > 1 and config.any_perturbed else "not_estimated",
        "grid_resolution_m": float(case.pixel),
        "perturbation": {"parameters": config.contract_parameters()},
    }
    timings = {"total_s": time.perf_counter() - started, "simulate_s": simulate_s, "polygonize_s": polygonize_s}
    return MonteCarloResult(payload, fractions, run_areas, draws, edge_runs, timings)


# ---------------------------------------------------------------- synthetic case (tests, demo, benchmark)

SYNTHETIC_START = pd.Timestamp("2019-07-10 18:00")
SPRUCE = 2  # C-2 boreal spruce


def synthetic_case(size: int = 201, wind: float = 20.0, wind_from: float = 270.0, ffmc: float = 90.0, bui: float = 64.0, fuel: np.ndarray | None = None,
                   centre_xy: tuple[float, float] = (0.0, 300_000.0), convergence_deg: float | None = None, pixel: float = 90.0, hours: int = 12) -> SIM.FireCase:
    """A made-up square grid (not real fuel, weather or ground): uniform C-2 spruce unless `fuel` is given, flat, with
    constant weather.  Centred on EPSG:3978 `centre_xy` (default: on the projection's central meridian, about 51.7 N,
    95 W in Ontario).  `convergence_deg=None` computes grid convergence at the centre as the simulator does."""
    fuel = np.full((size, size), SPRUCE, dtype=np.int32) if fuel is None else fuel
    rows, columns = fuel.shape
    west, north = centre_xy[0] - columns * pixel / 2, centre_xy[1] + rows * pixel / 2
    if convergence_deg is None:
        to_lonlat = Transformer.from_crs("EPSG:3978", "EPSG:4326", always_xy=True)
        lon0, lat0 = to_lonlat.transform(*centre_xy)
        lon1, lat1 = to_lonlat.transform(centre_xy[0], centre_xy[1] + 1000.0)
        convergence_deg = Geod(ellps="GRS80").inv(lon0, lat0, lon1, lat1)[0]
    weather = pd.DataFrame({
        "time_utc": pd.date_range(SYNTHETIC_START, periods=hours, freq="h"), "wind_speed_kmh": wind, "wind_direction_deg": wind_from, "ffmc": ffmc,
        "ffmc_daily": ffmc, "bui": bui, "day_of_year": 191, "temp_c": 25.0, "rh_pct": 30.0,
    })  # fmt: skip
    return SIM.FireCase(
        fire_id="SYNTH", west=west, north=north, pixel=pixel, fuel=fuel, fuel_as_mapped=fuel, elevation=np.zeros(fuel.shape, dtype=np.float32), weather=weather,
        convergence_deg=float(convergence_deg), ignition_cell=None, arrival=pd.DataFrame(), observation_times=pd.Series(dtype="datetime64[ns]"),
    )  # fmt: skip


def synthetic_ignition_xy(case: SIM.FireCase) -> tuple[float, float]:
    """Centre of the grid's middle cell, EPSG:3978."""
    rows, columns = case.shape
    return case.west + (columns // 2 + 0.5) * case.pixel, case.north - (rows // 2 + 0.5) * case.pixel


SYNTHETIC_LABELS = {"weather_source": "synthetic-constant-weather (NOT real weather)", "fuel_source": "synthetic-uniform-C-2 (NOT a real fuel grid)"}


def _percentiles(values: list[float]) -> dict[str, float]:
    return {"p50_s": float(np.percentile(values, 50)), "p95_s": float(np.percentile(values, 95)), "min_s": float(min(values)), "max_s": float(max(values)), "repeats": len(values)}


def benchmark(size: int, n_runs: int, repeats: int, seed: int = 385) -> dict:
    """Wall-clock p50/p95 of one 6 h `run_point` and of the full ensemble on a synthetic grid (after one warm-up)."""
    case = synthetic_case(size=size)
    xy = synthetic_ignition_xy(case)
    API.run_point(case, SYNTHETIC_START, ignition_xy=xy)
    single, ensemble = [], []
    for _ in range(repeats):
        tick = time.perf_counter()
        API.run_point(case, SYNTHETIC_START, ignition_xy=xy)
        single.append(time.perf_counter() - tick)
    for repeat in range(repeats):
        tick = time.perf_counter()
        run_monte_carlo(case, SYNTHETIC_START, ignition_xy=xy, seed=seed + repeat, n_runs=n_runs, mode="retrospective", **SYNTHETIC_LABELS)
        ensemble.append(time.perf_counter() - tick)
    return {"grid": f"{size}x{size} at 90 m", "single_run_point_6h": _percentiles(single), f"ensemble_n_runs_{n_runs}": _percentiles(ensemble)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monte Carlo spread bands on a SYNTHETIC grid (uniform spruce, constant wind; not real data).")
    parser.add_argument("--n-runs", type=int, default=DEFAULT_N_RUNS)
    parser.add_argument("--seed", type=int, default=385)
    parser.add_argument("--size", type=int, default=301, help="grid is size x size cells of 90 m")
    parser.add_argument("--wind-kmh", type=float, default=20.0)
    parser.add_argument("--wind-from-deg", type=float, default=270.0)
    parser.add_argument("--out", type=Path, help="write the GeoJSON here (default: print a summary only)")
    parser.add_argument("--benchmark", type=int, metavar="REPEATS", help="time run_point and the ensemble REPEATS times instead")
    args = parser.parse_args(argv)
    if args.benchmark:
        print(json.dumps(benchmark(args.size, args.n_runs, args.benchmark, args.seed), indent=2))
        return 0
    case = synthetic_case(size=args.size, wind=args.wind_kmh, wind_from=args.wind_from_deg)
    result = run_monte_carlo(case, SYNTHETIC_START, ignition_xy=synthetic_ignition_xy(case), seed=args.seed, n_runs=args.n_runs, mode="retrospective", **SYNTHETIC_LABELS)
    for feature in result.geojson["features"]:
        p = feature["properties"]
        print(f"{p['horizon_h']} h {p['band']}: {p['area_ha']:9.2f} ha  perimeter {p['perimeter_km']:7.2f} km  head {p['head_direction_deg']}")
    print(f"n_runs={args.n_runs} seed={args.seed} uncertainty_status={result.geojson['uncertainty_status']} runs touching grid edge={result.edge_runs} time={result.timings_s['total_s']:.2f}s")
    if args.out:
        args.out.write_text(to_json(result.geojson))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
