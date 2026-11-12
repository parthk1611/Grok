"""Per-day top-k alert metrics for an already-fitted ignition model (T2.2).

Nothing here fits, tunes or selects anything. It scores vectors that were
produced by a frozen model and answers the operational question: if each local
day we flag the k highest-scoring cells, how many ignitions do we catch?

Conventions (all stated in the output, none hidden):

* ``k = ceil(fraction * n_cells_that_day)``; the absolute k is always reported.
* Ties at the day's cutoff are resolved by *expected value* (the cutoff tie group
  contributes ``(k - n_above) * positives_in_group / group_size``), so results are
  deterministic and tied baselines (a constant rate) get exactly chance recall.
  Expected true positives can therefore be fractional.
* Per-day recall is undefined on days with zero positives. Those days are counted
  and excluded from the mean-of-days recall; pooled recall (sum TP / sum
  positives) includes all positives. Per-day precision is defined every day and
  its mean-of-days uses all days.
* ``lift = precision / prevalence`` with prevalence = positives / cell-days of the
  evaluated split (pooled), so lift is pooled-precision-based or mean-of-days.
* Bootstrap resamples whole local dates (optionally circular blocks of
  ``block_days`` consecutive dates) with replacement; row scores never change.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np

from .evaluation import _binary, _dates, evaluate_predictions

SEED = 385
DEFAULT_FRACTIONS = (0.01, 0.05, 0.10)
DEFAULT_BOOTSTRAPS = 1000


def _k_for(fraction, n):
    if not 0 < fraction <= 1:
        raise ValueError("fractions must be in (0, 1]")
    return min(n, max(1, math.ceil(round(fraction * n, 9))))


def _prepare(y, p, dates):
    y = _binary(y, "y")
    p = np.asarray(p, dtype=np.float64)
    if y.size == 0 or p.shape != y.shape:
        raise ValueError("y and p must be nonempty aligned one-dimensional arrays")
    if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise ValueError("p must contain finite probabilities in [0, 1]")
    days = _dates(dates, y.size)
    unique_days, day_index = np.unique(days, return_inverse=True)
    return y, p, unique_days, day_index


def per_day_topk(y, p, dates, fractions=DEFAULT_FRACTIONS):
    """Per-day counts needed by every top-k summary and by the bootstrap."""
    y, p, days, day_index = _prepare(y, p, dates)
    order = np.argsort(day_index, kind="stable")
    bounds = np.concatenate(([0], np.cumsum(np.bincount(day_index, minlength=days.size))))
    fractions = tuple(float(f) for f in fractions)
    n_cells = np.diff(bounds)
    positives = np.add.reduceat(y[order].astype(np.int64), bounds[:-1])
    k = {f: np.array([_k_for(f, int(n)) for n in n_cells]) for f in fractions}
    tp = {f: np.zeros(days.size) for f in fractions}
    for d in range(days.size):
        rows = order[bounds[d]:bounds[d + 1]]
        scores, labels = p[rows], y[rows]
        for f in fractions:
            kd, n = int(k[f][d]), scores.size
            cutoff = np.partition(scores, n - kd)[n - kd]
            above, tie = scores > cutoff, scores == cutoff
            n_above, group = int(above.sum()), int(tie.sum())
            tp[f][d] = labels[above].sum() + (kd - n_above) * labels[tie].sum() / group
    return {"days": days, "n_cells": n_cells, "positives": positives,
            "fractions": fractions, "k": k, "tp": tp}


def _summaries(day, fraction, weights):
    """Weighted summaries; ``weights`` is (B, n_days) date multiplicities."""
    w = np.atleast_2d(np.asarray(weights, dtype=np.float64))
    n, pos = day["n_cells"].astype(float), day["positives"].astype(float)
    k, tp = day["k"][fraction].astype(float), day["tp"][fraction]
    has_pos = pos > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        recall_day = np.where(has_pos, tp / np.where(has_pos, pos, 1), 0.0)
        total_pos, total_pos_days = w @ pos, w @ has_pos.astype(float)
        prevalence = total_pos / (w @ n)
        pooled_recall = (w @ tp) / total_pos
        mean_recall = (w @ recall_day) / total_pos_days
        pooled_precision = (w @ tp) / (w @ k)
        mean_precision = (w @ (tp / k)) / w.sum(axis=1)
        return {"pooled_recall": pooled_recall, "mean_day_recall": mean_recall,
                "pooled_precision": pooled_precision,
                "mean_day_precision": mean_precision,
                "pooled_lift": pooled_precision / prevalence,
                "mean_day_lift": mean_precision / prevalence,
                "prevalence": prevalence}


def _scalar(value):
    value = float(value)
    return value if math.isfinite(value) else None


def topk_table(day, weights=None):
    """Point-estimate table, one record per fraction."""
    weights = np.ones((1, day["days"].size)) if weights is None else weights
    rows = []
    for f in day["fractions"]:
        s = _summaries(day, f, weights)
        k = day["k"][f]
        rows.append({
            "fraction": f, "k_cells_min": int(k.min()), "k_cells_max": int(k.max()),
            "n_days": int(day["days"].size),
            "days_with_positives": int((day["positives"] > 0).sum()),
            "days_zero_positives_excluded_from_mean_recall": int((day["positives"] == 0).sum()),
            "positives": int(day["positives"].sum()),
            "expected_true_positives": float(day["tp"][f].sum()),
            **{key: _scalar(val[0]) for key, val in s.items()},
        })
    return rows


def bootstrap_counts(n_days, n_boot=DEFAULT_BOOTSTRAPS, seed=SEED, block_days=1):
    """(n_boot, n_days) integer multiplicities of resampled dates.

    ``block_days=1`` resamples individual dates. Larger values draw circular
    blocks of consecutive dates to respect serial correlation. Draws depend only
    on (n_days, n_boot, seed, block_days), so every model sees the same resamples.
    """
    if n_days < 1 or n_boot < 1 or block_days < 1:
        raise ValueError("n_days, n_boot and block_days must be positive")
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n_days / block_days)
    starts = rng.integers(0, n_days, size=(n_boot, n_blocks))
    picks = (starts[:, :, None] + np.arange(block_days)[None, None, :]) % n_days
    picks = picks.reshape(n_boot, -1)[:, :n_days]
    counts = np.zeros((n_boot, n_days), dtype=np.int32)
    np.add.at(counts, (np.arange(n_boot)[:, None], picks), 1)
    return counts


def weighted_ap_function(y, p, dates):
    """Return ``ap(counts)``: AP with each date's rows repeated ``counts[d]`` times.

    Exactly equals ``average_precision_score`` on the repeated rows (step-wise sum
    over distinct score thresholds), but sorts only once.
    """
    y, p, days, day_index = _prepare(y, p, dates)
    order = np.argsort(-p, kind="stable")
    scores, labels, owner = p[order], y[order].astype(np.float64), day_index[order]
    ends = np.flatnonzero(np.concatenate((scores[1:] != scores[:-1], [True])))

    def ap(counts):
        w = np.asarray(counts, dtype=np.float64)[owner]
        tp_cum, all_cum = np.cumsum(w * labels)[ends], np.cumsum(w)[ends]
        total = tp_cum[-1]
        if total <= 0:
            return None
        recall = tp_cum / total
        previous = np.concatenate(([0.0], recall[:-1]))
        # Leading score groups can have zero total weight (their dates were not
        # drawn); they add no recall, so their precision is irrelevant, not NaN.
        precision = np.divide(tp_cum, all_cum, out=np.zeros_like(tp_cum), where=all_cum > 0)
        return float(np.sum((recall - previous) * precision))

    return ap, int(days.size)


def _interval(values, point):
    values = np.asarray([v for v in values if v is not None and math.isfinite(v)])
    if not values.size:
        return {"point": point, "ci95_low": None, "ci95_high": None, "n_valid": 0}
    low, high = np.percentile(values, [2.5, 97.5])
    return {"point": point, "ci95_low": float(low), "ci95_high": float(high),
            "n_valid": int(values.size)}


def bootstrap_report(y, p, dates, day, counts):
    """95% percentile intervals for recall/lift at each k and for AP."""
    out = {"n_resamples": int(counts.shape[0]), "topk": {}}
    for f in day["fractions"]:
        point = _summaries(day, f, np.ones((1, counts.shape[1])))
        boot = _summaries(day, f, counts)
        out["topk"][f"{f:g}"] = {
            key: _interval(boot[key], _scalar(point[key][0]))
            for key in ("pooled_recall", "mean_day_recall", "pooled_precision",
                        "pooled_lift", "mean_day_lift")}
    ap, n_days = weighted_ap_function(y, p, dates)
    out["average_precision"] = _interval(
        [ap(c) for c in counts], ap(np.ones(n_days)))
    return out


def climatology_scores(train_cell_ids, train_y, eval_cell_ids, n_cells):
    """Per-cell training event rate (mean label over the supplied training rows).

    Only training rows are used; evaluation labels never enter. Cells absent from
    training get rate 0.
    """
    train_cell_ids = np.asarray(train_cell_ids, dtype=np.int64)
    train_y = _binary(train_y, "train_y", train_cell_ids.size)
    eval_cell_ids = np.asarray(eval_cell_ids, dtype=np.int64)
    if train_cell_ids.min(initial=0) < 0 or eval_cell_ids.min(initial=0) < 0 \
            or max(train_cell_ids.max(initial=0), eval_cell_ids.max(initial=0)) >= n_cells:
        raise ValueError("cell ids must lie in [0, n_cells)")
    count = np.bincount(train_cell_ids, minlength=n_cells)
    positive = np.bincount(train_cell_ids, weights=train_y, minlength=n_cells)
    rate = np.divide(positive, count, out=np.zeros(n_cells), where=count > 0)
    return rate[eval_cell_ids]


def constant_rate_scores(train_y, size):
    """The run.py 'training rate' baseline: one constant probability."""
    return np.full(size, float(np.mean(_binary(train_y, "train_y"))))


def cutoffs_from_fit_split(p_fit, fractions=DEFAULT_FRACTIONS):
    """Absolute probability cutoffs at the (1 - fraction) quantiles of ONE split.

    Same linear-interpolation quantile as ``run.py`` uses for frozen thresholds.
    Call this with the threshold-split (2018) scores only.
    """
    p_fit = np.asarray(p_fit, dtype=np.float64)
    return {float(f): float(np.quantile(p_fit, 1 - f)) for f in fractions}


def cutoff_rule_metrics(y, p, dates, cutoff):
    """Metrics of the rule ``alert if p >= cutoff`` on a split (cutoff fixed)."""
    y, p, days, day_index = _prepare(y, p, dates)
    alert = p >= cutoff
    per_day_alerts = np.bincount(day_index, weights=alert, minlength=days.size)
    tp, alerts, positives = int((alert & (y == 1)).sum()), int(alert.sum()), int(y.sum())
    prevalence = positives / y.size
    precision = tp / alerts if alerts else None
    return {
        "cutoff": float(cutoff), "n_alerts": alerts, "true_positive": tp,
        "positives": positives, "recall": tp / positives if positives else None,
        "precision": precision,
        "lift": precision / prevalence if precision is not None and prevalence else None,
        "alerts_per_day_mean": alerts / days.size,
        "alerts_per_day_median": float(np.median(per_day_alerts)),
        "alerts_per_day_p95": float(np.percentile(per_day_alerts, 95)),
        "alerts_per_day_max": int(per_day_alerts.max()),
        "days_with_zero_alerts": int((per_day_alerts == 0).sum()),
    }


def alert_options(y_fit, p_fit, dates_fit, fractions=DEFAULT_FRACTIONS, others=None):
    """Alert-rule options chosen from the fit split only.

    ``others`` maps split names to (y, p, dates); each is scored with the SAME
    rules (budgets recomputed per day, cutoffs frozen from the fit split) as a
    read-only check. Nothing is selected or re-tuned from ``others``.
    """
    cutoffs = cutoffs_from_fit_split(p_fit, fractions)
    splits = {"fit_split": (y_fit, p_fit, dates_fit), **(others or {})}
    result = {"cutoffs_from_fit_split": cutoffs, "fractions": [float(f) for f in fractions],
              "budget_rule": {}, "cutoff_rule": {}}
    for name, (y, p, dates) in splits.items():
        day = per_day_topk(y, p, dates, fractions)
        result["budget_rule"][name] = [
            {key: row[key] for key in (
                "fraction", "k_cells_min", "k_cells_max", "pooled_recall",
                "mean_day_recall", "pooled_precision", "pooled_lift")}
            | {"alerts_per_day": float(day["k"][row["fraction"]].mean())}
            for row in topk_table(day)]
        result["cutoff_rule"][name] = [
            {"fraction_of_fit_split": f, **cutoff_rule_metrics(y, p, dates, c)}
            for f, c in cutoffs.items()]
    return result


def system_report(y, p, dates, fractions=DEFAULT_FRACTIONS, counts=None, with_reliability=True):
    """Full report for one score vector on one split."""
    day = per_day_topk(y, p, dates, fractions)
    metrics = evaluate_predictions(y, p, dates=dates)
    report = {"scalar_metrics": {k: metrics[k] for k in (
        "n_samples", "n_positive", "prevalence", "average_precision",
        "ap_prevalence_ratio", "roc_auc", "brier_score", "log_loss")},
        "topk": topk_table(day)}
    if with_reliability:
        report["reliability_bins"] = metrics["reliability_bins"]
        report["quantile_reliability_bins"] = metrics["quantile_reliability_bins"]
    if counts is not None:
        report["bootstrap"] = bootstrap_report(y, p, dates, day, counts)
    return report


# ----------------------------------------------------------------- reporting

def _fmt(value, digits=6):
    return "n/a" if value is None else f"{value:.{digits}g}"


def markdown_tables(split, reports):
    """Compact markdown from ``system_report`` outputs ({system: report})."""
    lines = [f"### {split}", ""]
    first = next(iter(reports.values()))
    meta = first["topk"][0]
    lines += [f"{meta['n_days']} days; {meta['positives']} positives; "
              f"{meta['days_zero_positives_excluded_from_mean_recall']} days with zero "
              "positives (excluded from mean-of-days recall).", "",
              "| system | AP | ROC-AUC | Brier | log loss |", "|---|---|---|---|---|"]
    for name, rep in reports.items():
        m = rep["scalar_metrics"]
        lines.append(f"| {name} | {_fmt(m['average_precision'])} | {_fmt(m['roc_auc'])} "
                     f"| {_fmt(m['brier_score'])} | {_fmt(m['log_loss'])} |")
    lines += ["", "| system | top-k | k cells/day | pooled recall | mean-of-days recall | "
              "pooled precision | mean-of-days precision | pooled lift | mean-of-days lift |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, rep in reports.items():
        for row in rep["topk"]:
            k = row["k_cells_min"] if row["k_cells_min"] == row["k_cells_max"] \
                else f"{row['k_cells_min']}-{row['k_cells_max']}"
            lines.append(
                f"| {name} | {row['fraction']:.0%} | {k} | {_fmt(row['pooled_recall'])} "
                f"| {_fmt(row['mean_day_recall'])} | {_fmt(row['pooled_precision'])} "
                f"| {_fmt(row['mean_day_precision'])} | {_fmt(row['pooled_lift'])} "
                f"| {_fmt(row['mean_day_lift'])} |")
    if "bootstrap" in first:
        lines += ["", "95% bootstrap intervals (percentile; resampled dates):", "",
                  "| system | metric | point | 2.5% | 97.5% |", "|---|---|---|---|---|"]
        for name, rep in reports.items():
            b = rep["bootstrap"]
            ap = b["average_precision"]
            lines.append(f"| {name} | AP | {_fmt(ap['point'])} | {_fmt(ap['ci95_low'])} "
                         f"| {_fmt(ap['ci95_high'])} |")
            for fraction, metrics in b["topk"].items():
                for key in ("pooled_recall", "mean_day_recall", "pooled_lift"):
                    iv = metrics[key]
                    lines.append(f"| {name} | {key} @ top {float(fraction):.0%} "
                                 f"| {_fmt(iv['point'])} | {_fmt(iv['ci95_low'])} "
                                 f"| {_fmt(iv['ci95_high'])} |")
    if "reliability_bins" in first:
        lines += ["", "Reliability bins (frozen model, 10 equal-width probability bins):", "",
                  "| bin | n | positives | mean p | observed |", "|---|---|---|---|---|"]
        for b in first["reliability_bins"]:
            lines.append(f"| [{b['lower']:.1f}, {b['upper']:.1f}) | {b['n_samples']} "
                         f"| {b['n_positive']} | {_fmt(b['mean_probability'])} "
                         f"| {_fmt(b['observed_prevalence'])} |")
    return "\n".join(lines) + "\n"


def write_csv(path, rows):
    rows = list(rows)
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value))


def main(argv=None):
    """Score a frozen, already-fitted model; never trains. Needs the local cache."""
    from .contract import ROOT
    from .data import dates_for, load_cache, year_slice
    from .run import predict_bundle, predict_chunks
    import joblib

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=ROOT / "data/interim/ignition_modeling_cache")
    parser.add_argument("--model", type=Path, required=True,
                        help="frozen joblib: fitted pipeline or selected_system bundle")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAPS)
    parser.add_argument("--block-days", type=int, default=1)
    parser.add_argument("--fractions", type=float, nargs="+", default=list(DEFAULT_FRACTIONS))
    parser.add_argument("--train-years", type=int, nargs=2, default=[2010, 2016])
    args = parser.parse_args(argv)
    if (args.output / "final_test.json").exists():
        raise SystemExit("Refusing to write into a completed final-test directory")
    args.output.mkdir(parents=True, exist_ok=True)

    X, y, manifest = load_cache(args.cache)
    n_rows = manifest["rows"]
    ids = np.memmap(args.cache / "grid.u16", dtype="uint16", mode="r", shape=(n_rows,))
    n_cells = int(ids.max()) + 1
    model = joblib.load(args.model)
    predict = (lambda rows: predict_bundle(model, rows)) if isinstance(model, dict) \
        else (lambda rows: predict_chunks(model, rows))
    train = year_slice(manifest, *args.train_years)
    train_y, train_ids = np.asarray(y[train]), np.asarray(ids[train])
    fractions = tuple(args.fractions)

    scored = {}
    for split, year in (("2018_threshold_split", 2018), ("2019_test_report_only", 2019)):
        rows = year_slice(manifest, year)
        scored[split] = (np.asarray(y[rows]), predict(X[rows]), dates_for(manifest, rows),
                         np.asarray(ids[rows]))
    summary = {"seed": SEED, "bootstrap_resamples": args.bootstrap, "block_days": args.block_days,
               "train_years": args.train_years, "fractions": list(fractions),
               "model": str(args.model), "splits": {}}
    markdown = []
    for split, (ys, ps, ds, cs) in scored.items():
        counts = bootstrap_counts(np.unique(ds).size, args.bootstrap, SEED, args.block_days)
        systems = {
            "frozen_hgb": ps,
            f"climatology_{args.train_years[0]}_{args.train_years[1]}":
                climatology_scores(train_ids, train_y, cs, n_cells),
            f"rate_baseline_{args.train_years[0]}_{args.train_years[1]}":
                constant_rate_scores(train_y, ys.size)}
        reports = {name: system_report(ys, s, ds, fractions, counts, with_reliability=(name == "frozen_hgb"))
                   for name, s in systems.items()}
        summary["splits"][split] = reports
        markdown.append(markdown_tables(split, reports))
        write_csv(args.output / f"topk_{split}.csv",
                  [{"system": n, **row} for n, r in reports.items() for row in r["topk"]])
    fit_y, fit_p, fit_d, _ = scored["2018_threshold_split"]
    test_y, test_p, test_d, _ = scored["2019_test_report_only"]
    summary["alert_options"] = alert_options(
        fit_y, fit_p, fit_d, fractions, others={"2019_read_only": (test_y, test_p, test_d)})
    (args.output / "topk_summary.json").write_text(
        json.dumps(summary, indent=2, default=_json_default, allow_nan=False) + "\n")
    (args.output / "topk_tables.md").write_text("\n".join(markdown))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
