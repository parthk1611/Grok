# Ignition evaluation protocol

Predict `ignition` for an Ontario Fire Region 10 km cell and local calendar day.
The v1 panel is a retrospective hindcast; metrics do not establish operational
forecast availability or fire-spread performance.

## Freeze the temporal experiment before fitting

| Partition | Years | Permitted use |
| --- | --- | --- |
| Development training | 2010–2016 | Expanding-year folds for model and preprocessing choices |
| Calibration | 2017 | Fit the optional sigmoid calibrator using held-out model scores |
| Development assessment | 2018 | Compare raw/calibrated probabilities and inspect threshold tradeoffs |
| Final test | 2019 | One evaluation after the model, calibration choice and reporting thresholds are frozen |

Keep every row from a target date in the same temporal partition. Imputation,
scaling, feature selection and any class weighting or negative sampling belong
to training only. The final fitted model uses 2010–2016; 2017 remains separate
for calibration, and 2018/2019 remain separate for assessment. A different refit
policy requires a new declared experiment, not an unrecorded change.

Validation, calibration and test sets retain all cell-days at their natural
class proportions, including missing-weather rows. Training weights or sampled
negatives must never be carried into evaluation metrics. Freeze the source
checksum, feature list, split definition, seeds, model parameters, calibration
choice and threshold list before accessing final test results.

## Ranking and probability metrics

- **Average precision (AP)** is primary. Report the evaluated partition's
  positive fraction alongside AP as its constant-score reference. Report
  `AP / prevalence` only when positives exist. This reference changes with each
  year or coverage group; it is not one fixed panel-wide value.
- **ROC AUC** is supplementary and defined only when both classes are present.
  It does not substitute for rare-event precision and alert burden.
- **Brier score and log loss** assess probability quality; lower is better.
  Log loss uses scikit-learn's finite numerical clipping at probability endpoints.
- **Reliability bins** use ten fixed probability intervals from 0 to 1. Report
  bin count, mean predicted probability and observed ignition fraction. Empty
  bins remain explicit. These are descriptive bins, not confidence intervals.
- **Quantile reliability bins** additionally resolve the small probabilities
  expected at ignition prevalence. Assign each distinct score to the decile of
  its weighted mid-rank, keeping all identical scores together. Omit empty
  groups, yielding at most ten bins (one for constant scores). Report observed
  score limits, counts, positive counts, mean probability and observed fraction.
  Large ties can make groups unequal in size. These descriptive groups are
  calculated from assessed scores and never used to fit calibration or choose
  a threshold from the final test.

Report sample count, positive count and prevalence for the entire partition,
each year and each `cwfis_missing` group (`0`: weather present; `1`: missing).
Include AP, ROC AUC, Brier score and log loss within those groups. This exposes
coverage changes, including the severe 2015–2016 weather gaps. No positive cases
means AP is undefined; one class means ROC AUC is undefined. Use JSON `null`,
not a fabricated score. Constant scores with both classes yield AP equal to
prevalence and ROC AUC 0.5.

## Calibration and thresholds

Fit optional sigmoid calibration on **2017 held-out scores only**. Inspect its
reliability, Brier score and log loss on **2018**, alongside raw probabilities.
Choose raw versus calibrated output before the 2019 evaluation. In-sample
calibration scores are not evidence of generalization. Endpoint probabilities
must use the declared logit clipping rule before sigmoid fitting/inference.

On 2018, show a threshold table with the rule `alert = probability >= threshold`:

- true positives, false positives, true negatives and false negatives;
- recall and precision (precision is undefined when there are no alerts);
- alerts and false alerts per observed local day;
- false alerts per 10,000 evaluated cell-days: `10000 * FP / number_of_rows`;
- false-positive rate: `FP / number_of_negative_rows`, with its different denominator.

There is no agreed operational alert budget or recall requirement. Present
tradeoffs without claiming an optimal operational threshold. Freeze the
2018-derived reporting thresholds and apply all of them to 2019 without selecting
one from 2019 outcomes. Report whole-panel alert burden at natural prevalence.
Daily averages use the number of distinct dates actually supplied, not a guessed
grid size or season length; partial panels and subgroups must be identified.

## Reusable module and artifacts

`scripts/ignition_modeling/evaluation.py` provides:

```python
evaluate_predictions(y, p, *, dates=None, weather_missing=None)
threshold_tradeoffs(y, p, thresholds=None, *, dates=None)
save_curves(y, p, output_dir, *, prefix="model", max_points=1001)
```

Inputs are aligned, nonempty one-dimensional binary labels and finite
probabilities in `[0, 1]`. Dates are naive local target dates, never issue-time
UTC timestamps. `weather_missing` must be binary when supplied. The caller is
responsible for complete keys, source timing and partition membership; the
metrics module cannot reconstruct them from labels and scores.

Save exact scalar metrics and threshold counts, both reliability-bin CSVs, and PR/ROC
curve CSVs. Curve exports may retain at most 1,001 display points; exported
coordinates do not replace exact full-score AP/AUC calculations. Record the
experiment manifest with the reports. Omit uncertainty intervals unless a
separately specified procedure accounts for dependence across cells and dates;
ordinary independent-row confidence intervals are unsuitable here.
