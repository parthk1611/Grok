"""Evaluation at natural prevalence; partition and threshold choices belong upstream."""

import csv
from pathlib import Path

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    log_loss,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)


def _binary(values, name, size=None):
    values = np.asarray(values)
    if values.ndim != 1 or (size is not None and values.size != size):
        raise ValueError(f"{name} must be a one-dimensional aligned array")
    if not np.all((values == 0) | (values == 1)):
        raise ValueError(f"{name} must contain only 0 and 1")
    return values.astype(np.int8, copy=False)


def _inputs(y, p):
    y, p = _binary(y, "y"), np.asarray(p, dtype=np.float64)
    if not y.size or p.ndim != 1 or p.size != y.size:
        raise ValueError("y and p must be nonempty, one-dimensional aligned arrays")
    if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise ValueError("p must contain finite probabilities in [0, 1]")
    return y, p


def _dates(values, size):
    raw = np.asarray(values)
    if raw.ndim != 1 or raw.size != size or raw.dtype.kind in "biufc":
        raise ValueError("dates must be an aligned array of local calendar dates")
    if raw.dtype.kind != "M" and np.any(np.char.str_len(raw.astype(str)) != 10):
        raise ValueError("dates must be local YYYY-MM-DD dates, not timestamps")
    try:
        dates = raw.astype("datetime64[D]")
    except (TypeError, ValueError) as error:
        raise ValueError("dates must contain valid local calendar dates") from error
    if np.any(np.isnat(dates)):
        raise ValueError("dates must not contain missing dates")
    if raw.dtype.kind == "M" and np.any(raw != dates):
        raise ValueError("dates must be calendar dates or midnight date values")
    return dates


def _metrics(y, p):
    count, positive = int(y.size), int(y.sum())
    prevalence = positive / count if count else None
    ap = float(average_precision_score(y, p)) if positive else None
    return {
        "n_samples": count,
        "n_positive": positive,
        "n_negative": count - positive,
        "prevalence": prevalence,
        "average_precision": ap,
        "ap_prevalence_ratio": ap / prevalence if positive else None,
        "roc_auc": float(roc_auc_score(y, p)) if 0 < positive < count else None,
        "brier_score": float(np.mean((p - y) ** 2)) if count else None,
        "log_loss": float(log_loss(y, p, labels=[0, 1])) if count else None,
    }


def _reliability(y, p):
    bins = np.minimum((p * 10).astype(np.int8), 9)
    result = []
    for index in range(10):
        selected = bins == index
        count = int(selected.sum())
        result.append({
            "lower": index / 10,
            "upper": (index + 1) / 10,
            "upper_inclusive": index == 9,
            "n_samples": count,
            "n_positive": int(y[selected].sum()),
            "mean_probability": float(p[selected].mean()) if count else None,
            "observed_prevalence": float(y[selected].mean()) if count else None,
        })
    return result


def _quantile_reliability(y, p):
    # Weighted mid-ranks keep tied scores together, including large tied tails.
    scores, inverse, counts = np.unique(p, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(counts) - counts / 2) / p.size
    score_groups = np.minimum((10 * ranks).astype(np.int8), 9)
    groups, result = score_groups[inverse], []
    for group in np.unique(score_groups):
        selected, values = groups == group, scores[score_groups == group]
        count = int(selected.sum())
        positive = int(y[selected].sum())
        result.append({
            "bin": len(result) + 1,
            "min_probability": float(values[0]),
            "max_probability": float(values[-1]),
            "n_samples": count,
            "n_positive": positive,
            "mean_probability": float(p[selected].mean()),
            "observed_prevalence": positive / count,
        })
    return result


def evaluate_predictions(y, p, *, dates=None, weather_missing=None):
    """Return JSON-safe exact metrics, reliability bins and optional strata.

    AP is undefined without positives; ROC AUC requires both classes. Dates are
    local target dates. No weights or sampling correction enter assessment.
    """
    y, p = _inputs(y, p)
    result = _metrics(y, p)
    result.update(reliability_bins=_reliability(y, p),
                  quantile_reliability_bins=_quantile_reliability(y, p), n_dates=None)
    if dates is not None:
        dates = _dates(dates, y.size)
        years = dates.astype("datetime64[Y]").astype(np.int64) + 1970
        result["n_dates"] = int(np.unique(dates).size)
        result["by_year"] = {
            str(int(year)): _metrics(y[years == year], p[years == year])
            for year in np.unique(years)
        }
    if weather_missing is not None:
        missing = _binary(weather_missing, "weather_missing", y.size)
        result["by_weather_missing"] = {
            str(flag): _metrics(y[missing == flag], p[missing == flag])
            for flag in (0, 1)
        }
    return result


def threshold_tradeoffs(y, p, thresholds=None, *, dates=None):
    """Evaluate p >= threshold; choose/freeze thresholds on development data.

    Daily rates need explicit dates. False alerts per 10,000 cell-days use all
    evaluated rows as denominator; false-positive rate uses negatives only.
    """
    y, p = _inputs(y, p)
    if thresholds is None:
        thresholds = [0, .0001, .0005, .001, .005, .01, .05, .1, .25, .5, .75, .9, 1]
    thresholds = np.asarray(thresholds, dtype=np.float64)
    if thresholds.ndim != 1 or not thresholds.size or not np.all(np.isfinite(thresholds)):
        raise ValueError("thresholds must be a nonempty one-dimensional finite array")
    if np.any((thresholds < 0) | (thresholds > 1)):
        raise ValueError("thresholds must be in [0, 1]")
    days = int(np.unique(_dates(dates, y.size)).size) if dates is not None else None
    order = np.argsort(p, kind="stable")
    prefix_positive = np.concatenate(([0], np.cumsum(y[order], dtype=np.int64)))
    indices = np.searchsorted(p[order], thresholds, side="left")
    count, positive = int(y.size), int(prefix_positive[-1])
    negative, result = count - positive, []
    for threshold, index in zip(thresholds, indices):
        alerts, tp = count - int(index), positive - int(prefix_positive[index])
        fp, fn = alerts - tp, positive - tp
        result.append({
            "threshold": float(threshold),
            "n_samples": count,
            "n_positive": positive,
            "n_negative": negative,
            "n_dates": days,
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": negative - fp,
            "false_negative": fn,
            "n_alerts": alerts,
            "precision": tp / alerts if alerts else None,
            "recall": tp / positive if positive else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "false_positive_rate": fp / negative if negative else None,
            "false_alerts_per_10000_cell_days": 10000 * fp / count,
            "alerts_per_day": alerts / days if days else None,
            "false_alerts_per_day": fp / days if days else None,
        })
    return result


def _write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def save_curves(y, p, output_dir, *, prefix="model", max_points=1001):
    """Write reliability and display PR/ROC CSVs; scalar metrics remain exact."""
    y, p = _inputs(y, p)
    if not prefix or prefix in (".", "..") or "/" in prefix or "\\" in prefix:
        raise ValueError("prefix must be a single filename component")
    if isinstance(max_points, bool) or not isinstance(max_points, (int, np.integer)) or max_points < 2:
        raise ValueError("max_points must be an integer of at least 2")
    folder = Path(output_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    reliability = _reliability(y, p)
    path = folder / f"{prefix}_reliability.csv"
    _write_csv(path, list(reliability[0]), reliability)
    files = {"reliability": str(path)}
    quantiles = _quantile_reliability(y, p)
    path = folder / f"{prefix}_quantile_reliability.csv"
    _write_csv(path, list(quantiles[0]), quantiles)
    files["quantile_reliability"] = str(path)

    def write_curve(name, x_name, x, y_name, values, thresholds):
        indices = np.unique(np.linspace(0, x.size - 1, min(max_points, x.size), dtype=int))
        path = folder / f"{prefix}_{name}.csv"
        _write_csv(path, [x_name, y_name, "threshold"], (
            {x_name: float(x[i]), y_name: float(values[i]),
             "threshold": float(thresholds[i]) if i < thresholds.size else None}
            for i in indices
        ))
        files[name] = str(path)

    if np.any(y):
        precision, recall, thresholds = precision_recall_curve(y, p, drop_intermediate=True)
        write_curve("precision_recall", "recall", recall, "precision", precision, thresholds)
    if 0 < y.sum() < y.size:
        fpr, tpr, thresholds = roc_curve(y, p, drop_intermediate=True)
        write_curve("roc", "false_positive_rate", fpr, "true_positive_rate", tpr, thresholds)
    return files
