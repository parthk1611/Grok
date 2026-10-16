# Ontario ignition EDA and feature guide

Analysis date: 2026-10-05. No model fitting or data modification.

## Findings

- 7,785 positive cell-days (0.04374%); 2,285 negatives per positive. An all-negative classifier reaches 99.95626% accuracy while detecting zero ignitions.
- Daily weather/FWI is missing on 9,340,916 rows (52.48%). Seven-day precipitation is missing on 62.61%. Keep these rows and quality flags.
- No earlier observed same-cell event is recorded for 77.11% of rows. This censored history state differs from unavailable weather.
- 3,454 positive cell-days (44.37%) lack daily weather. Complete-case deletion would discard these recorded ignitions.
- The largest annual weather gap occurs in 2015: only 16 of 1,779,832 rows have weather (99.99910% missing). In 2016, 98.53% of rows lack weather. The highest pooled monthly ignition rate is in July (8.76 per 10,000 cell-days).
- Two quality fields (cwfis_imputed_station_fraction and cwfis_calcstatus_nonzero_fraction) are zero whenever observed. Counts and their coverage fractions are deterministic rescalings. They remain in the recommended shared source set; their incremental usefulness should be tested inside training splits.
- Coverage, seasonal timing and nearby observations can confound apparent feature differences. Class-distribution plots do not prove causal effects or predictive usefulness.

## Timing and predictor contract

This panel estimates whether at least one recorded ignition occurs in a 10 km cell on a target local calendar day. Issue time is 00:00 America/Toronto. Weather is from t-1; seven-day rain uses seven completed preceding dates; ignition histories exclude the target date. Archives lack historical per-record available_at times. This is retrospective hindcast, not a validated operational forecast replay.

Use ignition as the binary target. Exclude ignition_count and large_fire_200ha from predictors. Use grid_id and ignition_date to join and split, then remove them from the model matrix. Also exclude issue_time_utc, cwfis_observation_date and source/version metadata. Final perimeters, final fire size and future observations must not become ignition predictors.

Logistic regression: training-only imputation, numeric scaling and explicit missingness/censoring indicators. Histogram gradient boosting: preserve NaNs and quality fields. The baseline uses training-label prevalence and no predictors. The 36 columns are source inputs; derived indicators may increase the final matrix width.

## Annual aggregates

| Year | Positive cell-days | Weather missing |
| --- | ---: | ---: |
| 2010 | 661 | 27.11026% |
| 2011 | 1,245 | 31.21373% |
| 2012 | 1,407 | 36.73319% |
| 2013 | 527 | 36.80072% |
| 2014 | 293 | 29.11028% |
| 2015 | 584 | 99.99910% |
| 2016 | 573 | 98.53245% |
| 2017 | 736 | 72.90508% |
| 2018 | 1,250 | 67.38535% |
| 2019 | 509 | 25.02995% |

## Feature dictionary

- **calendar_day_of_year** (Calendar / solar): Day number within year. Why: Tests seasonal timing; compare with cyclic encodings during training. Missing: 0.00%.
- **calendar_doy_sin** (Calendar / solar): Sine of annual day angle. Why: Paired with cosine, represents a repeating annual cycle without an end-of-year jump. Missing: 0.00%.
- **calendar_doy_cos** (Calendar / solar): Cosine of annual day angle. Why: Completes the cyclic seasonal representation; neither component should be interpreted alone. Missing: 0.00%.
- **calendar_is_weekend** (Calendar / solar): Saturday/Sunday flag. Why: Tests a possible human-activity pattern; usefulness has not been established. Missing: 0.00%.
- **calendar_day_length_hours** (Calendar / solar): Astronomical daylight hours. Why: Provides latitude-and-date solar context; not a direct fuel-moisture observation. Missing: 0.00%.
- **terrain_elevation_mean_m** (Static terrain): Mean sampled ground elevation (m). Why: Tests differences associated with static topographic context. Missing: 0.01%.
- **terrain_slope_deg** (Static terrain): Fitted terrain slope (degrees). Why: Describes local ground inclination; relevance to ignition must be evaluated. Missing: 0.50%.
- **terrain_aspect_sin** (Static terrain): Sine of terrain aspect. Why: Together with cosine, encodes slope orientation without the 0/360-degree discontinuity. Missing: 0.50%.
- **terrain_aspect_cos** (Static terrain): Cosine of terrain aspect. Why: Completes the orientation pair; does not substitute for a vegetation or fuel map. Missing: 0.50%.
- **terrain_ruggedness_m** (Static terrain): Variability among sampled elevations (m). Why: Describes local topographic variation, rather than vegetation, access or fire spread. Missing: 0.01%.
- **cwfis_temp_c** (Prior-day weather): Prior-day noon air temperature (C). Why: Tests atmospheric warmth as a candidate signal of ignition conditions. Missing: 52.48%.
- **cwfis_rh_pct** (Prior-day weather): Prior-day relative humidity (%). Why: Tests atmospheric dryness; not a direct measure of fuel moisture. Missing: 52.48%.
- **cwfis_wind_speed_kmh** (Prior-day weather): Prior-day wind speed (km/h). Why: Describes wind conditions; direction and future hourly winds are absent. Missing: 52.48%.
- **cwfis_precip_mm** (Prior-day weather): Prior-day precipitation (mm). Why: Represents recent rain; zero rain and missing rain must remain distinct. Missing: 52.48%.
- **cwfis_precip_7d_mm** (Prior-day weather): Total rain over seven completed prior dates (mm). Why: Captures recent wet/dry conditions over a longer window; blank unless all seven observations exist. Missing: 62.61%.
- **cwfis_ffmc** (FWI components): Fine Fuel Moisture Code. Why: Describes dryness of litter and fine surface fuels relevant to ignition. Missing: 52.48%.
- **cwfis_dmc** (FWI components): Duff Moisture Code. Why: Describes moisture in moderately deep organic layers; adds a longer moisture-memory signal. Missing: 52.48%.
- **cwfis_dc** (FWI components): Drought Code. Why: Describes deep organic-layer moisture and seasonal drought. Missing: 52.48%.
- **cwfis_isi** (FWI components): Initial Spread Index. Why: Combines fine-fuel dryness and wind; tests associated conditions, not a spread target. Missing: 52.48%.
- **cwfis_bui** (FWI components): Buildup Index. Why: Combines DMC/DC to represent fuel available for combustion. Missing: 52.48%.
- **cwfis_fwi** (FWI components): Fire Weather Index. Why: Combines ISI/BUI to rate potential fire intensity; not an ignition probability. Missing: 52.48%.
- **cwfis_dsr** (FWI components): Daily Severity Rating. Why: A transformation of FWI related to suppression difficulty; likely redundant with FWI. Missing: 52.48%.
- **fire_history_same_cell_30d** (Past ignition history): Earlier same-cell ignition records in 30d. Why: Tests recent recorded activity; all dates precede the issue day. Missing: 0.00%.
- **fire_history_neighborhood_30d** (Past ignition history): Earlier ignition records in the 3x3-cell neighbourhood in 30d. Why: Adds nearby recent context; neighbourhood size is about 30x30 km away from boundary clipping. Missing: 0.00%.
- **fire_history_same_cell_365d** (Past ignition history): Earlier same-cell records in 365d. Why: Tests longer-term recurrence in the available archive; early-2010 history is left-censored. Missing: 0.00%.
- **fire_history_neighborhood_365d** (Past ignition history): Earlier neighbourhood records in 365d. Why: Adds broader annual recurrence context without using future records. Missing: 0.00%.
- **fire_history_days_since_same_cell** (Past ignition history): Days since an earlier observed same-cell ignition. Why: Captures recency; blank means no earlier observed event in the available 2010+ history, not zero days. Missing: 77.11%.
- **terrain_sample_count** (Coverage / quality): Number of valid terrain samples (0-9). Why: Makes terrain reliability visible. Missing: 0.00%.
- **terrain_coverage_fraction** (Coverage / quality): Terrain sample count / 9. Why: Expresses terrain coverage; exactly redundant with its count. Missing: 0.00%.
- **cwfis_station_count** (Coverage / quality): Usable selected weather stations (0-4). Why: Identifies strength of available weather support. Missing: 0.00%.
- **cwfis_nearest_station_km** (Coverage / quality): Distance to nearest usable selected station (km). Why: Shows how far the weather estimate is extrapolated from a station. Missing: 52.48%.
- **cwfis_observation_coverage_fraction** (Coverage / quality): Usable station count / 4. Why: Expresses station coverage; exactly redundant with station count. Missing: 0.00%.
- **cwfis_imputed_station_fraction** (Coverage / quality): Fraction of selected source records marked interpolated/imputed. Why: Preserves source processing; all observed v1 values are zero, so test redundancy with the missingness flag. Missing: 52.48%.
- **cwfis_calcstatus_nonzero_fraction** (Coverage / quality): Fraction with a nonzero source calculation-status flag. Why: Preserves calculation states; all observed v1 values are zero. Nonzero is not automatically invalid in other sources. Missing: 52.48%.
- **cwfis_missing** (Coverage / quality): No usable selected prior-day weather station (0/1). Why: Distinguishes unavailable weather from real physical zeroes. Missing: 0.00%.
- **cwfis_precip_7d_coverage_days** (Coverage / quality): Usable days in seven-day rain window (0-7). Why: Explains why rain totals are missing and distinguishes partial coverage. Missing: 0.00%.

FWI definitions: [Natural Resources Canada](https://natural-resources.canada.ca/forest-forestry/wildland-fires/canada-fire-weather-index-system).

## HRDPS and spread

HRDPS, ERA5-Land, fuel type, vegetation density, lightning, roads and settlement are not present in this v1 matrix. The PDF describes proposed future work. For HRDPS, match variables, units, grid, forecast lead times and issue timing explicitly; a same-day hour 0-6 live forecast cannot silently replace prior-day observations. CWFIS-derived FWI and seven-day rain also require a documented calculation/initialisation contract. Wind direction and a dated fuel layer are required separately for spread work. Existing fires should feed a separate incident-conditioned spread simulator.

## Evaluation

Use whole dates/seasons in chronological development and final holdouts, plus a separate spatial-block experiment. Fit transformations and feature selection only on each training side. Preserve natural prevalence in calibration/test data; tune weighting only in training/validation. Report average precision, precision/recall and alert burden, reliability plots, Brier score/log loss and performance by weather coverage. Calibration data must be later and separate from model fitting. Default random early-stopping validation must be replaced by a chronological strategy.

## Validation scope

No violations were found in the listed checks.

The listed full-file checks cover schema parsing, keys/universe, labels/count consistency, timing strings, selected bounds and missingness relationships. They do not independently reconstruct NFDB labels, station-selection history, past-fire features, precipitation sums or source publication availability. The documented station-candidate selection issue and event-dependent edge-cell inclusion remain sensitivity questions; EDA does not resolve them.

Every row scanned. Counts, missingness, means, ranges, cell/date summaries and listed checks are exact; quantiles and Pearson correlations use a uniform priority sample. All positive cell-days retained for distribution plots. No rows altered, imputation, training or source-record revalidation.
