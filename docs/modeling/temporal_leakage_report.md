# Temporal leakage and source availability audit

Audit date: 2026-10-09, with final corrected-cache evidence reconciled after execution. Scope: the 36 predictors explicitly listed in `data/metadata/ignition_feature_selection_v1.json`, the frozen v1 ignition panel, and the source builder. This audit role did not fit a model.

## Gate

**PASS for the executed corrected cache `ignition-v1-weather-asof-global-date-v2` under the retrospective frozen-grid scope.** All 17,798,320 rows and the exact 36 named source predictors were retained; all 19 CWFIS predictors were replaced using globally indexed source dates and station eligibility before nearest-four selection. Independent correction tests, the full corrected-cache audit, and final production artifact verification have passed. The authoritative decision is [training_gate.json](training_gate.json); executed evidence is in [verification_report.md](verification_report.md) and [corrected_weather_audit.json](corrected_weather_audit.json).

**BLOCK remains for fitting the original, uncorrected 36-column v1 matrix.** Its weather station selection depends on stations introduced after the observation date, and the source join assumes global date order that the archive does not have. Its `t-1` row dates do not remove those defects. The original CSV and EDA describe that historical version; production used the separately versioned corrected numerical cache at `data/interim/ignition_modeling_cache`, identified by its [manifest](../../data/interim/ignition_modeling_cache/manifest.json).

**BLOCK for a historically operational availability claim.** CWFIS and NFDB lack per-record historical `available_at` timestamps. CDEM's recorded 2013 release also postdates the earliest target years. Date-based hindcast conditioning cannot prove what was published at a historical issue time.

## Target and cutoff

For `(grid_id, ignition_date=t)`, the primary target is whether at least one selected NFDB recorded incident occurs on local date `t`. Issue time is `00:00 America/Toronto`; the target horizon is `[00:00 t, 00:00 t+1)` in that timezone. `issue_time_utc` is generated using timezone-aware conversion at `scripts/build_ignition_feature_panel.py:485–500`.

NFDB `YEAR`, `MONTH`, and `DAY` become the recorded calendar date at `scripts/prepare_ontario_ignitions.py:72–73`; this provides no intraday ignition/discovery/report timestamp. The label contract uses that date as a local date rather than converting an observed UTC timestamp.

The 36-column allowlist contains 36 unique names. `ignition` is the target. `ignition_count` and `large_fire_200ha` are excluded labels. `grid_id`, `ignition_date`, `issue_time_utc`, observation/source identifiers, and source-vintage metadata are audit/join/split fields rather than predictors. Perimeters, final size, current fuels, satellite detections, and other spread fields are absent from the allowlist.

## Predictor ledger

The table records the **original matrix** audit, preserving the defect history. `PASS` means its time dependency passed that retrospective audit; `CONDITIONAL` identifies static-source or archive-availability limitations; `BLOCK` identifies the original CWFIS fields that required replacement. **All 36 fields in the executed corrected cache now have a retrospective fit gate of PASS**, subject to the publication, static-terrain and frozen-grid scope limitations described below.

| Predictor | Permitted information and purpose | Original matrix gate |
| --- | --- | --- |
| `calendar_day_of_year` | Known target-date day number; seasonality. | PASS |
| `calendar_doy_sin` | Deterministic target-date cyclic encoding. | PASS |
| `calendar_doy_cos` | Deterministic target-date cyclic encoding. | PASS |
| `calendar_is_weekend` | Known target-date weekday. | PASS |
| `calendar_day_length_hours` | Target date and fixed cell latitude; astronomical day length. | PASS |
| `terrain_elevation_mean_m` | Fixed CDEM ground elevation, without event-conditioned change. | CONDITIONAL |
| `terrain_slope_deg` | Fixed CDEM sample-plane slope. | CONDITIONAL |
| `terrain_aspect_sin` | Fixed terrain aspect cyclic encoding. | CONDITIONAL |
| `terrain_aspect_cos` | Fixed terrain aspect cyclic encoding. | CONDITIONAL |
| `terrain_ruggedness_m` | Fixed elevation variability. | CONDITIONAL |
| `terrain_sample_count` | Coverage of the fixed nine-sample terrain query. | CONDITIONAL |
| `terrain_coverage_fraction` | Fixed sample count divided by nine. | CONDITIONAL |
| `cwfis_station_count` | Number of usable stations selected from an as-of-date station catalogue. | BLOCK |
| `cwfis_nearest_station_km` | Distance to the nearest usable as-of-date selected station. | BLOCK |
| `cwfis_observation_coverage_fraction` | As-of-date usable/candidate station fraction. | BLOCK |
| `cwfis_imputed_station_fraction` | Interpolation flags on selected `t-1` records. | BLOCK |
| `cwfis_calcstatus_nonzero_fraction` | Calculation-status flags on selected `t-1` records. | BLOCK |
| `cwfis_missing` | No usable selected `t-1` station; not zero weather. | BLOCK |
| `cwfis_temp_c` | Temperature from only records dated `t-1`. | BLOCK |
| `cwfis_rh_pct` | Relative humidity from only records dated `t-1`. | BLOCK |
| `cwfis_wind_speed_kmh` | Wind speed from only records dated `t-1`. | BLOCK |
| `cwfis_precip_mm` | Precipitation from only records dated `t-1`. | BLOCK |
| `cwfis_ffmc` | Archived FFMC dated `t-1`, including its past-weather state. | BLOCK |
| `cwfis_dmc` | Archived DMC dated `t-1`. | BLOCK |
| `cwfis_dc` | Archived DC dated `t-1`. | BLOCK |
| `cwfis_isi` | Archived ISI dated `t-1`. | BLOCK |
| `cwfis_bui` | Archived BUI dated `t-1`. | BLOCK |
| `cwfis_fwi` | Archived FWI dated `t-1`. | BLOCK |
| `cwfis_dsr` | Archived DSR dated `t-1`. | BLOCK |
| `cwfis_precip_7d_mm` | Sum of exactly seven daily values dated `t-7` through `t-1`; blank if any unavailable. | BLOCK |
| `cwfis_precip_7d_coverage_days` | Number of available daily values in that same window, 0–7. | BLOCK |
| `fire_history_same_cell_30d` | Selected event count in the same cell with dates in `[t-30,t)`. | CONDITIONAL |
| `fire_history_neighborhood_30d` | Selected event count in the existing 3×3 topology, including the cell, in `[t-30,t)`. | CONDITIONAL |
| `fire_history_same_cell_365d` | Selected event count in the same cell in `[t-365,t)`. | CONDITIONAL |
| `fire_history_neighborhood_365d` | Selected event count in the 3×3 topology in `[t-365,t)`. | CONDITIONAL |
| `fire_history_days_since_same_cell` | Days since the latest selected same-cell event strictly before `t`; censored blank if none. | CONDITIONAL |

Terrain conditionality: the registry records CDEM vintage ending 2011, release 2013-04-01, and modern extraction. The allowed interpretation is static ground terrain for retrospective analysis. Neither pre-2013 publication availability nor absence of all later terrain changes was independently established; no vegetation/fuel/access inference is permitted.

History conditionality: the source is a modern NFDB archive without historical report availability, and selected events begin 2010-05-01 and include May–October only. Windows count the selected incident subset, not all fires in all months or pre-2010 history. Code explicitly excludes out-of-season records at `scripts/build_cpa_daily_ignition_labels.py:170–172`. These are incomplete/censored historical summaries, not complete lifetime histories.

## Concrete findings

### Future station existence affects prior features

`load_station_catalog` scans the whole 2010–2019 archive at `scripts/build_ignition_feature_panel.py:273–298`. `nearest_station_choices` picks the nearest four from that all-time catalogue at lines 301–318. Only afterward does `make_cwfis_features` filter `first_seen <= observation_date` at lines 395–398. The build fixes those choices once at lines 577–579.

A station first introduced in 2019 can therefore occupy a candidate slot for 2010, displacing a station that actually existed in 2010. Filtering the future station afterward does not restore the displaced station. This can change missingness, station counts/distances, quality fractions and every derived weather value, including seven-day precipitation. This is a future-source-topology dependency even when no future temperature or label is directly read.

An in-memory counterexample using the actual builder functions and `count=1` confirms the defect: an existing 2010 station 7.440403 km from the cell supplies FWI and weather. Adding a nearer station first seen in 2019 changes the 2010-04-30 feature from count 1/missing 0/temperature 1 to count 0/missing 1/blank temperature. The same displacement applies to the fourth candidate slot under the production count of four. The corrected implementation passed an independent production-count fixture: adding four nearer future stations leaves earlier numerical weather and coverage unchanged. See [WeatherAcceptanceTest](../../tests/test_ignition_modeling.py), including `test_inserting_nearer_future_stations_cannot_change_past_weather`.

The executed combined station-selection and source-join corrections changed station counts on 14,024,726 cell-days and weather missingness on 8,064,924. These figures quantify both corrections together; they do not isolate each defect's individual contribution.

### Archive chunk order needs independent source resolution

`read_cwfis_days` treats adjacent date runs as a daily block at `scripts/build_ignition_feature_panel.py:320–351`. `CwfisDayLookup` advances through these blocks using a monotonic requested date at lines 354–375. Its own comment documents a non-globally-sorted 2015 overlap. Date-only timing checks can pass while an earlier/later fragment for the same date is omitted or the wrong fragment is used.

A bounded initial read of the first 100,000 raw records found dates 2010-01-01 through 2010-04-24, no backwards date transition and no duplicate date/station rows in that prefix. 91,610 rows had missing FWI; that prefix alone could not establish full-archive ordering. The subsequently executed full index scanned 4,159,936 source records and logged **2,183 backwards date transitions**, including 2015-08-01 to 2015-03-08. It retained 1,038,321 relevant date/station records across 817 stations, with zero duplicate/conflicting pairs in the retained region. Global date/key grouping and the declared first-valid-FWI duplicate policy also passed deliberately unsorted and conflicting-duplicate fixtures. The original 2015–2016 EDA gaps therefore cannot be interpreted entirely as upstream observation unavailability.

### Fire-history blanks are censored states, with checked join evidence

`load_events` rejects grid IDs absent from the grid at lines 421–436. `FireHistory.advance` inserts only `event_date < issue_date`, then removes dates strictly before each window cutoff at lines 462–473. The issue date itself never enters a history feature. `values` leaves days-since blank only if `last_same_cell` has no prior event at lines 475–482.

An independent reconstruction used date-sorted event lists and binary searches, rather than the builder's `FireHistory` implementation. On 107,785 rows drawn from the 100,000-row uniform sample and all 7,785 positive cell-days, all five history fields matched the 8,109 committed source events. There were 107,750 distinct keys because 35 positives also occurred in the uniform sample. All ten years were represented. The 80,433 checked blank days-since entries matched absence of an earlier selected same-cell source event; none was a demonstrated broken join. This evidence does not prove completeness of the NFDB source outside the selected subset or independently check every full-panel history row.

Past incidents earlier within a validation/test period may enter later days' history under this rolling hindcast contract. No fitted statistics or future incidents may enter. Actual operational availability of those earlier incident reports remains unverified.

### Frozen spatial universe is selected partly using target-period events

`scripts/build_cpa_daily_ignition_labels.py:188–199` includes boundary-centroid cells plus any edge cell containing a selected 2010–2019 ignition; lines 212–213 pass all study-period event grid IDs to that rule. This is target-dependent spatial selection. It is not a predictor column, but can bias the evaluation population, particularly for edge cells selected by held-out events.

The permitted scope is evaluation conditional on the explicitly frozen 9,673-cell v1 universe. Results cannot establish performance on an independently selected Ontario grid, new cells, or operational deployment. A boundary-only, target-independent grid requires a separately versioned dataset and rerun. The raw boundary geometry was absent at the initial audit, so the number of event-added edge cells was not independently measured here.

## Checks performed

- Exact allowlist: 36 unique predictors; excluded labels/metadata do not overlap it.
- Source reconstruction: 107,785 checked rows, zero mismatches in each of the five history fields, five calendar predictors, seven terrain fields, ignition indicator and ignition count. Calendar reconstruction reused the deterministic calendar function; history reconstruction did not reuse its rolling implementation. Terrain checks tested alignment to the committed static summary, not the upstream elevation service.
- NFDB selected-event file SHA-256: `dff9a8cdb0b7e7b96f6210b90642ff4eff4c3be51754261ac07df003f1c4a518`.
- Bounded full-panel prefix: 100,000 rows through 2010-05-11; zero issue-UTC or exact `t-1` observation-date mismatches. Seven-day precipitation sum/null and coverage matched on 41,962 cell-day windows, including 13,429 with all seven values. Sum tolerance was 0.000008 mm to allow six-decimal daily output rounding. This checks panel-internal arithmetic, not source-record completeness.
- Existing repository tests: `python -B -m unittest discover -s tests -v`; three passed. They verify basic past-only insertion, terrain summary and stopping before an unneeded unsorted tail. They do not test all-time station candidate selection or globally repeated observation dates.
- Manifest-pinned CWFIS source: recorded and subsequently downloaded SHA-256 `67e706160316e2b1e496bc3b316d17d5c1623954f29231d5cb71e618f0329246`. The newly downloaded bytes were checksum-verified, and a bounded raw prefix was independently inspected.

## Executed correction acceptance

The executed modelling-only replacement is [weather.py](../../scripts/ignition_modeling/weather.py). `index_archive` globally keys records by date/station, `AsOfStations.advance` admits stations only when their first-seen date is no later than the requested observation date before retaining the nearest four, and `daily_weather` requires that exact date's valid FWI. `corrected_weather_days` uses only `t-7` through `t-1` and requires seven finite precipitation values. The coverage denominator is the number of eligible candidate slots, `min(4, eligible_station_count)`; with zero candidates coverage is zero and weather is missing. Duplicate or decreasing issue dates are rejected before opening the weather database, preventing an as-of catalogue from being reused backward in time.

The original release requirements below have been satisfied by the saved execution and independent verification. They remain requirements for any future rebuild:

1. Check the pinned source checksum and log the actual corrected artifact/version. Preserve the target, keys, row universe and explicit 36-column input names.
2. For each observation date `s`, select nearest four stations from stations with `first_seen <= s` **before** top-k selection; handle zero eligible stations as missing. The source-reader station set must include every station that can be eligible, not only the original all-time nearest-four union. State the coverage denominator.
3. Resolve all same-date/key archive fragments globally with a declared deterministic duplicate policy. Record source rows, backwards date transitions and examples, duplicate/conflicting pairs and their resolution. A retrospective first-valid-FWI policy does not establish historical release availability.
4. Recompute all 19 CWFIS predictors, including prior-seven-day rain from each day's own as-of catalogue. Do not overwrite missing weather with zero or delete rows for missingness.
5. Test that adding future station records cannot change earlier values, quality or missingness; test exact `t-1` use, same/future-day exclusion, seven-day boundaries, incomplete-window nulls, unsorted/repeated date blocks and empty eligible catalogues.
6. Record the corrected cache audit and quantify differences from original v1, including formerly missing rows that become available and changed station selections. Verify source-to-cache alignment on real-data cases and that all keys/labels remain unchanged.
7. Before fitting, enforce the allowlist and chronological split. Fit imputers/scalers/feature selection/class-weight choices inside training folds only. Treat blank days-since as an explicit censored state; imputation must preserve that indicator. Preserve natural class prevalence in calibration, threshold selection and test partitions.
8. Retain the declared retrospective/frozen-grid limitations. No `available_at` timestamp is inferred from an observation date, download time or source manifest.

The independent [verification report](verification_report.md) records **29/29 passing tests** and a full numeric-cache scan of **17,798,320 rows**, with zero reported range, quality/null, grid or label violations. Exact feature order, cache version, all 1,840 date slices, each date's 9,673 grid IDs and all 7,785 positives were preserved. Seven-day precipitation sums and coverage were independently reconstructed for all cells on 1,780 dates; the first six May dates of each year require April source dates outside the panel and are covered by strict-past source fixtures. Twelve real cell/date station joins across 2010, 2015, 2016 and 2019 independently match all 11 corrected daily weather/FWI fields within 1e-6.

Corrected daily weather is missing on **1,275,992 rows**, and corrected seven-day precipitation on **2,171,969 rows**. These counts supersede the original EDA's missingness for modelling input, while that EDA remains a record of the original CSV. Final production acceptance independently verifies saved configuration, preprocessing/schema, freeze ordering and test artifacts; this documentation reconciliation performs no refit or new scan.

The associated [machine-readable rules](availability_rules.json) now record **original matrix BLOCK / corrected cache PASS / operational replay BLOCK**. No unresolved predictor-date leakage remains under the documented retrospective frozen-grid scope. Historical source publication/revision availability, static-terrain approximation and target-dependent edge-cell selection remain limitations.
