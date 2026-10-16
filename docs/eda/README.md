# Ontario ignition EDA

The 2026-10-05 exploratory analysis scans all 17,798,320 v1 cell-days without
modifying labels, imputing values, deleting rows, or fitting models. It is a
retrospective ignition analysis; HRDPS ingestion and incident-conditioned spread
simulation remain separate work.

Open [the visual report](eda_report.html), [the text report](eda_report.md), or
[the notebook](ontario_ignition_eda.ipynb). The
[feature dictionary](feature_dictionary.csv) explains all 36 recommended source
predictors. Their machine-readable selection is
[`ignition_feature_selection_v1.json`](../../data/metadata/ignition_feature_selection_v1.json).

## Findings

- The panel has 7,785 positive cell-days (0.04374%), approximately 2,285
  negatives per positive. Accuracy alone is misleading.
- Daily weather/FWI is unavailable on 52.482010% of rows; seven-day rain is
  unavailable on 62.610842%. Weather-missing rows include 3,454 positive
  cell-days, so complete-case deletion would discard recorded ignitions.
- Only 16 of 1,779,832 rows have weather in 2015. Weather is missing on
  98.532446% of 2016 rows. The source and station-selection causes have not
  been independently reconstructed by this EDA.
- July has the highest pooled monthly ignition rate. Among weather-covered
  rows, positive days have higher mean temperature/FFMC/FWI and lower mean
  humidity. These are descriptive associations, not causal effects or evidence
  of held-out predictive performance.
- The blank days-since-fire feature (77.106109%) means no earlier observed
  same-cell ignition in the available 2010+ history. Preserve this censored
  state separately from missing weather.
- Coverage fractions rescale counts. Two source-quality fields are zero
  whenever observed. These and related FWI components warrant training-only
  feature-selection experiments; the shared recommended input contract is
  retained here.

## Measurement and validation scope

Counts, missingness, means, ranges, date/cell aggregates and the listed
consistency checks use every row. Quantiles and pairwise Pearson correlations
use a reproducible uniform priority sample of 100,000 rows (seed 385). Class
distribution plots use all positive rows and sampled negative rows, omitting
missing values within each feature. Feature statistics include observed class
denominators.

The scan checks the unique 48-column schema, exact expected cell/date key
universe, duplicate keys, label/count consistency, local-midnight/prior-day
timestamp strings, selected physical bounds, and coverage/missingness
relationships. No violations were found. This does not independently rederive
NFDB labels, station-selection history, lookback counts or rainfall sums, and
does not establish historical publication availability. Consult
[`eda_summary.json`](eda_summary.json) for the exact checks and runtime versions.

The provenance files record the Kaggle v1 download, its verified structural
checks and checksum, and the source builder's audit. The EDA does not recompute
the download checksum. Small aggregate tables, figures and reports are tracked;
the full panel and generated row samples are excluded from Git.

## Reproduce

Run from the repository root in an environment with the optional EDA packages:

```sh
python -m pip install -r requirements-eda.txt
python scripts/run_ignition_eda.py
python scripts/build_ignition_eda_report.py
```

The default input is the generated repository
`data/processed/ontario_fire_region_10km_daily_ignition_features_v1.csv.gz`.
It may be absent from a fresh checkout; the existing feature-build instructions
in `ABOUT.md` explain its generation. An already downloaded uncompressed CSV
can be used without a rebuild:

```sh
python scripts/run_ignition_eda.py --input /path/to/ontario_fire_region_10km_daily_ignition_features_v1.csv
python scripts/build_ignition_eda_report.py
```

Both commands default to `docs/eda`. Use `--output-dir` for the scan and
`--eda-dir` for rendering when keeping experimental artifacts elsewhere.
The scan also accepts `--grid` and `--feature-selection`. Rendering requires
the ignored uniform/positive samples; regenerate them with the scan first.
The notebook displays committed cached tables/figures and works without the
large CSV. Open it with a working directory inside the checkout.

## Historical timing and HRDPS boundary

Target days start at 00:00 America/Toronto. CWFIS values use the prior calendar
day, precipitation uses seven completed preceding dates, and recorded ignition
history uses strictly earlier dates. Dynamic archives lack historical
per-record `available_at` timestamps, so operational replay is not established.
Static terrain cannot substitute for fuel, vegetation, roads or settlement.

The current 36 source predictors are calendar/solar (5), static terrain (5),
weather (5), FWI components (7), earlier ignition history (5), and coverage/
quality (9). Labels, keys and source/version/time metadata stay outside the
shared predictor matrix. Logistic regression needs training-fitted imputation,
scaling and missing/censoring indicators; histogram boosting retains NaNs.

Jasdish's proposed HRDPS component supplies forecast temperature/humidity,
wind and precipitation for hours 0-6, then archives its runs. It is not present
in this panel. It must match Manav's agreed feature definitions, units, grid,
issue times and weather/FWI calculation contract; same-day forecasts cannot
silently replace these prior-day observations. Fuel types, vegetation,
lightning and dated fire-front progression are also absent. Spread needs its
own incident-conditioned inputs and observed progression targets.

FWI terminology follows [Natural Resources Canada's primary documentation](https://natural-resources.canada.ca/forest-forestry/wildland-fires/canada-fire-weather-index-system).
