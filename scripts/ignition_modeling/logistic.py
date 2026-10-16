"""Full-row, bounded-memory logistic regression optimized by averaged SGD."""
from copy import deepcopy
from time import perf_counter

import numpy as np
import psutil
from sklearn.linear_model import SGDClassifier
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_is_fitted

from .contract import FEATURE_NAMES, SEED, check_matrix
from .preprocessing import NumericPreprocessor


def _rows(X, indices):
    return X.iloc[indices] if hasattr(X, "iloc") else X[indices]


def _configuration(params):
    defaults = {"alpha": 1e-4, "class_weight": None, "epochs": 3, "batch_size": 50000}
    unknown = set(params) - set(defaults)
    if unknown:
        raise ValueError(f"Unknown logistic parameters: {sorted(unknown)}")
    config = defaults | params
    alpha = config["alpha"]
    if isinstance(alpha, bool) or not np.isscalar(alpha) or not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("alpha must be a finite positive number")
    if config["class_weight"] not in (None, "balanced"):
        raise ValueError("class_weight must be None or 'balanced'")
    for name in ("epochs", "batch_size"):
        value = config[name]
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
        config[name] = int(value)
    if config["batch_size"] > 50000:
        raise ValueError("batch_size must not exceed 50000 for bounded memory")
    config["alpha"] = float(alpha)
    return config


def _class_counts(y, n_rows, batch_size):
    if y.ndim != 1 or len(y) != n_rows or y.dtype.kind not in "biuf":
        raise ValueError("y must be a numeric one-dimensional binary vector aligned with X")
    positives = 0
    for start in range(0, n_rows, batch_size):
        labels = y[start:start + batch_size]
        if not np.all((labels == 0) | (labels == 1)):
            raise ValueError("y must contain only finite binary labels 0 and 1")
        positives += int(np.count_nonzero(labels))
    counts = {0: n_rows - positives, 1: positives}
    if not all(counts.values()):
        raise ValueError("The training partition must contain both ignition classes")
    return counts


def train_logistic(X, y, params, *, seed=SEED, preprocessor=None):
    """Return a fitted raw-input Pipeline and JSON-compatible training diagnostics.

    X and y must contain only the current training partition. A supplied fitted
    NumericPreprocessor must also have been fitted on that exact partition.
    """
    started = perf_counter()
    config = _configuration(params)
    check_matrix(X)
    y = np.asarray(y)
    counts = _class_counts(y, len(X), config["batch_size"])
    weights = ({label: len(y) / (2 * count) for label, count in counts.items()}
               if config["class_weight"] == "balanced" else {0: 1.0, 1: 1.0})
    process = psutil.Process()
    rss_start = process.memory_info().rss
    observed_peak_rss = rss_start

    def sample_memory():
        nonlocal observed_peak_rss
        info = process.memory_info()
        observed_peak_rss = max(observed_peak_rss, info.rss)
        return info

    if preprocessor is None:
        fitted = NumericPreprocessor(chunk_size=config["batch_size"]).fit(X)
        preprocessing_source = "fitted_on_current_training_partition"
    else:
        if not isinstance(preprocessor, NumericPreprocessor):
            raise ValueError("preprocessor must be a fitted NumericPreprocessor")
        check_is_fitted(preprocessor, "output_names_")
        if preprocessor.n_samples_seen_ != len(X):
            raise ValueError("Supplied preprocessing has a different training row count")
        fitted = deepcopy(preprocessor)
        preprocessing_source = "deepcopy_of_caller_training_partition_fit"
    if tuple(fitted.feature_names_in_) != FEATURE_NAMES or len(fitted.output_names_) != 57:
        raise ValueError("Preprocessing must preserve the fixed 36-to-57 feature schema")
    sample_memory()
    preprocessing_seconds = perf_counter() - started

    # Bound the largest weighted per-example step; the objective still uses the
    # original fold-derived weights, without clipping, duplication or sampling.
    eta0 = 0.05 / max(weights.values())
    model = SGDClassifier(loss="log_loss", penalty="l2", alpha=config["alpha"],
                          learning_rate="constant", eta0=eta0, average=True,
                          shuffle=False, random_state=seed, class_weight=None,
                          max_iter=1, tol=None)
    rng = np.random.default_rng(seed)
    starts = np.arange(0, len(X), config["batch_size"])
    diagnostics = []
    previous_coef = previous_intercept = previous_objective = None
    fitting_seconds = objective_seconds = 0.0
    largest_transformed_batch_bytes = 0

    def objective():
        natural_sum = weighted_sum = 0.0
        for start in starts:
            stop = min(int(start) + config["batch_size"], len(X))
            raw = _rows(X, slice(int(start), stop))
            transformed = fitted.transform(raw)
            sample_memory()
            labels = y[int(start):stop]
            logits = model.decision_function(transformed)
            losses = np.logaddexp(0, logits) - labels * logits
            if not np.isfinite(losses).all():
                raise FloatingPointError("Nonfinite full-training logistic objective")
            natural_sum += float(losses.sum())
            weighted_sum += float(np.dot(losses, np.where(labels == 1, weights[1], weights[0])))
            del transformed, raw
        penalty = 0.5 * config["alpha"] * float(np.square(model.coef_).sum())
        natural, weighted = natural_sum / len(X), weighted_sum / len(X)
        return natural, weighted, penalty, weighted + penalty

    for epoch in range(1, config["epochs"] + 1):
        epoch_started = perf_counter()
        epoch_positives = rows_seen = positive_batches = 0
        for start in rng.permutation(starts):
            stop = min(int(start) + config["batch_size"], len(X))
            indices = rng.permutation(np.arange(int(start), stop))
            labels = y[indices]
            raw = _rows(X, indices)
            transformed = fitted.transform(raw)
            largest_transformed_batch_bytes = max(largest_transformed_batch_bytes, transformed.nbytes)
            sample_memory()
            if not np.isfinite(transformed).all():
                raise FloatingPointError(f"Nonfinite preprocessing output in epoch {epoch}")
            sample_weights = np.where(labels == 1, weights[1], weights[0])
            try:
                model.partial_fit(transformed, labels, classes=np.array([0, 1]),
                                  sample_weight=sample_weights)
            except (ValueError, FloatingPointError) as exc:
                raise RuntimeError(f"Logistic partial_fit failed in epoch {epoch}, chunk {int(start)}") from exc
            if not np.isfinite(model.coef_).all() or not np.isfinite(model.intercept_).all():
                raise FloatingPointError(f"Nonfinite logistic coefficients in epoch {epoch}")
            positives = int(np.count_nonzero(labels))
            epoch_positives += positives
            positive_batches += int(positives > 0)
            rows_seen += len(labels)
            sample_memory()
            del transformed, raw, indices, labels, sample_weights
        epoch_seconds = perf_counter() - epoch_started
        fitting_seconds += epoch_seconds
        if rows_seen != len(y) or epoch_positives != counts[1]:
            raise RuntimeError("An epoch did not visit every training row and positive exactly once")
        objective_started = perf_counter()
        natural, weighted, penalty, value = objective()
        elapsed_objective = perf_counter() - objective_started
        objective_seconds += elapsed_objective
        coef_change = (None if previous_coef is None else
                       float(np.linalg.norm(model.coef_ - previous_coef)))
        diagnostics.append({
            "epoch": epoch, "rows_seen": rows_seen, "positives_seen": epoch_positives,
            "negatives_seen": rows_seen - epoch_positives, "batches": len(starts),
            "batches_with_positives": positive_batches,
            "natural_prevalence_train_log_loss": natural,
            "weighted_train_log_loss": weighted, "l2_penalty": penalty,
            "weighted_train_objective": value,
            "objective_change": None if previous_objective is None else value - previous_objective,
            "coefficient_l2_norm": float(np.linalg.norm(model.coef_)),
            "coefficient_l2_change": coef_change,
            "coefficient_relative_change": None if previous_coef is None else
                coef_change / max(float(np.linalg.norm(previous_coef)), 1e-12),
            "intercept": float(model.intercept_[0]),
            "intercept_change": None if previous_intercept is None else
                float(model.intercept_[0] - previous_intercept),
            "fitting_seconds": epoch_seconds, "objective_seconds": elapsed_objective,
        })
        previous_coef = model.coef_.copy()
        previous_intercept = float(model.intercept_[0])
        previous_objective = value

    memory = sample_memory()
    pipeline = Pipeline([("preprocessor", fitted), ("classifier", model)])
    report = {
        "status": "finite_budget_completed", "estimator": "SGDClassifier",
        "model_family": "L2 logistic regression", "optimizer": "averaged stochastic gradient descent",
        "params": config, "seed": int(seed), "loss": "log_loss", "penalty": "l2",
        "learning_rate": "constant", "eta0": eta0, "average": True,
        "training_rows": len(y), "class_counts": {str(k): v for k, v in counts.items()},
        "training_class_weights": {str(k): v for k, v in weights.items()},
        "weight_source": "current training partition only", "estimator_class_weight": None,
        "resampling": False, "rows_processed": len(y) * config["epochs"],
        "input_features": list(FEATURE_NAMES), "transformed_features": list(fitted.output_names_),
        "preprocessing_source": preprocessing_source, "epochs": diagnostics,
        "convergence_proven": False,
        "convergence_note": "Finite epochs and finite coefficients do not prove convergence; inspect epoch objectives and changes.",
        "nonfinite_failure": False, "preprocessing_seconds": preprocessing_seconds,
        "fitting_seconds": fitting_seconds, "objective_seconds": objective_seconds,
        "total_seconds": perf_counter() - started, "rss_bytes_start": rss_start,
        "observed_peak_rss_bytes": observed_peak_rss, "rss_bytes_end": memory.rss,
        "process_lifetime_peak_working_set_bytes": getattr(memory, "peak_wset", None),
        "largest_transformed_batch_bytes": largest_transformed_batch_bytes,
        "memory_note": "RSS sampled after preprocessing and batch transforms/fits; process lifetime peak includes earlier work.",
    }
    return pipeline, report
