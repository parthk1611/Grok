# Ignition model comparison

Status: implemented, trained and independently verified on 2026-10-09.
See [the executed comparison](modeling/final_report.md) and
[verification evidence](modeling/verification_report.md). The original research
plan below defines the comparison for each Ontario Fire Region 10 km cell/day
in the 2010–2019 fire-season panel. Modelling uses a separately versioned cache
that corrects the original weather station selection and source-date joins.

| Approach | Role | Reason for inclusion |
| --- | --- | --- |
| Historical-rate baseline | Predict the training partition's ignition frequency for every row | Establish a benchmark without input features. |
| Logistic regression | Interpretable ignition classifier | Assess whether the proposed inputs improve on the historical rate. |
| Histogram gradient boosting | Nonlinear ignition classifier | Assess whether nonlinear relationships and interactions improve prediction. |

Use the same chronological holdouts for all three approaches. Compare
precision–recall performance and probability calibration; record the training
and evaluation dates, positive counts, and preprocessing choices.

For the two feature-based models, use calendar/solar, static terrain,
prior-day CWFIS weather/FWI, past-only ignition history, and source-quality
fields described in [the feature contract](ignition_features.md).
Exclude labels (`ignition`, `ignition_count`, `large_fire_200ha`) and
identifier/provenance metadata from predictors. Freeze the exact input list
before training.

Retain rows with missing weather. Fit any imputation, scaling, feature
selection, and missingness/censoring indicators within each training partition.
Preserve the distinction between missing weather and no earlier observed
same-cell ignition in the available 2010+ history. This is a retrospective
hindcast comparison; archive timing does not establish operational availability.

## Separate fire-spread research

Cell2Fire with Canadian FBP is a candidate for future fire-spread research.
It is outside this ignition comparison. Final perimeters and final size must
not become ignition predictors; spread requires its own incident-conditioned
progression dataset and evaluation.
