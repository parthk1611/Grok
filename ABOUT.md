# COMP-385: Ontario ignition-risk dataset

This repository builds a **retrospective, time-safe wildfire ignition dataset**
for COMP-385. Its prediction question is:

> For a 10 km grid cell in Ontario's managed Fire Region, will one or more
> wildfire ignitions occur on this local calendar day?

It is **not** a fire-spread, burned-area, perimeter, or final-size model. Do
not add final perimeters, final fire size, post-event burn products, future
detections, or any same-/future-day observation as explanatory variables.

## Current dataset

The generated v1 feature panel is:

`data/processed/ontario_fire_region_10km_daily_ignition_features_v1.csv.gz`

It is deliberately ignored by Git because it is a large deterministic build
artifact (about 747 MB compressed in the 2026-10-05 rebuild). It has:

| Property | Value |
| --- | --- |
| Feature version | `ignition-feature-v1.0.0` |
| Geography | Ontario Fire Region: official response-plan sectors where `RES_SECTOR != OFR` |
| Spatial unit | Existing 10 km EPSG:3978 grid cell |
| Time unit | Local `America/Toronto` calendar day, issued at 00:00 local time |
| Study period | 2010-05-01 through 2019-10-31; May 1–October 31 only |
| Grid cells | 9,673 |
| Dates | 1,840 |
| Rows | 17,798,320 |
| Key | (`grid_id`, `ignition_date`) |
| Target source | Ontario NFDB ignition records, filtered to the study region and fire season |

The matching build audit is
`data/processed/ontario_fire_region_10km_daily_ignition_features_v1_audit.json`.
Read it before using a rebuilt panel: it records paths, row counts, coverage,
and time-safety validations for that exact output.

## Column dictionary

An empty value is intentional unless stated otherwise. Missingness must not be
silently changed to zero or globally imputed.

### Key and labels

| Column | Meaning |
| --- | --- |
| `grid_id` | Stable identifier for a 10 km grid cell. Join to `data/interim/ontario_fire_region_10km_grid.csv` for its projected origin and geographic centroid. |
| `ignition_date` | Target local calendar date (`YYYY-MM-DD`) in `America/Toronto`. |
| `ignition` | Primary binary target: `1` when at least one selected NFDB ignition occurred in the cell on that date; otherwise `0`. |
| `ignition_count` | Number of selected NFDB ignition records in the cell on the date. It is retained for auditing/count models; use `ignition` for the primary binary task. |
| `large_fire_200ha` | Secondary binary label: `1` when any ignition record on that cell-day has final recorded size at least 200 ha. This is a label, never an input feature. |

### Dataset/version and calendar features

| Column | Meaning |
| --- | --- |
| `feature_set_version` | Feature-contract identifier, currently `ignition-feature-v1.0.0`. |
| `issue_time_utc` | UTC instant corresponding to 00:00 local time on `ignition_date`; daylight-saving-aware. All dynamic input must precede this instant. |
| `calendar_day_of_year` | Day number within the calendar year. |
| `calendar_doy_sin`, `calendar_doy_cos` | Cyclical sine/cosine encoding of day of year. Use together rather than interpreting either alone. |
| `calendar_is_weekend` | `1` for Saturday/Sunday, otherwise `0`. |
| `calendar_day_length_hours` | Astronomical day length calculated from the cell-centroid latitude and target date. |

### Static terrain features

Terrain comes from legacy NRCan CDEM and is static ground terrain only. It is
not a fuel, vegetation, road, settlement, or disturbance proxy. Each value is
summarized from nine equal-area samples in the existing 10 km cell.

| Column | Meaning |
| --- | --- |
| `terrain_elevation_mean_m` | Mean available CDEM elevation, metres. Blank means no CDEM elevation sample was returned for the cell. |
| `terrain_slope_deg` | Slope in degrees fitted from the local elevation samples. Blank when terrain coverage is insufficient for a slope. |
| `terrain_aspect_sin`, `terrain_aspect_cos` | Sine and cosine representation of fitted terrain aspect. They avoid the 0°/360° discontinuity. Blank when slope/aspect cannot be estimated. |
| `terrain_ruggedness_m` | Elevation variability/ruggedness across available samples, metres. Blank with no elevation coverage. |
| `terrain_sample_count` | Number of valid elevation samples returned, from 0 to 9. |
| `terrain_coverage_fraction` | `terrain_sample_count / 9`; use this with terrain values to assess quality. |
| `terrain_source_id` | Source/version identifier: `nrcan_cdem_1945_2011`. |
| `terrain_source_vintage_end_year` | Last year represented by the legacy CDEM source: `2011`. |

### Prior-day CWFIS weather and Fire Weather Index features

These fields use **only the CWFIS observation dated `ignition_date - 1 day`**.
For each cell, the builder selects up to four nearest station candidates, then
inverse-distance-weights candidates that both existed by that observation date
and have a non-missing FWI value. The result is a retrospective hindcast
feature, not an operational forecast replay: the public archive has no
per-record historical `available_at` time.

| Column | Meaning |
| --- | --- |
| `cwfis_source_id` | `cwfis_fwi_2010s_v3_ll`, the archived CWFIS 2010s longitude/latitude FWI dataset. |
| `cwfis_observation_date` | Source date considered for the current target day: always the prior calendar day. It does **not** alone mean a usable station value existed. |
| `cwfis_station_count` | Number of usable selected stations, from 0 to 4. |
| `cwfis_nearest_station_km` | Distance to the nearest usable selected station, km; blank when station count is zero. |
| `cwfis_observation_coverage_fraction` | Usable selected-station count divided by four candidate stations. |
| `cwfis_imputed_station_fraction` | Fraction of usable selected records whose CWFIS option flags indicate interpolation/imputation (`IDW` or `M=`); blank when no station is usable. |
| `cwfis_calcstatus_nonzero_fraction` | Fraction of usable selected records with non-zero CWFIS calculation status; blank when no usable status is present. |
| `cwfis_missing` | Quality flag: `1` if no selected usable station exists for the prior day, otherwise `0`. Keep it in training. |
| `cwfis_temp_c` | Distance-weighted prior-day noon air temperature, °C. |
| `cwfis_rh_pct` | Distance-weighted prior-day relative humidity, percent. |
| `cwfis_wind_speed_kmh` | Distance-weighted prior-day wind speed, km/h. |
| `cwfis_precip_mm` | Distance-weighted prior-day precipitation, mm. |
| `cwfis_ffmc` | Fine Fuel Moisture Code. |
| `cwfis_dmc` | Duff Moisture Code. |
| `cwfis_dc` | Drought Code. |
| `cwfis_isi` | Initial Spread Index. |
| `cwfis_bui` | Buildup Index. |
| `cwfis_fwi` | Fire Weather Index. |
| `cwfis_dsr` | Daily Severity Rating. |
| `cwfis_precip_7d_mm` | Sum of the preceding seven CWFIS daily precipitation values. Blank unless all seven values are available. |
| `cwfis_precip_7d_coverage_days` | Number of usable daily precipitation values in that seven-day window, 0–7. |

All numeric CWFIS weather/FWI values are blank when `cwfis_missing = 1`.
Do not replace blanks with zero: a true zero has a physical meaning. Use the
coverage and quality columns in any model.

### Past-only ignition-history features

These are derived only from NFDB records whose ignition date is **strictly
earlier** than the target date. A neighbourhood is the existing 3 × 3 group of
10 km grid cells centred on the current cell. Because the committed input event
history begins in 2010, early-history values are left censored rather than
inventing pre-2010 fires.

| Column | Meaning |
| --- | --- |
| `fire_history_source_id` | `nfdb_ontario_ignition_labels`. |
| `fire_history_same_cell_30d` | Earlier selected ignitions in the same cell during the completed 30-day lookback window. |
| `fire_history_neighborhood_30d` | Earlier selected ignitions in the cell's 3 × 3 neighbourhood during the completed 30-day lookback window. |
| `fire_history_same_cell_365d` | Earlier selected ignitions in the same cell during the completed 365-day lookback window. |
| `fire_history_neighborhood_365d` | Earlier selected ignitions in the 3 × 3 neighbourhood during the completed 365-day lookback window. |
| `fire_history_days_since_same_cell` | Days since an earlier selected ignition in the same cell. Blank means no earlier event exists in the available 2010+ history; it does not mean zero days. |

## Missingness in the 2026-10-05 full rebuild

The full scan covered all 17,798,320 rows and found no malformed rows. Blank
cells, plus `NA`, `N/A`, and `NULL` tokens, were counted as nulls.

| Fields | Null rate | Interpretation |
| --- | ---: | --- |
| Key, label, version, calendar, source-ID, count, coverage, and completed-window history fields | 0% | Complete by construction. |
| `terrain_elevation_mean_m`, `terrain_ruggedness_m` | 0.010338% (1,840 rows) | One cell has no returned elevation coverage. |
| `terrain_slope_deg`, `terrain_aspect_sin`, `terrain_aspect_cos` | 0.496227% (88,320 rows each) | Forty-eight cells lack sufficient terrain samples for a fitted slope/aspect. |
| `cwfis_nearest_station_km`, `cwfis_imputed_station_fraction`, `cwfis_calcstatus_nonzero_fraction`, all CWFIS daily weather/FWI values | 52.482010% (9,340,916 rows each) | No usable selected prior-day CWFIS station. Check `cwfis_missing`. |
| `cwfis_precip_7d_mm` | 62.610842% (11,143,678 rows) | Fewer than seven usable preceding precipitation observations; use its coverage field. |
| `fire_history_days_since_same_cell` | 77.106109% (13,723,592 rows) | No earlier observed same-cell ignition in the available history. |

This is not a reason to delete half the dataset or to use zero imputation. The
weather gap is substantial enough to warrant targeted improvement work, but
the missingness flags are currently part of the honest source-quality record.

## What has been done

1. **Defined the study universe.** The official Ontario Fire Response Plan
   Area is projected to EPSG:3978. A 10 km grid is retained when its centroid
   lies in the managed region, plus any edge cell containing a selected
   ignition.
2. **Prepared NFDB ignition labels.** Ontario (`SRC_AGENCY = ON`) point records
   for 2010–2019 are validated for dates and coordinates. The fire-season and
   region filter retains 8,109 events. They yield 7,785 positive cell-days,
   including 318 large-fire positive cell-days.
3. **Built the label panel.** The output has every grid cell for every
   fire-season day: 9,673 × 1,840 = 17,798,320 label rows. The panel is highly
   imbalanced: positive ignition cell-days are about 0.0437% of rows.
4. **Added static terrain.** Nine CDEM samples per cell produce elevation,
   slope, aspect, ruggedness, and explicit coverage fields. The committed
   terrain summary is `data/interim/cdem_terrain_10km_summary.csv`.
5. **Added time-bounded CWFIS features.** The CWFIS 2010s archive is checksum
   pinned in `data/raw/cwfis/manifest.json`. Only prior-day weather/FWI records
   enter each target row; station distance, count, interpolation, status, and
   missingness remain visible.
6. **Added past-only ignition history.** History is updated after the target
   date completes, preventing same-day or future fire leakage.
7. **Validated the build.** The feature builder checks complete label schema,
   row universe, daily cell set, key ordering/uniqueness, terrain alignment,
   exact output row count, prior-day CWFIS timing, and history leakage. The
   current audit reports zero CWFIS timing violations and no prohibited
   spread/post-event predictors.
8. **Rebuilt the full panel on 2026-10-05.** Official NFDB, Ontario Fire
   Region, and CWFIS inputs were downloaded; the CWFIS archive matched its
   pinned SHA-256 (`67e706…0329246`); the labels, feature panel, audit, and
   full column-wise null scan were generated.

## Reproducing the dataset

The downloaded archives and full cell-day outputs are ignored by Git. From a
fresh checkout, run:

```sh
python scripts/download_nfdb.py
python scripts/prepare_ontario_ignitions.py
python scripts/download_ontario_fire_region.py
python scripts/build_cpa_daily_ignition_labels.py
python scripts/download_cwfis_fwi.py
python scripts/build_ignition_feature_panel.py
python -m unittest discover -s tests -v
```

`build_cpa_daily_ignition_labels.py` requires `shapely`. The 2026-10-05 rebuild
used an isolated Python environment with `shapely` installed. The feature
builder itself streams by date but the full output is large; do not commit it
without an explicit team decision.

## Training guidance

- Use a chronological holdout and preferably a spatial block as well. A random
  row split leaks nearby dates and recurrent-cell patterns.
- Keep all rows. For a model with native missing-value handling, pass numeric
  weather/terrain blanks through and retain the quality fields. For models
  without it, fit median imputation and missing indicators on the training
  split only; never fit them on all years.
- Treat `fire_history_days_since_same_cell` as a censored “no earlier observed
  event” state, not ordinary weather missingness. Add a training-derived
  indicator before any numeric imputation.
- Train with class weighting or negative downsampling if needed, but preserve
  natural prevalence in validation/test data. Accuracy is misleading at this
  target prevalence; report precision–recall, recall at an operational alert
  threshold, calibration, and results split by CWFIS-covered vs. uncovered
  rows.
- Do not use `large_fire_200ha` as an input to the ignition model. It is a
  separate, extremely rare outcome label.

## Planned work and decision boundaries

1. **Diagnose weather coverage.** Map/aggregate `cwfis_missing` by year,
   season, geography, and target class before changing the feature design.
2. **Test CWFIS station-selection sensitivity.** Compare the current four
   nearest stations with a larger candidate set and an explicit maximum-distance
   policy. Keep prior-day timing and quality flags. Adopt it only if held-out
   coverage and model performance improve without implausible extrapolation.
3. **Consider ERA5-Land for a separate hindcast enrichment.** It can improve
   spatial weather completeness but is a reanalysis, so it must remain clearly
   labelled retrospective/hindcast. It cannot justify an operational forecast
   claim without archived pre-issue forecasts.
4. **Add other families only with historical provenance.** Historic land
   cover/fuels, roads, settlement, lightning, satellite phenology, and forecast
   weather require dated source assets, an availability/quality ledger, and
   leakage tests. Do not backfill current maps into 2010–2019.
5. **Keep spread work separate.** A future spread dataset needs
   incident-conditioned candidate cells and dated fire-front/perimeter history;
   the current ignition panel cannot be repurposed as a spread target.

For implementation details and source semantics, also read `AGENTS.md`,
`docs/ignition_features.md`, and
`data/metadata/ignition_feature_source_registry_v1.json`.
