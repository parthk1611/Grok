# Project context

COMP-385 is building data products for Ontario wildfire modelling. Keep the two
prediction problems distinct: the ignition product predicts whether an existing
10 km grid cell has an ignition on a calendar day; a future spread product will
predict incident-conditioned fire-front/perimeter progression. Do not describe
the ignition panel as a spread model or use final perimeters, final size, burn
products, or future detections as ignition predictors.

## Ignition panel and feature layer

The established label universe is the Ontario Fire Region 10 km, fire-season
cell-day ignition panel for 2010--2019: 9,673 cells, 1,840 dates, and
17,798,320 rows. Its key is `(grid_id, ignition_date)`, with `ignition_date` a
local `America/Toronto` date. The validated v1 feature layer retains that exact
row universe and labels and provides 48 time-safe columns:

- calendar/solar variables computed at a 00:00 local issue time;
- static CDEM terrain summaries;
- CWFIS weather/FWI features from only the prior-day observation; and
- NFDB ignition-history features based only on records strictly before the
  issue date.

Preserve source quality rather than filling it away. The feature output carries
missingness, station coverage/distance, interpolation and `calcstatus` flags,
terrain coverage/sample counts, source/version linkage, and audit metadata.
Early history is incomplete by design because the committed event subset begins
in 2010; do not fabricate a pre-2010 history.

This is a retrospective hindcast/explanatory dataset, not an operational
forecast replay. Although CWFIS rows are restricted to `t - 1`, the historical
CWFIS and NFDB archives do not give per-record `available_at` timestamps. Do
not claim that a modern archive download was known at the historical issue
time. CDEM is allowed only as static ground terrain, not as a proxy for
vegetation, fuels, roads, settlement, or disturbance.

## Leakage rules

All dynamic inputs must have been observed before the local target day starts.
Keep history updates after a date has completed, use dated source vintages, and
retain unknown/missing source coverage as missing rather than zero. Any fitted
scaling, imputation, target encoding, climatology, smoothing, or feature
selection belongs inside the training side of each temporal/spatial split.

Do not silently backfill current maps into 2010--2019. Land cover/fuels,
roads/access, settlement/population, lightning, satellite phenology, and
ERA5-Land enrichment remain deferred until each has an appropriate historical
asset plus an availability/quality ledger. ERA5-Land remains a hindcast unless
replaced by forecasts archived before each issue time. Operational forecasting
also remains future work.

## Roadmap

First maintain and validate the ignition panel and its time contract, then add
the deferred feature families only with documented source timing and tests.
Build fire spread separately: it needs incident-conditioned candidate cells and
dated fire-front or perimeter progression. It does not yet exist; final
perimeters and final fire size cannot substitute for that target.

## Reproducibility and artifacts

When the ingestion/feature implementation is present, the principal commands
are:

```sh
python scripts/prepare_ontario_ignitions.py
python scripts/build_cpa_daily_ignition_labels.py
python scripts/build_cdem_terrain_summary.py
python scripts/download_cwfis_fwi.py
python scripts/build_ignition_feature_panel.py
python -m unittest discover -s tests -v
```

Treat large deterministic panels and downloaded source archives as generated
artifacts: do not commit them merely because a rebuild produced them. Keep
small manifests, source registries, audit JSON, and documentation when they
record provenance, checksums, coverage, missingness, alignment, or leakage
validation. Use explicit output/audit paths for any date-range test subset so
it cannot be mistaken for the full panel.
