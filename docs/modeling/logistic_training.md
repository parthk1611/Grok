# Logistic regression training

`scripts/ignition_modeling/logistic.py` fits L2 logistic regression with
`SGDClassifier(loss="log_loss", penalty="l2")`. SGD is the optimizer, not a
separate prediction family. It avoids materializing the full 57-column float64
preprocessed matrix: the 36-column float32 source matrix alone is about 2.4 GiB,
and current available RAM cannot safely hold its transformed matrix and copies.

## Training contract

`train_logistic(X, y, params, seed=385, preprocessor=None)` returns a fitted
scikit-learn `Pipeline` and JSON-compatible diagnostics. The pipeline stores both
training-fitted numeric preprocessing and the classifier; save the whole object
with joblib. Prediction must also use chunks, as `run.predict_chunks` does.

The input is the exact ordered 36-feature contract. Labels must be binary, row
aligned, and contain both classes. Preprocessing produces the fixed 57 outputs:
36 imputed/scaled inputs and 21 missingness/history-censoring indicators. If the
caller supplies a fitted `NumericPreprocessor`, it must have been fitted on the
exact current training partition. Training copies it rather than mutating it.
The function checks its fitted schema and training row count, but cannot infer
the provenance of an externally supplied fit; the chronological runner owns
that partition boundary.

Each epoch visits every training row exactly once. Chunk order and row order
inside each chunk are reproducibly shuffled with the supplied seed. Rows with
missing weather remain in training. There is no resampling, undersampling,
duplication, or validation/test use. Individual chunks may contain no ignitions;
both classes must occur in the complete training partition, and every epoch
checks its total positive coverage.

## Bounded configuration

The development candidates use `alpha` in `{1e-4, 1e-3}`, `class_weight` in
`{None, "balanced"}`, three epochs and batches of at most 50,000 rows.
Unweighted fits use weight 1 for both classes. Balanced weights are
`n_training / (2 * n_class)` calculated from the current training labels only.
They are passed once through `sample_weight`; the estimator's `class_weight`
remains `None` to prevent double weighting.

The optimizer uses averaged coefficients and a constant learning rate
`eta0 = 0.05 / max(training_class_weights)`. This bounds the largest weighted
per-example step at 0.05 while preserving the original weighted objective;
rare positive-class weights are not clipped. The same fixed rule applies to
all development folds. It reduces the risk of enormous initial updates from
the standard optimal schedule under rare-event weighting. It does not bound
the feature-scaled gradient or guarantee convergence.

The diagnostic objective is evaluated over the complete training partition,
in chunks, after each epoch:

`mean(training_weight * logistic_loss) + alpha / 2 * ||coefficients||²`.

The intercept is not penalized. Stable logits-based loss avoids concealing
large losses through probability clipping. Diagnostics include natural-
prevalence and weighted training log loss, objective changes, coefficient and
intercept changes, class/row coverage, runtime and sampled process RSS. Memory
figures distinguish sampled RSS from the Windows process-lifetime peak, which
can include previous fits. Errors or nonfinite transformed inputs,
coefficients or objectives stop training with an exception.

Three epochs are a computational budget, **not evidence of convergence**.
Candidate performance is assessed on chronological development folds. If
optimization diagnostics show instability or continued large changes, that
limitation must be reported; any increased epoch budget must be decided using
development data before the final test is opened. These training losses are
diagnostics, not held-out performance estimates.

## Synthetic acceptance checks

Acceptance uses synthetic data only before the root training gate. Checks cover
both weighting modes, multiple chunks, exact full-row/positive coverage,
deterministic replay, finite probabilities, learned signal, training-only
preprocessing, schema rejection, and identical predictions after a complete
pipeline save/reload. No production model fit is authorized by this module.
