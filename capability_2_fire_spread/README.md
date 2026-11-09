# AI capability 2: wind-driven fire spread

Everything for the project's second capability lives in this folder: the
scripts that collect and clean the data, the master fire-spread dataset they
produce, its documentation, tests, and a log of every action taken.

The dataset is a replay set for the spread simulator: 752 Ontario wildfires of
40 ha or more from 2012–2025, with the growth of 631 of them rebuilt from
satellite passes and set beside hourly wind, fire-weather codes, fuel and
terrain. It is separate from the ignition panel in the repository root, and
none of its columns may be used as ignition predictors.

| Path | What it is |
| --- | --- |
| `data/processed/ontario_fire_spread_master_v1.csv` | The master dataset: one row per fire per satellite observation (6,249 rows × 119 columns) |
| `data/processed/` | The fire list, hourly and daily weather, matched detections, arrival cells, audit and validation results |
| `docs/fire_spread_dataset.md` | Data dictionary, sources, method, validation and known limits |
| `docs/comparison_with_cell2fire.md` | How the simulator differs from Cell2Fire, and what each has been tested against |
| `docs/fire_spread_eda.md`, `docs/eda/` | Exploratory analysis: ten charts of the dataset, each with an explanation (`scripts/eda_fire_spread.py` redraws them) |
| `logs/fire_spread_build_log.txt` | Timestamped log of every download, filter and count |
| `scripts/` | The build, in the order listed below |
| `tests/` | Unit tests for the builders and the simulator |
| `scripts/fbp_spread.py`, `scripts/simulate_spread.py` | The spread simulator: Canadian FBP rates of spread on a 90 m grid |
| `scripts/run_spread_replay.py`, `data/processed/simulation/` | Tuning on 2012–2020 fires, scoring on 2021–2024 fires, and the results |
| `viewer/fire_spread_viewer.html` | Interface: replay fires through the simulator on a zoomable map and compare with satellite passes |
| `scripts/spread_basemap.py`, `data/raw/basemap/manifest.json` | The viewer's base map (Canada Base Map, Natural Resources Canada) and the record of what was fetched |
| `docs/fire_spread_simulator.md` | How the simulator works, its scores and its limits |
| `data/raw/*/manifest.json`, `data/interim/*_audit.json` | What was downloaded and what each step kept or dropped |

The downloaded source files and the intermediate tables are not in git; the
scripts rebuild them. Run from the repository root:

```sh
pip install -r capability_2_fire_spread/requirements.txt
cd capability_2_fire_spread
python scripts/download_fire_disturbance.py
python scripts/build_spread_fire_catalogue.py
python scripts/download_firms_viirs.py
python scripts/build_spread_progression.py
python scripts/download_spread_weather.py
python scripts/build_spread_weather.py
python scripts/download_spread_fuel_windows.py
python scripts/build_spread_fuel_summary.py
python scripts/download_spread_terrain_windows.py
python scripts/build_spread_terrain_summary.py
python scripts/build_spread_master.py
python scripts/validate_spread_master.py
python -m unittest discover -s tests -v
```

## Point-start API and simulator tests (T1.6)

`scripts/spread_api.py` wraps `simulate_spread.simulate` for a fire that starts at one
point: `run_point(case, start_utc, ignition_xy=(x, y) | lonlat=(lon, lat), horizons=(1, 3, 6), settings=None, weather_override=None, seed=None)`
returns the arrival-time grid, a nested burned mask and the area in hectares for each horizon.
It reads the fuel map as mapped (never the burn-scar grid repaired with the final perimeter),
forces `repair_burn_scar=False`, ignores the observed arrival cells, and raises if the weather does
not cover the run. `seed` is recorded but nothing is perturbed yet (Monte Carlo is T1.7).
Note: `Settings().ros_scale` defaults to 1.0; pass the tuned value explicitly if you want it.

Pinned environment and tests (run from the repository root, Python 3.13 tested):

```bash
python3 -m venv .venv-spread
.venv-spread/bin/pip install -r capability_2_fire_spread/requirements-lock.txt
cd capability_2_fire_spread
../.venv-spread/bin/python -m unittest discover -s tests -v
cd .. && .venv-spread/bin/python -m unittest tests.test_contracts -v
```

`tests/test_spread_point_api.py` uses made-up grids only (no dataset needed). Two tests are
marked `expectedFailure` because they document simulator artefacts: fire still squeezes through a
one-cell *diagonal* wall (the one-cell straight wall leak was fixed), and a diagonal wind burns about
11% more area at 6 h than an axis-aligned one (31% more at 1 h).

## Monte Carlo burn-probability bands (T1.7)

`scripts/spread_montecarlo.py` runs `run_point` many times with perturbed wind speed, wind direction,
FFMC and `ros_scale` (base 0.8) and writes the p10/p50/p90 contours for 1, 3 and 6 h as a GeoJSON
FeatureCollection that validates against `contracts/spread_output.schema.json` (v0.2.x). The default
perturbation sizes are **provisional, not calibrated** (calibration is T2.5). Method, measured band
areas, runtime and the wind-azimuth grid bias are in `docs/spread_montecarlo.md`.

Demo on a made-up grid (uniform spruce, constant 20 km/h west wind; NOT real data), from `capability_2_fire_spread`:

```bash
../.venv-spread/bin/python scripts/spread_montecarlo.py --n-runs 100 --seed 385 --out /tmp/spread_mc.json
../.venv-spread/bin/python scripts/spread_montecarlo.py --benchmark 20 --size 301 --n-runs 100   # wall-clock p50/p95
../.venv-spread/bin/python scripts/spread_direction_bias.py                                      # azimuth bias tables
```

From Python: `run_monte_carlo(case, start_utc, seed=..., mode=..., weather_source=..., fuel_source=..., ignition_xy=... | lonlat=...)`
returns a `MonteCarloResult` whose `.geojson` is the payload (`to_json` gives canonical text; the same seed gives the
same bytes apart from `run_timestamp`). `mode`, `stale_level`/`age_minutes` and the source labels describe the inputs,
so the caller must supply them.
