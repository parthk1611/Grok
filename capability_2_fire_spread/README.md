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
| `logs/fire_spread_build_log.txt` | Timestamped log of every download, filter and count |
| `scripts/` | The build, in the order listed below |
| `tests/` | Unit tests for the builders |
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
