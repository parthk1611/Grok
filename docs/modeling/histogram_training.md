# Histogram gradient boosting training

`scripts/ignition_modeling/histogram.py::train_hgb` returns a fitted sklearn
pipeline and JSON diagnostics. The pipeline accepts the exact ordered 36-column
ignition predictor manifest. It keeps numeric NaNs, adds the explicit
`fire_history_days_since_same_cell__censored` indicator, and neither imputes nor
scales inputs. Labels, identifiers and source metadata cannot enter its input
matrix; named DataFrame inputs must match the complete ordered predictor schema.

Candidates use 20 or 40 iterations, class weight `None` or `balanced`, learning
rate 0.1, 15 maximum leaves, minimum 100 samples per leaf and L2 regularization
1.0. The coordinator selects iterations and weighting from five chronological
development folds. `early_stopping=False` prevents sklearn's default random
internal validation split. `categorical_features=None` prevents an unnecessary
categorical column-transformer copy. Installed sklearn 1.7.2 supports the
estimator's `class_weight`; no additional sample weights or resampling are
applied. Both classes must be present.

Training uses every supplied row. Preprocessing writes a C-order float64
37-column temporary mapping under the caller's scratch directory, in blocks of
at most 50,000 rows. Its dtype avoids sklearn converting the entire matrix to
float64 in anonymous RAM. For the 12,458,824-row 2010–2016 partition the mapping
uses approximately 3.44 GiB on disk. This reduces preprocessing memory, but
sklearn histogram boosting still allocates binned data, class labels, predictions,
gradients, Hessians and row-index buffers. The conservative estimate including
256 MiB headroom is approximately 1.6–1.7 GiB of additional anonymous memory.
Mapped pages and caller-owned inputs are additional; memory pressure can still
cause paging. The estimate is not an enforceable memory limit.

Diagnostics report the estimated allocation, initial and minimum available
physical memory, sampled process RSS peak and increment, elapsed time, class
counts and effective weights, iteration count and both feature schemas. Sampling
runs every 50 ms and can miss shorter spikes. On Windows the process lifetime
peak working set is also recorded separately; it is not attributed exclusively
to this fit. The coordinator should inspect available memory before full fits,
especially while other processes hold large arrays.

Only the exact mapping created for this fit is closed and deleted, including
when estimator fitting fails. Cleanup verifies its resolved parent equals the
provided scratch directory and its generated filename matches the transformer's
prefix and suffix. It does not remove the directory or any caller-owned files.
Neither pipeline step retains the transformed matrix. Saved pipelines predict
from raw predictor inputs; callers should batch prediction for large panels.

Production fitting remains behind the coordinator's data/provenance/verification
gate. Local acceptance uses a small synthetic fixture with weather/history NaNs,
both weighting options, explicit history-censor checks and joblib reload parity.

Acceptance passed on 2026-10-09 with 240 rows (220 negative, 20 positive): native
NaNs and censor states survived transformation; three requested iterations ran
without internal early stopping; balanced class weights were 0.5454545 and 6.0;
joblib reload probabilities matched exactly. Scratch cleanup passed after both
successful fitting and an intentionally invalid estimator configuration, while
a caller-owned sentinel remained. An extra metadata column was rejected.
Sampled fixture RSS peaked at approximately 145 MiB. This fixture verifies
behavior, not the runtime or peak memory of a full-panel fit.
