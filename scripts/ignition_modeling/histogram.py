"""Native-NaN histogram boosting with a temporary, disk-backed feature matrix."""
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import psutil
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline

from .contract import FEATURE_NAMES, HISTORY_FEATURE
from .preprocessing import HistoryIndicator

DEFAULT_PARAMS = {
    "max_iter": 20, "class_weight": None, "learning_rate": 0.1,
    "max_leaf_nodes": 15, "min_samples_leaf": 100, "l2_regularization": 1.0,
}


class _MemoryMonitor:
    def __init__(self):
        self.process = psutil.Process()
        self.interval = 0.05
        self.stop = threading.Event()
        self.rss_start = self.process.memory_info().rss
        self.peak_rss = self.rss_start
        self.available_start = psutil.virtual_memory().available
        self.min_available = self.available_start
        self.errors = []
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop.is_set():
            try:
                self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
                self.min_available = min(self.min_available, psutil.virtual_memory().available)
            except psutil.Error as error:
                self.errors.append(type(error).__name__)
                return
            self.stop.wait(self.interval)

    def finish(self):
        self.stop.set()
        self.thread.join()
        info = self.process.memory_info()
        self.peak_rss = max(self.peak_rss, info.rss)
        return {
            "rss_at_start_bytes": self.rss_start,
            "rss_at_finish_bytes": int(info.rss),
            "peak_sampled_rss_bytes": self.peak_rss,
            "peak_sampled_rss_increment_bytes": self.peak_rss - self.rss_start,
            "available_at_start_bytes": self.available_start,
            "minimum_sampled_available_bytes": self.min_available,
            "sampling_interval_seconds": self.interval,
            "process_lifetime_peak_working_set_bytes": getattr(info, "peak_wset", None),
            "monitor_errors": self.errors,
        }


def _class_counts(y, n_rows):
    if getattr(y, "ndim", None) != 1 or len(y) != n_rows:
        raise ValueError("Expected one binary ignition label for every training row")
    if getattr(y, "dtype", None) is None or y.dtype.kind not in "biuf":
        raise ValueError("Ignition labels must be numeric 0 or 1")
    positive = 0
    for start in range(0, n_rows, 50000):
        block = np.asarray(y[start:start + 50000])
        if np.any((block != 0) & (block != 1)):
            raise ValueError("Ignition labels must contain only 0 and 1")
        positive += int(np.count_nonzero(block == 1))
    counts = {"0": n_rows - positive, "1": positive}
    if min(counts.values()) == 0:
        raise ValueError("Histogram training requires both ignition classes")
    return counts


def _memory_estimate(n_rows, balanced):
    features = len(FEATURE_NAMES) + 1
    # Binned uint8 data and its temporary missing mask, then labels, predictions,
    # gradients/hessians, row indices and (only if requested) class weights.
    estimator = n_rows * (2 * features + 32 + (8 if balanced else 0))
    chunk_buffers = min(n_rows, 50000) * (36 + features) * 8
    binning_sample = min(n_rows, 200000) * features * 8
    return {
        "transformed_disk_bytes": n_rows * features * 8,
        "estimated_estimator_peak_bytes": estimator,
        "estimated_chunk_and_binning_bytes": chunk_buffers + binning_sample,
        "safety_margin_bytes": 256 * 1024**2,
        "estimated_additional_anonymous_memory_bytes": estimator + chunk_buffers
            + binning_sample + 256 * 1024**2,
        "scope": "Conservative estimate, not a hard bound; excludes reclaimable mapped pages and caller-owned inputs",
    }


def _remove_owned_mapping(mapped, directory):
    path = Path(mapped.filename).resolve()
    if path.parent != directory or not path.name.startswith("HistoryIndicator-") or path.suffix != ".f64":
        raise RuntimeError("Refusing to delete a transformed file outside the provided scratch directory")
    mapped._mmap.close()
    path.unlink()


def train_hgb(X, y, params, *, scratch_dir, seed=385):
    """Fit all supplied rows and return a fitted raw-36-input pipeline plus JSON diagnostics."""
    unknown = set(params) - DEFAULT_PARAMS.keys()
    if unknown:
        raise ValueError(f"Unsupported histogram parameters: {sorted(unknown)}")
    configuration = {**DEFAULT_PARAMS, **params}
    if configuration["class_weight"] not in (None, "balanced"):
        raise ValueError("class_weight must be None or 'balanced'")
    if scratch_dir is None:
        raise ValueError("A scratch directory is required for disk-backed histogram training")
    n_rows = len(X)
    counts = _class_counts(y, n_rows)
    weights = {key: n_rows / (2 * count) if configuration["class_weight"] else 1.0
               for key, count in counts.items()}
    directory = Path(scratch_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    estimate = _memory_estimate(n_rows, configuration["class_weight"] == "balanced")
    disk_free = shutil.disk_usage(directory).free
    if disk_free < estimate["transformed_disk_bytes"] + 64 * 1024**2:
        raise OSError("Insufficient scratch disk space for the float64 transformed training matrix")
    preprocessor = HistoryIndicator(scratch_dir=str(directory))
    classifier = HistGradientBoostingClassifier(
        **configuration, early_stopping=False, categorical_features=None,
        random_state=seed,
    )
    monitor = _MemoryMonitor()
    monitor.thread.start()
    started = time.perf_counter()
    mapped = None
    try:
        mapped = preprocessor.fit_transform(X, y)
        if not isinstance(mapped, np.memmap) or mapped.dtype != np.float64 or not mapped.flags.c_contiguous:
            raise RuntimeError("Histogram preprocessing must return a C-order float64 disk mapping")
        classifier.fit(mapped, y)
    finally:
        try:
            if mapped is not None:
                _remove_owned_mapping(mapped, directory)
        finally:
            memory = monitor.finish()
    elapsed = time.perf_counter() - started
    pipeline = Pipeline([("preprocessor", preprocessor), ("classifier", classifier)])
    diagnostics = {
        "model": "HistGradientBoostingClassifier", "sklearn_version": sklearn.__version__,
        "seed": int(seed), "params": configuration, "n_rows": n_rows,
        "class_counts": counts, "effective_class_weights": weights,
        "weight_application": "Estimator class_weight only; no external sample_weight or resampling",
        "n_features_raw": len(FEATURE_NAMES), "n_features_transformed": int(classifier.n_features_in_),
        "raw_feature_schema": list(FEATURE_NAMES),
        "transformed_feature_schema": preprocessor.get_feature_names_out().tolist(),
        "history_censor_feature": f"{HISTORY_FEATURE}__censored",
        "native_nan": True, "scaling": False, "imputation": False,
        "early_stopping": False, "categorical_features": None,
        "internal_random_validation": False,
        "iteration_selection": "External chronological development folds",
        "n_iter": int(classifier.n_iter_), "elapsed_seconds": elapsed,
        "memory_estimate": estimate, "memory_actual": memory,
        "estimate_exceeds_initial_available_memory": estimate["estimated_additional_anonymous_memory_bytes"]
            > memory["available_at_start_bytes"],
        "scratch_disk_free_at_start_bytes": disk_free,
        "transformed_scratch_file_removed": True,
    }
    return pipeline, diagnostics
