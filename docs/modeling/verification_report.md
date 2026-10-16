# Independent verification

Status: **data/source gate and final production artifact acceptance PASS**.
The independent verifier fitted only 200-row synthetic model fixtures. Production
training and final test results require their own saved execution artifacts.

## Executed checks

`tests/test_ignition_modeling.py` contains 26 passing independent fixture checks.
The complete suite also passes three existing feature-panel tests: **29/29**.
Run from the repository root:

```powershell
.venv-modeling/Scripts/python.exe -m unittest discover -s tests -v
```

| Contract | Evidence |
| --- | --- |
| Feature schema | Independent literal list matches the exact ordered 36 inputs; labels, keys and issue/source dates are excluded; matrices with the wrong width fail. |
| Chronological separation | Five expanding folds validate 2012–2016; all training dates precede each validation date; 2017–2019 never enter those folds. Date assignments survive row permutation and duplicate dates stay together. |
| Dates | Missing dates, invalid dates, dates outside May–October 2010–2019 and intraday timestamps fail. |
| Numerical cache | Reload reproduces rows, labels and NaNs; changed feature order or cache version fails. Year slices preserve all cell rows. |
| Global weather index | A deliberately unsorted, cross-chunk archive retains all three independent date/station records. A missing-FWI duplicate is replaced by the first valid record; a later conflicting valid record does not replace it. Source checksum mismatch fails before index creation. |
| As-of station selection | Adding four nearer stations first observed eight years later leaves earlier numerical weather and coverage identical. |
| Weather quality | A record without valid FWI is unusable even when its temperature is extreme; counts, distance, coverage, interpolation/status flags and fully missing days have explicit expected values. |
| Seven-day rain | The window contains exactly the seven completed dates; target-day extreme rain is excluded. A missing prior-day rain value produces six coverage days and a missing sum. The window resets across seasonal/year gaps. |
| Weather iterator | Duplicate or decreasing issue dates fail before any database is opened. |
| Fire history | Current/future events remain unconsumed; exact 30-day window boundaries and neighboring-cell counts match independent expected values. No prior observed event remains censored. |
| Logistic preprocessing | Manually expected medians, imputed means/variances and scaled values match; the seasonal sine remains unchanged. Entirely missing training columns keep their position with documented zero fallback and unit scale, even when later observations exist. |
| Fold locality | Changing held-out values and missingness and transforming extreme test inputs leaves every fitted median, count, mean, variance and scale unchanged. |
| Indicators | Logistic output has 36 source inputs plus 21 fixed binary indicators; native-missing output has 36 raw inputs plus one explicit censored-history indicator. The native branch retains source NaNs and numerical values. |
| Transform persistence | Disk-backed/chunked transforms and a reloaded joblib preprocessor reproduce in-memory numerical output and feature names. Wrong DataFrame order, extra target column, strings, infinite values and empty training data fail. |
| Logistic model integration | A 200-row fixture records the actual weights passed to SGD: 180 negatives weighted 200/360 and 20 positives weighted 5, with estimator class weights disabled. Each row/positive is visited once per epoch. Coefficients/objectives remain finite; finite-budget completion is explicitly not claimed as convergence. |
| Histogram integration | A 200-row fixture preserves raw NaNs, uses estimator class weights once, disables internal random early stopping and completes exactly four requested iterations. Temporary disk mappings are removed. |
| Model reload/schema | Both complete fitted fixture pipelines reproduce probabilities after joblib reload, accept the exact named input schema, reject reversed columns and validate bundle feature order. |
| Run resume | A stale saved configuration is rejected before fitting and remains unchanged. |
| Evaluation | Hand-calculated AP, prevalence, Brier score, threshold confusion counts, inclusive ties and false-alert denominators match. One-class metrics are explicit; malformed probabilities fail. |

## Full corrected-cache audit

Executed independently after cache construction in **35.33 seconds**, scanning
**17,798,320 rows** in chunks of at most 50,000. No original CSV rescan or production
model fit was performed. The completed original-panel audit is PASS with 277
listed checks and zero violations; its checksum remains
`8791c96e460e6294791bbbf1ad0d6aad68e31a98be28dbcc6060463f94ccfc9d`.

The verifier independently confirmed the exact 36-column order and cache version
`ignition-v1-weather-asof-global-date-v2`, all 1,840 frozen fire-season dates,
contiguous complete date slices, and every date's exact permutation of 9,673 grid
IDs. All labels are binary; 7,785 positives and each year's row/positive counts
match the original integrity report. No infinities or listed physical-range,
station-count, quality-fraction, missing/null, distance or rain-coverage violations
were found.
Binary sizes also match exactly: `X.f64` is 5,125,916,160 bytes, `y.u8` is
17,798,320 bytes and `grid.u16` is 35,596,640 bytes.

Corrected daily weather is missing on **1,275,992 rows**, and seven-day rain on
**2,171,969 rows**. These differ materially from the original EDA because both the
global date-index join and as-of station selection were corrected. The original
CSV/EDA still describes the uncorrected version.

The verifier independently recomputed seven-day rain sums and coverage for all
9,673 cells on **1,780 dates**. The first six May dates in each year cannot be
reconstructed solely from panel rows because preceding April rows are outside the
panel; the strict-past source iterator is covered by independent fixtures.

For three geographically distinct cells on each of **2010-05-01, 2015-05-01,
2016-07-15 and 2019-10-31**, the verifier independently ranked stations eligible
by the prior observation date, selected the nearest four, joined indexed raw
records by date/key and recomputed all 11 weather/FWI fields. All 12 cell/date
cases match the cache within 1e-6, including missing and partially covered cases.
This checks the indexed records against reconstructed cell features; it does not
rescan every original archive line or prove historical publication availability.

Reproduce the independent full numeric audit from the repository root:

```powershell
.venv-modeling/Scripts/python.exe -c "import sys,json; sys.path.insert(0,'tests'); from test_ignition_modeling import audit_corrected_cache; print(json.dumps(audit_corrected_cache('data/interim/ignition_modeling_cache','docs/modeling/data_quality_report.json'),indent=2))"
```

## Executed artifact checks

Both saved chronological smoke pipelines in `artifacts/ignition_modeling/smoke`
were independently reloaded and used to regenerate probabilities for **all
299,863 May 2011 validation rows**, containing **125 positives**. Cache-manifest
hash and the 36-to-57/37 feature schemas match. Tie-aware AP was independently
computed from sorted probabilities and cumulative positive counts; Brier score
was directly recomputed. Both reproduce `smoke_results.json` within 1e-12:

| Smoke pipeline | Average precision | Brier score |
| --- | ---: | ---: |
| Logistic | 0.0007154197092167486 | 0.0007412071366102292 |
| Histogram | 0.005205036957592549 | 0.0004161135336017339 |

These are limited chronological smoke results, not the complete development
comparison or final test performance.

All **40 development checkpoints** in `artifacts/ignition_modeling/full_run`
were independently audited: eight configurations across validation years
2012–2016. Saved labels equal every complete natural validation partition; fold
training counts, class weights, candidate parameters, feature schemas, histogram
iteration/early-stopping settings and logistic row visitation match their
contracts. AP, ROC-AUC, Brier score and log loss were independently recomputed
from each saved prediction vector, using tie-grouped counts for ranking metrics.
All checkpoint values and candidate summaries match the development comparison.

## Final production acceptance

The complete read-only artifact audit finished in **57.48 seconds**. It did not
refit or retune any model. Current package versions, code hashes, cache-manifest
hash and the frozen model/configuration hashes match the saved execution.

The selected model is **unweighted HistGradientBoosting candidate 2, 40
iterations**, selected by the highest mean AP across the five development years:
**0.00644949739737252**. Both final pipelines retain exactly 36 named source
inputs, with 57 transformed logistic inputs or 37 histogram inputs, and their
preprocessing fit-row counts match the complete 2010–2016 training partition.
The final training diagnostics also match **12,458,824 rows and 5,290 positives**:
logistic candidate 3 and histogram candidate 2. The loaded selected classifier's
actual parameters equal the frozen winning configuration. Artifact timestamps
follow selected model → freeze → test predictions → final report.

The independent auditor recomputed the saved 2019 test metrics from all
**1,779,832 rows and 509 positives**:

| Metric | Independently verified value |
| --- | ---: |
| Prevalence | 0.000285982047743832 |
| Average precision | 0.007274601011646913 |
| ROC-AUC | 0.8891953135479145 |
| Brier score | 0.00028565196046010543 |
| Log loss | 0.0023054209887880795 |

Weather-availability strata, dates and row order match the numerical cache.
Reloading `selected_system.joblib` reproduces saved predictions for **512 fixed,
evenly spaced test rows** within 1e-14. This is verification of the frozen run,
not a new model selection or test-time adjustment.

All eight stored thresholds exactly equal quantiles recomputed from the selected
system's **2018** scores. Their frozen 2019 true/false-alert counts and
false-alert denominator match persisted results. **No operational threshold was
selected.** Sigmoid calibration fitted in 2017 was rejected using 2018 log loss;
the saved final system has no calibrator. Final report freeze metadata equals
the standalone freeze record. The frozen selected-system SHA-256 is
`ac84064c386041a4d0201be3d99eaede8fdf059a78f765478d3a269e8c7865ac`.

Reproduce production artifact verification without fitting:

```powershell
.venv-modeling/Scripts/python.exe -c "import sys,json; sys.path.insert(0,'tests'); from test_ignition_modeling import audit_production_artifacts; print(json.dumps(audit_production_artifacts('artifacts/ignition_modeling/full_run','data/interim/ignition_modeling_cache'),indent=2))"
```

## Integration findings

| Severity | Location | Evidence | Required correction |
| --- | --- | --- | --- |
| P1, corrected | `scripts/ignition_modeling/run.py`, cached development result reuse | The initial implementation accepted existing fold JSON by filename while rewriting execution configuration. | The coordinator added an immutable comparison including code/cache hashes, features and search configuration, plus cached fold/parameter/prediction-file checks. The independent rejection regression test passes. |

Corrected weather received the required independent full-cache range and
quality/null-consistency audit after the original-panel scan.

## Scope limits

The unit tests use small deliberately adversarial fixtures; the separate cache
audit scans the complete 17.8M-row numeric cache, and production artifacts have
been inspected independently. Finite-budget logistic optimization does not prove
convergence; its objectives and changes are recorded. Historical publication
timestamps remain unavailable; acceptance is for a retrospective hindcast on the
frozen v1 grid, whose study-period event selection remains a limitation. The
verifier does not independently reconstruct every NFDB source event or raw-source
publication/revision history.
