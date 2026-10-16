# Ontario fire-spread dataset (v1)

This is the data product for the project's second capability, the wind-driven
fire-spread simulator. The simulator is physics-based, so this is a replay set
rather than a training table: real Ontario fires, each with the inputs a
simulator needs (wind, fuel moisture, fuel, terrain, an ignition point) beside
what the fire actually did, so simulated spread can be scored against observed
spread.

It is separate from the ignition panel. None of its fields (final size,
perimeters, satellite detections, response type) may be used as ignition
predictors.

## What it contains

All files are in `capability_2_fire_spread/data/processed/`. Paths below are relative to `capability_2_fire_spread/`.

| File | One row per | Rows |
| --- | --- | ---: |
| `ontario_fire_spread_master_v1.csv` | fire per satellite observation, with the fire's attributes repeated | 6,249 |
| `ontario_fire_spread_fires_v1.csv` | fire | 752 |
| `ontario_fire_spread_weather_hourly_v1.csv.gz` | fire per hour | 212,472 |
| `ontario_fire_spread_weather_daily_v1.csv` | fire per day | 8,853 |
| `ontario_fire_spread_arrival_cells_v1.csv.gz` | fire per 375 m cell, with when it was first seen burning | 118,400 |
| `ontario_fire_spread_detections_v1.csv.gz` | satellite detection matched to a fire | 300,601 |
| `ontario_fire_spread_v1_audit.json`, `ontario_fire_spread_v1_validation.json` | build audit and validation results | |

Per-fire grids for a spatial simulator are in `data/raw/fuel_terrain/windows/`:
`<fire_id>_fbp_fuel_90m.tif` and `<fire_id>_mrdem_dtm_90m.tif`, on one shared
90 m grid per fire. The cleaned final perimeters are in
`data/interim/spread_fire_perimeters.geojson`.

Scope: 752 Ontario wildfires of 40 ha or more from 2012–2025 that have a final
perimeter. Satellites saw 631 of them; those are the fires in the master table.
The 69 fires from 2025 have no rows because NASA has not yet published the
2025 yearly archive. 368 fires are flagged `trackable` (at least three growth
steps and detections over at least 20% of the perimeter).

`split` is `calibration` for 2012–2020 (392 fires, 3,242 rows) and `holdout`
for 2021–2024 (239 fires, 3,007 rows). Tune simulator settings on calibration
fires only.

## Sources

| # in the team plan | Source | Used for | Resolution | Licence | Retrieved |
| --- | --- | --- | --- | --- | --- |
| 1 | Ontario Fire Disturbance Point and Area (Ontario GeoHub) | Fire list, start point, dates, cause, final perimeter | Points and polygons | Open Government Licence – Ontario | 2026-10-08 |
| 2 | NASA FIRMS VIIRS 375 m, Suomi NPP (2012–2024) and NOAA-20 (2018–2024), yearly Canada files | Timestamped fire growth | 375 m; a few passes a day | NASA open data | 2026-10-08 |
| 3 | ERA5 / ERA5-Land reanalysis through the Open-Meteo archive (`era5_seamless`) | Hourly weather and wind; fire-weather codes computed from it | 0.1° to 0.25°; hourly | CC BY 4.0; Copernicus licence | 2026-10-08 |
| 5 | FBP Fuel Types 2024 (CWFIS web coverage service) | Fuel type around each fire | 30 m, sampled to 90 m | Open Government Licence – Canada | 2026-10-08 |
| 7 | MRDEM-30 digital terrain model (CanElevation) | Elevation, slope, aspect | 30 m, resampled to 90 m | Open Government Licence – Canada | 2026-10-08 |

Request details, file sizes and checksums are in the `manifest.json` files
under `data/raw/`.

Three departures from the team plan:

- **ERA5-Land access.** The plan names the Copernicus Climate Data Store,
  which needs a personal API key. The same reanalysis was fetched through
  Open-Meteo, which needs none. Temperature and humidity are ERA5-Land; wind
  and precipitation come from ERA5 where ERA5-Land has no value. The variable
  definitions here (noon hour, 24-hour rain, start-up values) must be matched
  by whatever computes the ignition-side and live features.
- **SCANFI (#6) is not used.** The fuel map already encodes percent conifer for
  mixedwood stands (codes 625, 650, 675), which is what the plan wanted SCANFI
  for. Crown closure is therefore absent.
- **HRDPS (#4) is not used.** It is the live feed and keeps only 30 days; it
  has no role in a historical replay set.

## How the fires' growth was rebuilt

1. A detection is matched to a fire when it is a vegetation fire (`type` 0) of
   nominal or high confidence, lies within 750 m of the fire's final perimeter,
   and is dated from 3 days before the reported start to 1 day after the
   reported out date. A detection near two fires goes to the nearer perimeter.
2. A fire's detections are split into observations wherever more than 90
   minutes pass with none. One observation is one satellite pass, or two passes
   by different satellites minutes apart.
3. Detections are placed on a 375 m grid in EPSG:3978. A cell's arrival time is
   the first observation in which it was seen burning.
4. For each observation, every newly burning cell is paired with the nearest
   cell seen earlier. The distances give the spread distance; the mean offset
   gives the direction. For a fire's first observation the reference is the
   reported ignition point.

All bearings are degrees clockwise from true north, corrected for the grid's
convergence.

## Column dictionary: master table

Blank means not available; it never means zero.

**Fire attributes** (repeated on each of the fire's rows)

| Columns | Meaning |
| --- | --- |
| `fire_id` | `<year>_<Ontario fire identifier>`, for example `2019_RED23`. |
| `dataset_version`, `split` | `fire-spread-v1.0.0`; `calibration` or `holdout`. |
| `fire_year`, `district_code` | Year, and the letters of the identifier (the fire management district). |
| `start_date`, `out_date`, `duration_days` | Reported start and declared-out dates (no time of day), and days between. |
| `reported_size_ha`, `perimeter_area_ha`, `size_class` | Final size as reported, area of the perimeter polygon, and a size band. |
| `cause`, `cause_group` | Decoded cause, and `lightning`, `human` or `unknown`. |
| `response_code` | How the fire was managed: `MON` monitored, `FUL` full suppression, `MOD` modified. Use it to interpret results; never as an ignition predictor. |
| `ignition_longitude`, `ignition_latitude` | The agency's reported fire location, used as the ignition point (WGS84). |
| `ignition_inside_perimeter`, `ignition_distance_to_perimeter_m` | Whether that point falls inside the final perimeter, and how far outside if not. |
| `centroid_longitude`, `centroid_latitude`, `utc_offset_hours` | Perimeter centroid; −6 west of 90°W, otherwise −5 (standard time all year). |
| `fire_detections`, `fire_observations`, `fire_growth_steps` | Totals for the fire. |
| `detected_share_of_perimeter` | Area of cells seen burning over perimeter area. Can exceed 1 because cells are 375 m wide. |
| `trackable` | At least three growth steps and a detected share of at least 0.2. |
| `first_detection_utc`, `last_detection_utc`, `first_detection_days_after_start` | First and last satellite detection, and the lag from the reported start. |
| `fuel_map_year`, `fuel_window_file` | 2024, and the fire's fuel grid. |
| `fuel_around_*`, `fuel_inside_*` | Fuel mix in the window outside the perimeter and inside it: `_conifer_share`, `_mixedwood_share`, `_deciduous_share`, `_open_share`, `_non_fuel_share`, `_water_share`, `_unrecognised_code_share`, `_percent_conifer` (over burnable cells), `_dominant_fuel_type`, `_cells`. |
| `terrain_window_file`, `terrain_valid_share` | The fire's elevation grid and the share of its cells with a value. |
| `terrain_window_*`, `terrain_inside_*` | Terrain over the whole window and inside the perimeter: `_elevation_mean_m`, `_elevation_range_m`, `_slope_mean_pct`, `_slope_p90_pct`, `_slope_mean_deg`, `_aspect_sin`, `_aspect_cos` (downslope direction from grid north, slope-weighted). |

**The observation**

| Columns | Meaning |
| --- | --- |
| `observation`, `time_utc`, `time_local` | Sequence number within the fire and the time of the pass. |
| `day_or_night`, `satellites` | `D` or `N`; `N` is Suomi NPP, `N20` is NOAA-20. |
| `detections`, `cells_detected` | Detections in this observation and the cells they fall in. |
| `new_cells`, `new_area_ha` | Cells seen burning for the first time, and their area (14.06 ha each). |
| `cumulative_cells`, `cumulative_area_ha` | Everything seen burning up to and including this observation. |
| `hours_since_previous_observation`, `hours_since_first_observation` | Time steps. |
| `growth_reference` | `earlier_detections`, or `reported_ignition_point` on the first observation. |
| `spread_distance_mean_m`, `spread_distance_p90_m`, `spread_distance_max_m` | Distance from new cells to the nearest earlier cell. |
| `spread_rate_m_per_h` | `spread_distance_p90_m` over the hours since the previous observation. |
| `spread_bearing_deg`, `head_bearing_deg` | Direction of the mean offset of all new cells, and of the farthest 10%. |
| `spread_directionality` | 0 is growth in all directions, 1 is growth in one direction. |
| `frp_total_mw`, `frp_max_mw` | Fire radiative power of the detections. |

**Weather over the interval since the previous observation** (the 12 hours
before a fire's first observation)

| Columns | Meaning |
| --- | --- |
| `weather_window_start_utc`, `weather_hours` | Start of the interval and the hourly values in it. |
| `wind_u_kmh`, `wind_v_kmh` | Mean east-west and north-south wind components (positive toward east and north). |
| `wind_speed_mean_kmh`, `wind_speed_max_kmh` | Mean and maximum 10 m wind speed. |
| `wind_direction_deg`, `downwind_bearing_deg` | Bearing the mean wind blows from, and the opposite bearing it pushes the fire toward. |
| `wind_steadiness` | Vector-mean speed over mean speed; 1 is a steady direction. |
| `temp_mean_c`, `temp_max_c`, `rh_mean_pct`, `rh_min_pct`, `precip_total_mm` | Temperature, humidity and rain over the interval. |
| `head_vs_wind_angle_deg`, `spread_vs_wind_angle_deg` | Angle (0–180°) between the observed head or mean spread bearing and the downwind bearing. |

**Fire weather for the day** (the local date at the middle of the interval)

| Columns | Meaning |
| --- | --- |
| `fire_weather_date`, `fire_weather_days_since_startup` | The date used and how many days the codes had been running. |
| `noon_temp_c`, `noon_rh_pct`, `noon_wind_kmh`, `rain_24h_mm` | Local-noon weather and rain in the 24 hours to noon. |
| `ffmc`, `dmc`, `dc`, `isi`, `bui`, `fwi` | Fine Fuel Moisture, Duff Moisture and Drought Codes; Initial Spread, Buildup and Fire Weather Indices. |
| `fine_fuel_moisture_pct` | Moisture content implied by FFMC: `147.2 × (101 − FFMC) / (59.5 + FFMC)`. |

The fire-weather codes use the standard Van Wagner (1987) daily equations in
`scripts/fire_weather_index.py`, started from FFMC 85, DMC 6 and DC 15 on
1 April (or 30 days before the fire if earlier) at each fire's location.

## Time semantics

This is a hindcast replay set, not an operational forecast archive.

- Weather is reanalysis for the hours in which the fire was spreading. That is
  the right input for replaying a simulator; it is not what a forecaster had.
- The fuel map is from 2024. Inside the perimeter of an older fire it shows
  regrowth, not what burned: 29% conifer inside perimeters against 59% just
  outside. Use `fuel_around_*` to describe what a fire burned through.
- The final perimeter is used only to decide which detections belong to a
  fire. It is an outcome and must not be given to a simulator as an input.
- Fire attributes such as final size, out date and `fire_*` totals are
  outcomes too.

## Validation snapshot (2026-10-08)

All 18 structural checks pass (unique keys, areas that never shrink, arrival
cells that match the totals, plausible weather, no missing weather, fuel or
terrain on any row). Beyond structure:

- Area seen by satellite tracks the agency's perimeter area across fires
  (rank correlation 0.86); the median fire has 77% of its perimeter area seen.
- The first detection comes a median 2 days after the reported start.
- The computed Fire Weather Index on a fire's start date agrees only moderately
  with the one the agency recorded (r = 0.47, medians 14.6 and 16.5). The
  agency value comes from a nearby station; this one is reanalysis.
- Fires run downwind: the head is within 45° of downwind on 42% of growth
  steps against 25% by chance, rising from 35% in wind under 10 km/h to 54% at
  20 km/h or more.
- Fires grow more in drier, windier weather: median new area per growth step
  rises from 28 ha when the Initial Spread Index is under 2 to 211 ha at 12 or
  more.

## Known limits

- **Time resolution.** The median gap between observations is 12.2 hours; 12%
  are 6 hours or less and 18% are over a day. The proposal's 1- and 3-hour
  projections can be produced but not checked against these observations.
- **Satellites see flames, not burned ground.** A pass records where fire was
  active at that moment, under clear sky. Cells that burned between passes or
  under cloud are missed, so detected area is a lower bound.
- **Observations exist only when fire is active.** There are no rows for quiet
  periods, so this table cannot by itself answer "does the fire grow today?".
- **Fires of 40 ha or more only**, and 2025 is waiting on NASA's archive.
- **Fuel map vintage**, as above.
- **Start dates have no time of day**, and the reported point is outside the
  final perimeter for 102 fires (median 112 m away).
- **Suppression.** 212 of the 752 fires had full suppression, which shortens
  spread relative to a free-burning model.

## Reproducing

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

Every script appends what it did, with counts, to
`logs/fire_spread_build_log.txt`.
