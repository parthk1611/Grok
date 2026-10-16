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
