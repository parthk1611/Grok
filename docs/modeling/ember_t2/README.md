# EMBER T2.1 / T2.2: reproduction and top-k alert metrics

Branch `models/ember-repro-topk-v1` (from `contracts/v0`). Status as of 2026-11-02
(America/Toronto): **T2.1 BLOCKED, T2.2 code and tests done but NOT RUN on real
data.** This file contains no model results; every metric below is a placeholder
that the commands will generate. Nothing was fabricated.

## T2.1 status: blocked at the boundary download

Environment: Python 3.12.15 venv from `requirements-modeling.txt` (exact pins) plus
unpinned `shapely` 2.2.0 and `pyproj` 3.8.0 (needed by the label builder per
`AGENTS.md`; not in any requirements file).

| Step | Result |
| --- | --- |
| `python scripts/download_nfdb.py` | OK. Archive sha256 `9eb4773d...8be3` equals `data/raw/nfdb/manifest.json`. |
| `python scripts/prepare_ontario_ignitions.py` | OK. Output byte-identical to the tracked `data/interim/ontario_nfdb_ignitions_2010_2019.csv` (git showed only an audit timestamp change, reverted). |
| `python scripts/download_ontario_fire_region.py` | **FAILS.** `StopIteration` at `dbf_name = next(name for name in archive.namelist() if name.lower().endswith(".dbf"))` (line 36). |

Cause: the upstream `FIRERESP.zip` (https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Packages/FIRERESP.zip)
is now an Esri File Geodatabase (`Non_Sensitive.gdb/`, member dates 2026-10-05),
not a shapefile. Its sha256 is `b0a551e3...934c`, not the pinned
`1368d14e...a4a9` in `data/raw/ontario_fire_region/fire_response_plan_sectors_manifest.json`.
The pinned archive is not reachable from this URL any more, and the repo's pure
Python parser (`download_cpa_boundary.parse_dbf/parse_polygons`) cannot read
a geodatabase.

Per instructions I did not substitute another boundary, parser or source. The
downstream steps (`build_cpa_daily_ignition_labels.py`,
`download_cwfis_fwi.py`, `build_ignition_feature_panel.py`, `ignition_modeling.validation`,
`ignition_modeling.data`, `ignition_modeling.run`) were therefore not run, so no
cache, no fitted model and no 2018/2019 prediction vectors exist on the box (searched
the whole filesystem for the panel, cache and `*.joblib`; none found).

Unblock options (Team Lead / ATLAS decision, not mine):

1. Someone with the original `fire_response_plan_sectors.geojson` (sha256
   `37efcdb2...2263`, 15 features) supplies it; the tracked grid and filtered events
   suggest it is only needed to re-run the label builder.
2. ATLAS reads the new GDB (needs GDAL/pyogrio, not installed) and proves the 15
   `RES_SECTOR != 'OFR'` features reproduce the tracked grid
   (`data/interim/ontario_fire_region_10km_grid.csv`, sha256 `b22741d8...3f35`) and the
   label audit; the resulting boundary asset must be documented as a new source revision.
3. Parth/anyone who ran the 2026-10-05 build copies the 747 MB panel and its audit
   (the build checks the panel sha256 recorded in `docs/modeling/data_quality_report.json`).

Tests that could run: `python -m unittest discover -s tests` gives 50 tests, all OK
(36 pre-existing incl. 7 contract tests that need `jsonschema`, installed
unpinned in the local venv, plus 14 new top-k tests).

## T2.2: what was built

`scripts/ignition_modeling/topk.py` (no training or tuning code; imports only
evaluation helpers, and `run.predict_*` inside the CLI) and `tests/test_ignition_topk.py`.

Definitions, all fixed in advance:

* per local day over all cells, `k = ceil(fraction * n_cells)`; k = 97 / 484 / 968 for
  1% / 5% / 10% of 9,673 cells, reported in every table;
* ties at the cutoff resolved by expected value (deterministic; constant score gives
  exactly chance recall and lift 1);
* pooled recall = sum TP / sum positives; mean-of-days recall excludes days with zero
  positives (their count is printed); mean-of-days precision uses all days;
* lift = precision / prevalence of the evaluated split;
* baselines: climatology = per-cell mean label over 2010-2016 only; rate = one constant
  (2010-2016 mean label, as `run.py` does);
* 95% percentile bootstrap by date: 1000 resamples, `numpy.random.default_rng(385)`,
  same resamples for every system; recall@k (pooled and mean-of-days), lift and AP.
  `--block-days 1` (default) resamples single dates; larger values draw circular
  blocks of consecutive dates (sensitivity to serial correlation);
* alert options use only the 2018 split: top-1/5/10% of cells per day, and absolute
  cutoffs at the 2018 pooled (1 - fraction) quantiles, applied unchanged to 2019 as a
  read-only check. Nothing is chosen on 2019.

Validation done (synthetic data only): hand-computed expected values, tie handling,
zero-positive days, k rounding, row-order invariance, weighted AP equal to sklearn
`average_precision_score` on repeated rows (with and without ties and with undrawn
top-scoring dates), bootstrap summaries equal to a direct resample, fixed seeds,
cutoffs depend only on the fit split, agreement with `evaluation.threshold_tradeoffs`.
A synthetic 184-day x 9,673-cell timing check gave about 4 s per 50 bootstrap
resamples per system (about 80 s per 1000), which is a speed check, not a result.

The CLI has NOT been executed end to end because there is no cache or model; its
`main()` is thin glue around the tested functions and may need small fixes on first
real run.

## How to run once the frozen model exists

```sh
# T2.1: new output dir, never the committed reports
python -m scripts.ignition_modeling.run --output /workspace/ember_scratch/full_run
# T2.2: scores the already-fitted bundle, never refits
python -m scripts.ignition_modeling.topk \
  --model /workspace/ember_scratch/full_run/selected_system.joblib \
  --output /workspace/ember_scratch/topk
```

It writes `topk_summary.json`, `topk_tables.md` and `topk_<split>.csv`; paste the
generated tables into this folder only after reviewing them.
