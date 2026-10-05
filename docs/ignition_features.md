# Ontario wildfire ignition feature layer (v1)

This is an ignition-only, retrospective feature build for the existing Ontario
Fire Region 10 km cell/day label panel. It does not create an incident, fire
front, perimeter, or arrival product.

## Prediction contract

`(grid_id, ignition_date)` remains the sole panel key. `ignition_date` is a
local `America/Toronto` calendar date and the feature issue time is 00:00 local
time. The target is the existing same-local-day label; all observed dynamic
inputs therefore end before that local day starts.

The output preserves every original label column and row. In the full build it
contains 9,673 cells × 1,840 fire-season dates = 17,798,320 rows. A date-range
option exists only for reproducibility tests; it is not a different row
universe.

This is a hindcast/explanatory dataset, not an operational forecast. In
particular, the CWFIS archive and NFDB archive do not retain a per-record
historical `available_at` timestamp. The output and audit report retain that
limitation instead of treating a modern archive download as information that
was necessarily available in 2010.

## Included features

| Family | Output fields | Time rule and QA |
| --- | --- | --- |
| Calendar / solar | `calendar_day_of_year`, `calendar_doy_sin`, `calendar_doy_cos`, `calendar_is_weekend`, `calendar_day_length_hours` | Computed from the issue date and grid latitude. `issue_time_utc` records the daylight-saving-aware issue timestamp. |
| CDEM terrain | `terrain_elevation_mean_m`, `terrain_slope_deg`, `terrain_aspect_sin`, `terrain_aspect_cos`, `terrain_ruggedness_m`, `terrain_sample_count`, `terrain_coverage_fraction` | Nine equal-area sample locations are generated inside each existing EPSG:3978 10 km cell and queried from legacy CDEM. This is static ground terrain only. Coverage and missing slope are retained rather than imputed. |
| CWFIS weather/FWI | `cwfis_temp_c`, `cwfis_rh_pct`, `cwfis_wind_speed_kmh`, `cwfis_precip_mm`, `cwfis_ffmc`, `cwfis_dmc`, `cwfis_dc`, `cwfis_isi`, `cwfis_bui`, `cwfis_fwi`, `cwfis_dsr`, `cwfis_precip_7d_mm` | The only dynamic source date used for issue date *t* is *t − 1*. Four nearest archived stations are inverse-distance weighted only when their FWI is present and the station had already appeared in the archive. `cwfis_station_count`, `cwfis_nearest_station_km`, `cwfis_observation_coverage_fraction`, `cwfis_imputed_station_fraction`, `cwfis_calcstatus_nonzero_fraction`, `cwfis_missing`, and `cwfis_precip_7d_coverage_days` retain quality/coverage. |
| Past-only ignition history | `fire_history_same_cell_30d`, `fire_history_neighborhood_30d`, `fire_history_same_cell_365d`, `fire_history_neighborhood_365d`, `fire_history_days_since_same_cell` | Counts are updated only with records whose `ignition_date < issue_date`. A neighbourhood is the existing 3 × 3 grid topology. The committed event subset starts in 2010, so early-period history is intentionally incomplete rather than fabricated. |

`feature_set_version`, `terrain_source_id`, `terrain_source_vintage_end_year`,
`cwfis_source_id`, `cwfis_observation_date`, and
`fire_history_source_id` provide row-level source/version linkage. The source
registry and generated audit add source URLs, checksums/retrieval metadata,
coverage, historical semantics, and availability limitations.

## Source choices and time semantics

The machine-readable decisions are in
[`data/metadata/ignition_feature_source_registry_v1.json`](../data/metadata/ignition_feature_source_registry_v1.json).

- **CDEM terrain** — NRCan's legacy CDEM has 1945–2011 coverage and is used only
  for low-change ground-terrain summaries. The extractor uses the documented
  CDEM profile API; it does not use a newer lidar/DSM/land-cover product as a
  substitute. The CDEM release is a static terrain reference, not evidence of
  2010 vegetation, roads, fuels, or disturbance.
- **CWFIS FWI archive** — the downloaded `cwfis_fwi2010sv3.0_ll.csv.bz2`
  archive covers 2010-01-01 through 2019-12-31 and includes noon weather plus
  FFMC, DMC, DC, ISI, BUI, FWI, and DSR. The download script pins the observed
  SHA-256. The public archive's readme describes the 2010–2019 system history,
  but does not provide a historical release time for each record. It is therefore
  labelled `retrospective_hindcast`, never an operational replay. The archive
  contains an overlapping 2015 extract that is not globally date ordered; the
  reader consumes only required prior-day dates and does not scan an unrelated
  trailing duplicate segment after the final requested date.
- **NFDB history** — current NFDB-derived labels supply past records only. The
  builder never reads the current day's or a later record into a history feature.
  NFDB date precision and historical reporting availability remain limitations,
  and no final perimeter or final size is used as an explanatory feature.

The CWFIS `opts` and `calcstatus` values are not discarded. The build preserves
an interpolation/missing-condition fraction and non-zero status fraction, so a
model can distinguish a source-derived value from a well-observed value.

## Explicitly deferred, not silently substituted

| Planned family | Decision in v1 | Reason |
| --- | --- | --- |
| ERA5-Land weather / soil state | Deferred enrichment | ERA5-Land has historical coverage but is reanalysis. It needs a documented cell aggregation and remains a hindcast unless replaced by archived forecasts issued before each target day. CWFIS provides the implemented historical weather/FWI baseline. |
| MODIS land cover / fuel | Deferred | A current/reprocessed annual land-cover image is not used for an earlier issue date. A retained historical product version and its `available_at` date are required before annual class fractions can be joined. The 2024 FBP fuel map is expressly out of scope. |
| Roads / access | Deferred | A 2010 historic ORN edition exists, but no redistributable licensed geometry extract was supplied. Current roads are not backfilled because they can encode later access. |
| Settlement / population | Deferred | A dated Census geography/value extract and availability ledger are needed for every vintage. Current settlement or population data are not joined backwards. |
| Lightning, satellite phenology, RDPA | Deferred | The required coverage/issuance/QA ledger is not yet bundled. Missing-era sensors are not coded as zero. |
| Fire spread | Not built | No incident-conditioned candidate cells or dated perimeter snapshots were supplied. Final perimeters, final size, burn products, and future detections are prohibited ignition predictors. |

## Reproducibility

Regenerate the label panel from its dated sources (the panel itself is not
committed):

```sh
python scripts/download_nfdb.py
python scripts/prepare_ontario_ignitions.py
python scripts/download_ontario_fire_region.py
python scripts/build_cpa_daily_ignition_labels.py
```

Build static terrain once (the committed summary is the output of this command):

```sh
python scripts/build_cdem_terrain_summary.py
```

Download the immutable CWFIS source archive. The archive itself is ignored by
git; the script records its checksum and refuses an unreviewed upstream change.

```sh
python scripts/download_cwfis_fwi.py
```

Build the full feature panel and its audit:

```sh
python scripts/build_ignition_feature_panel.py
```

The feature panel is intentionally ignored because it is a large deterministic
build artifact. Its audit JSON records source paths, date coverage, output and
input row counts, source-station coverage, terrain missingness, key/spatial
alignment checks, and leakage checks. To make a small reproducibility subset,
use `--start-date YYYY-MM-DD --end-date YYYY-MM-DD` with a distinct output and
audit path.

Run the executable contract test with:

```sh
python -m unittest discover -s tests -v
```

## Validation boundaries

Before writing an output, the builder checks the complete input panel's exact
schema, dates, per-day cell universe, row count, label values, ordering, and
key uniqueness. It requires a one-to-one terrain/grid alignment. During the
join it asserts output row count and ordered unique keys; it verifies CWFIS
observation date is strictly earlier than the issue date and inserts history
records only after their date has completed. It reports missing CWFIS and CDEM
coverage rather than turning missing values into zeros.

No scaling, imputation, target encoding, climatology, spatial smoothing, or
feature selection is fitted here. Those transformations must be fit within the
training side of each temporal/spatial split.
