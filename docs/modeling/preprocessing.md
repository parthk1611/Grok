# Ignition preprocessing

`scripts/ignition_modeling/preprocessing.py` uses the frozen, ordered 36-feature
manifest. Keys, dates, source metadata and labels are excluded before this stage.
Numeric arrays must have 36 columns in manifest order; DataFrames additionally
require those exact column names and order. Inputs must be numeric and may contain
NaN. Infinity is rejected. Each chronological training fold fits its own
transformer; calibration, threshold and final-test rows only use `transform`.

## Logistic inputs: 57 columns

`NumericPreprocessor` learns one median per source column from training rows
only. Missing values receive that median. A column with no observed training
values receives the numeric fallback 0 and retains its fixed missingness
indicator. Later observed values remain observed; the holdout cannot alter the
training median or scaler. All 36 original predictors remain in the output.

The transformer adds 21 indicators in `INDICATOR_FEATURES` manifest order:
12 weather/FWI fields, five terrain fields, three nullable station-quality fields
and the same-cell history interval. Indicators are always present, including when
a training fold contains no missing values, and remain unscaled 0/1. Names end in
`__missing`, except `fire_history_days_since_same_cell__censored`.

A blank history interval means no earlier observed same-cell fire in the available
history beginning in 2010. It is censored history, not zero days since a fire and
not an ordinary failed weather observation. Its imputed numeric value is only a
model placeholder; the separate censor indicator preserves that distinction.

After imputation, numeric inputs use training population mean and standard
deviation. Constant columns use scale 1. Existing calendar and terrain sine/cosine
features, `calendar_is_weekend` and `cwfis_missing` remain unscaled. Scaling makes
regularization comparable across quantities such as elevation, rainfall and fire
counts. No resampling, label-derived transformation or feature selection occurs
here. A training-only all-missing column cannot provide an observed-value effect
until a later refit includes observations.

## Histogram boosting inputs: 37 columns

`HistoryIndicator` retains all 36 original columns, including native NaN, and adds
the same explicit history censor indicator as column 37. It performs no learned
imputation or scaling. HistGradientBoosting handles missing values directly; the
history flag records the meaning of censored history separately.

## Memory and persistence

Both estimators accept `scratch_dir=None, chunk_size=50000`. Statistics and output
blocks are limited to at most 50,000 rows. Median fitting copies one training
column at a time and uses an in-place NaN median; it does not copy the entire
training matrix. Post-imputation means and variances use a stable combination of
chunk statistics. Fitted attributes contain only the small statistics and schema,
so the inference pipeline can save the fitted transformer with its classifier.

`fit_transform(X)` returns a float64 C-order disk memmap when `scratch_dir` is set;
otherwise it returns an array. Files have unique names, so successive folds do not
overwrite each other. The caller owns successful memmap cleanup. `transform(X)`
returns an array; large training or prediction jobs must supply chunks rather
than materializing the complete transformed panel. Failed disk transformations
remove their partial output. Source matrices are never modified.

Synthetic acceptance checks passed for 57/37 output schemas, missing and
all-missing training columns, fixed indicators, training-only statistics,
nullable numeric DataFrames, reordered-column rejection, infinity rejection,
chunk-size invariance, sklearn cloning and memmap output. These checks do not
constitute model training or evidence of predictive performance.
