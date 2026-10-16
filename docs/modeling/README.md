# Retrospective ignition model comparison

Status: training and independent verification completed. Read
[the final report](final_report.md), open [the portable report](report.html), or
inspect [verification evidence](verification_report.md). Exact execution state
is recorded in `run_status.json`.

The target is at least one recorded ignition in a frozen v1 10 km cell during
the local date, with issue cutoff 00:00 America/Toronto and a one-day horizon.
Use exactly the 36 fields in `data/metadata/ignition_feature_selection_v1.json`.
Keep keys and dates outside the model matrix. The frozen study universe includes
edge cells selected using study-period ignition events, so evaluation is
conditional on this universe and does not establish unbiased province-wide or
operational forecasting performance.

The downloaded v1 panel has a source-selection defect: all-time nearest stations
are selected before filtering by first observation date. It also joins an archive
whose date segments are not globally sorted. Modelling uses a separate disk cache
with globally indexed date/station records, nearest eligible stations selected
before top-k, and reconstructed prior-seven-day precipitation. All original rows,
labels, calendar, terrain and history values are retained. The original CSV and
EDA remain unchanged. The corrected cache has its own version and source audit.
First valid FWI wins duplicated station/day records; archive revision and
publication times remain unknown. This is a retrospective hindcast.

Development uses five expanding chronological folds ending in validation years
2012 through 2016. Calibration is held out in 2017; threshold tradeoffs use 2018;
the final test is 2019. No labels overlap future dates, so no purge gap is needed.
All fitted preprocessing stays within each training partition. No resampling is
applied; validation/test retain every row and their natural prevalence.

Numeric caches and model binaries are ignored generated artifacts. Compact
manifests, reports, reproduction commands and verification evidence are tracked.
Only the coordinator edits shared configuration, orchestration and this guide.

## Reproduction

Use Python 3.12 and install `requirements-modeling.txt` in an isolated environment.
The original audited CSV is supplied through `--input`; it is not stored in Git.
Recover the exact CWFIS archive URL and checksum from
`data/raw/cwfis/manifest.json` before cache preparation. From the repository root:

```powershell
python -m scripts.ignition_modeling.validation --input '<original audited CSV>' --chunk-size 50000
python -m scripts.ignition_modeling.data --input '<original audited CSV>'
python -m unittest discover -s tests -v
python -m scripts.ignition_modeling.run --smoke --output artifacts/ignition_modeling/smoke
python -m scripts.ignition_modeling.run
python scripts/publish_ignition_modeling_report.py
```

The training runner checks the audited data contract and immutable configuration,
and refuses to overwrite a completed final test. A fresh reproduction uses a new
`--output` directory; preserve the recorded test results and document any
correctness-defect rerun. Report packaging does not train or alter selection.
The publication wrapper is outside the frozen training-module hash set, so the
executed training code and its original fingerprints remain unchanged.

Models and all validation/test prediction vectors are saved locally under
`artifacts/ignition_modeling/full_run/`. `selected_system.joblib` is the final
probability-producing bundle; `final_logistic.joblib` and `final_hgb.joblib`
retain both fitted model-family pipelines. Use `predict_bundle` from
`scripts.ignition_modeling.run` with the exact 36-column schema and corrected
past-only inputs. No operating threshold has been selected.
