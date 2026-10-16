"""Independent, small-fixture acceptance checks for ignition modelling."""
import bz2
import csv
import json
import math
import sqlite3
import tempfile
import unittest
from collections import deque
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from importlib.metadata import version

import numpy as np
import pandas as pd
import joblib
from threadpoolctl import threadpool_limits

from scripts.build_ignition_feature_panel import FireHistory, GridCell
from scripts.ignition_modeling import contract, data, evaluation, splits, weather
from scripts.ignition_modeling import histogram, logistic, run
from scripts.ignition_modeling.preprocessing import HistoryIndicator, NumericPreprocessor


EXPECTED_FEATURES = (
    "calendar_day_of_year", "calendar_doy_sin", "calendar_doy_cos",
    "calendar_is_weekend", "calendar_day_length_hours", "terrain_elevation_mean_m",
    "terrain_slope_deg", "terrain_aspect_sin", "terrain_aspect_cos",
    "terrain_ruggedness_m", "terrain_sample_count", "terrain_coverage_fraction",
    "cwfis_station_count", "cwfis_nearest_station_km",
    "cwfis_observation_coverage_fraction", "cwfis_imputed_station_fraction",
    "cwfis_calcstatus_nonzero_fraction", "cwfis_missing", "cwfis_temp_c",
    "cwfis_rh_pct", "cwfis_wind_speed_kmh", "cwfis_precip_mm", "cwfis_ffmc",
    "cwfis_dmc", "cwfis_dc", "cwfis_isi", "cwfis_bui", "cwfis_fwi",
    "cwfis_dsr", "cwfis_precip_7d_mm", "cwfis_precip_7d_coverage_days",
    "fire_history_same_cell_30d", "fire_history_neighborhood_30d",
    "fire_history_same_cell_365d", "fire_history_neighborhood_365d",
    "fire_history_days_since_same_cell",
)


def one_cell_grid():
    return pd.DataFrame({"grid_id": ["a"], "centroid_longitude": [-85.0],
                         "centroid_latitude": [48.0]})


def write_archive(root, rows):
    archive = root / "archive.csv.bz2"
    fields = ("rep_date", "aes", "wmo", "lon", "lat", "opts", "calcstatus",
              "temp", "rh", "ws", "precip", "ffmc", "dmc", "dc", "isi",
              "bui", "fwi", "dsr")
    defaults = dict.fromkeys(fields, "1")
    defaults.update(lon="-85", lat="48", opts="", calcstatus="0", rh="50")
    with bz2.open(archive, "wt", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(defaults | row for row in rows)
    manifest = root / "data/raw/cwfis/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"archive_sha256": weather.sha256(archive)}))
    return archive


def write_weather_db(path, rows):
    fields = ("temp", "rh", "ws", "precip", "ffmc", "dmc", "dc", "isi",
              "bui", "fwi", "dsr", "imputed", "calcstatus")
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE records(day TEXT,key TEXT,lon REAL,lat REAL," +
                   ",".join(f"{name} REAL" for name in fields) + ")")
        for row in rows:
            defaults = {"key": "station", "lon": -85.0, "lat": 48.0,
                        **dict.fromkeys(fields, 1.0), "imputed": 0, "calcstatus": 0}
            value = defaults | row
            columns = ("day", "key", "lon", "lat", *fields)
            db.execute("INSERT INTO records VALUES(" + ",".join("?" for _ in columns) + ")",
                       tuple(value[name] for name in columns))
        db.commit()


def audit_corrected_cache(directory, quality_path):
    """Full numeric-cache gate; never rescans the downloaded CSV or fits a model."""
    directory = Path(directory)
    X, y, manifest = data.load_cache(directory)
    original = json.loads(Path(quality_path).read_text())
    n, grid_count = len(y), original["totals"]["grid_cells"]
    ids = np.memmap(directory / "grid.u16", dtype="uint16", mode="r", shape=(n,))

    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    require(original["status"] == "PASS" and original["full_scan_completed"], "Original integrity gate failed")
    require(manifest["features"] == list(EXPECTED_FEATURES), "Exact 36-column order differs")
    require(n == original["totals"]["rows"] == 17798320, "Row universe differs")
    require(grid_count == 9673, "Grid universe differs")
    for filename, width in (("X.f64", 288), ("y.u8", 1), ("grid.u16", 2)):
        require((directory / filename).stat().st_size == n * width, f"Binary storage size differs: {filename}")
    require(manifest["original_panel_sha256"] == original["source"]["sha256"], "Original checksum differs")
    expected_dates = [(date(year, 5, 1) + timedelta(days=offset)).isoformat()
                      for year in range(2010, 2020) for offset in range(184)]
    require(list(manifest["date_slices"]) == expected_dates, "Exact chronological date universe differs")
    column = {name: i for i, name in enumerate(EXPECTED_FEATURES)}
    daily = ["cwfis_temp_c", "cwfis_rh_pct", "cwfis_wind_speed_kmh", "cwfis_precip_mm",
             "cwfis_ffmc", "cwfis_dmc", "cwfis_dc", "cwfis_isi", "cwfis_bui", "cwfis_fwi", "cwfis_dsr"]
    quality = ["cwfis_observation_coverage_fraction", "cwfis_imputed_station_fraction",
               "cwfis_calcstatus_nonzero_fraction"]
    checks = 0
    for start in range(0, n, 50000):
        block, labels = X[start:start + 50000], y[start:start + 50000]
        require(not np.isinf(block).any(), f"Infinite numeric cache input near row {start}")
        require(np.isin(labels, [0, 1]).all(), f"Nonbinary labels near row {start}")
        for name in daily[1:]:
            values = block[:, column[name]]
            require(not np.any(values < 0), f"Negative physical weather {name} near row {start}")
            if name in ("cwfis_rh_pct", "cwfis_ffmc"):
                require(not np.any(values > (100 if name == "cwfis_rh_pct" else 101)), f"Weather upper bound {name}")
        count, missing = block[:, column["cwfis_station_count"]], block[:, column["cwfis_missing"]]
        require(np.isfinite(count).all() and np.isin(count, [0, 1, 2, 3, 4]).all(), "Station count invalid")
        require(np.array_equal(missing, count == 0), "Missing flag disagrees with station count")
        for name in quality:
            values = block[:, column[name]]
            require(not np.any((values < 0) | (values > 1)), f"Quality fraction outside [0,1]: {name}")
        require(np.isfinite(block[:, column[quality[0]]]).all(), "Observation coverage missing")
        for name in [*daily, "cwfis_nearest_station_km", *quality[1:]]:
            require(np.isnan(block[missing == 1, column[name]]).all(), f"Unavailable day has observed {name}")
        distance = block[:, column["cwfis_nearest_station_km"]]
        require(np.isfinite(distance[missing == 0]).all() and not np.any(distance < 0), "Nearest usable distance invalid")
        require(np.isfinite(block[missing == 0, column["cwfis_fwi"]]).all(), "Usable day has missing FWI")
        coverage, rain = block[:, column["cwfis_precip_7d_coverage_days"]], block[:, column["cwfis_precip_7d_mm"]]
        require(np.isin(coverage, np.arange(8)).all(), "Rain coverage invalid")
        require(np.array_equal(np.isnan(rain), coverage < 7), "Rain null/coverage mismatch")
        require(not np.any(rain < 0), "Negative seven-day rain")
        checks += len(labels)
    annual, rolling, end_previous = {}, deque(maxlen=7), 0
    for day, (start, end) in manifest["date_slices"].items():
        require(start == end_previous and end - start == grid_count, "Date slices not complete/contiguous")
        grid_ids = np.asarray(ids[start:end])
        require(np.array_equal(np.sort(grid_ids), np.arange(grid_count)), f"Grid ID permutation invalid on {day}")
        values = X[start:end]
        row = annual.setdefault(day[:4], {"rows": 0, "positives": 0, "weather_missing": 0, "rain_7d_missing": 0})
        row["rows"] += end - start
        row["positives"] += int(y[start:end].sum())
        row["weather_missing"] += int(values[:, column["cwfis_missing"]].sum())
        row["rain_7d_missing"] += int(np.isnan(values[:, column["cwfis_precip_7d_mm"]]).sum())
        if day[5:] == "05-01":
            rolling.clear()
        rain_by_grid = np.empty(grid_count)
        rain_by_grid[grid_ids] = values[:, column["cwfis_precip_mm"]]
        rolling.append(rain_by_grid)
        if len(rolling) == 7:
            history = np.asarray(rolling)
            expected_coverage = np.isfinite(history).sum(axis=0)
            expected_rain = np.where(expected_coverage == 7, np.nansum(history, axis=0), np.nan)
            require(np.array_equal(values[:, column["cwfis_precip_7d_coverage_days"]], expected_coverage[grid_ids]), f"Independent rolling coverage mismatch {day}")
            require(np.allclose(values[:, column["cwfis_precip_7d_mm"]], expected_rain[grid_ids], atol=1e-6, rtol=0, equal_nan=True), f"Independent rolling rain mismatch {day}")
        end_previous = end
    require(end_previous == checks == n, "Cache scan incomplete")
    require(annual == manifest["annual"], "Annual cache counts disagree with manifest")
    for row in original["annual"]:
        result = annual[str(row["year"])]
        require(result["rows"] == row["rows"] and result["positives"] == row["positive_cell_days"], "Annual labels/rows changed")
    require(sum(row["positives"] for row in annual.values()) == 7785, "Total labels changed")
    representative = []
    grid = pd.read_csv(contract.ROOT / "data/interim/ontario_fire_region_10km_grid.csv")
    with closing(sqlite3.connect(directory / "cwfis.sqlite")) as db:
        stations = db.execute("SELECT key,lon,lat,MIN(day) FROM records GROUP BY key ORDER BY MIN(day),key").fetchall()
        for issue in ("2010-05-01", "2015-05-01", "2016-07-15", "2019-10-31"):
            observed = (date.fromisoformat(issue) - timedelta(days=1)).isoformat()
            records = {row[0]: row[1:] for row in db.execute("SELECT key,temp,rh,ws,precip,ffmc,dmc,dc,isi,bui,fwi,dsr,imputed,calcstatus FROM records WHERE day=?", (observed,))}
            for grid_index in (0, grid_count // 2, grid_count - 1):
                cell = grid.iloc[grid_index]
                ranked = []
                for order, (key, lon, lat, first) in enumerate(stations):
                    if first > observed:
                        continue
                    phi1, phi2 = math.radians(cell.centroid_latitude), math.radians(lat)
                    delta_phi = phi2 - phi1
                    delta_lon = math.radians(lon - cell.centroid_longitude)
                    hav = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lon / 2)**2
                    distance = 12742.0176 * math.asin(math.sqrt(min(1, max(0, hav))))
                    ranked.append((distance, order, key))
                selected = sorted(ranked)[:4]
                usable = [(distance, records[key]) for distance, _, key in selected
                          if key in records and records[key][9] is not None]
                start, end = manifest["date_slices"][issue]
                location = start + int(np.flatnonzero(ids[start:end] == grid_index)[0])
                require(X[location, column["cwfis_station_count"]] == len(usable), "Independent station count differs")
                require(abs(X[location, column[quality[0]]] - len(usable) / len(selected)) <= 1e-6, "Independent station coverage differs")
                for j, name in enumerate(daily):
                    included = [(distance, record[j]) for distance, record in usable if record[j] is not None]
                    expected = (sum(value / max(distance, 1)**2 for distance, value in included) /
                                sum(1 / max(distance, 1)**2 for distance, _ in included)) if included else np.nan
                    require(np.isclose(X[location, column[name]], expected, atol=1e-6, rtol=0, equal_nan=True), f"Independent source join differs for {issue}/{grid_index}/{name}")
                representative.append({"issue_date": issue, "grid_id": cell.grid_id,
                                       "usable_stations": len(usable), "nearest_eligible_keys": [key for _, _, key in selected]})
    return {"status": "PASS", "rows_scanned": checks, "dates": len(expected_dates),
            "grid_cells": grid_count, "positives": 7785, "annual": annual,
            "representative_source_checks": representative,
            "rain_sum_dates_independently_checked": 1780,
            "source_sha256": manifest["original_panel_sha256"], "cache_version": manifest["cache_version"]}


def independent_metrics(labels, probability):
    """Ranking metrics from tie-grouped cumulative counts, independent of evaluator."""
    labels, probability = np.asarray(labels, dtype=np.float64), np.asarray(probability)
    if not np.isfinite(probability).all() or np.any((probability < 0) | (probability > 1)):
        raise ValueError("Invalid persisted probabilities")
    order = np.argsort(-probability, kind="stable")
    scores, target = probability[order], labels[order]
    ends = np.r_[np.flatnonzero(scores[1:] != scores[:-1]), len(scores) - 1]
    tp = np.cumsum(target, dtype=np.int64)[ends]
    positives, negatives = int(target.sum()), len(target) - int(target.sum())
    recall = tp / positives if positives else np.zeros(len(tp))
    clipped = np.clip(probability, np.finfo(np.float64).eps, 1 - np.finfo(np.float64).eps)
    return {"n_samples": len(labels), "n_positive": positives, "n_negative": negatives,
            "prevalence": positives / len(labels),
            "average_precision": float(np.sum(np.diff(np.r_[0, recall]) * tp / (ends + 1))) if positives else None,
            "roc_auc": float(np.trapezoid(np.r_[0, recall], np.r_[0, (ends + 1 - tp) / negatives])) if positives and negatives else None,
            "brier_score": float(np.mean((probability - labels)**2)),
            "log_loss": float(np.mean(-labels * np.log(clipped) - (1 - labels) * np.log1p(-clipped)))}


def audit_production_artifacts(directory, cache_directory):
    """Read-only production acceptance; never refits, chooses or retunes models."""
    directory = Path(directory)
    X, y, manifest = data.load_cache(cache_directory)
    config = json.loads((directory / "configuration.json").read_text())
    compare = json.loads((directory / "development_comparison.json").read_text())
    freeze = json.loads((directory / "final_freeze.json").read_text())

    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    def match_metrics(actual, reported):
        for name, value in actual.items():
            require(reported[name] is None if value is None else np.isclose(reported[name], value, atol=1e-12, rtol=0), f"Metric differs: {name}")

    require(config["features"] == list(EXPECTED_FEATURES) and config["seed"] == 385, "Run schema/seed differs")
    require(config["cache_manifest_sha256"] == weather.sha256(Path(cache_directory) / "manifest.json"), "Cache fingerprint differs")
    for name, digest in config["source_code_sha256"].items():
        require(weather.sha256(contract.ROOT / "scripts/ignition_modeling" / name) == digest, f"Code fingerprint differs: {name}")
    for package, recorded in config["packages"].items():
        require(version(package) == recorded, f"Package version differs: {package}")
    paths = sorted(directory.glob("fold_*.json"))
    require(len(paths) == 40 and len(compare["fold_results"]) == 40, "Development checkpoints incomplete")
    records = {}
    for path in paths:
        record = json.loads(path.read_text())
        family, candidate, year = record["family"], record["candidate"], record["fold"]
        require(year in range(2012, 2017) and family in ("logistic", "hgb") and candidate in range(4), "Unexpected fold identity")
        require(record["params"] == config[f"{'logistic' if family == 'logistic' else 'hgb'}_candidates"][candidate], "Candidate parameters differ")
        valid, train = data.year_slice(manifest, year), data.year_slice(manifest, 2010, year - 1)
        with np.load(path.with_suffix(".npz")) as predictions:
            labels, probability = predictions["y"], predictions["p"]
            require(np.array_equal(labels, y[valid]), "Validation labels/order or prevalence changed")
            require(probability.shape == labels.shape, "Validation prediction schema differs")
            metrics = independent_metrics(labels, probability)
            match_metrics(metrics, record["metrics"])
        diag = record["diagnostics"]
        total, positive = train.stop - train.start, int(y[train].sum())
        require(diag.get("training_rows", diag.get("n_rows")) == total, "Training row count differs")
        require(diag["class_counts"] == {"0": total - positive, "1": positive}, "Training class counts differ")
        expected_weights = ({"0": total / (2 * (total - positive)), "1": total / (2 * positive)}
                            if record["params"]["class_weight"] == "balanced" else {"0": 1.0, "1": 1.0})
        require(diag.get("training_class_weights", diag.get("effective_class_weights")) == expected_weights, "Fold-derived weights differ")
        if family == "hgb":
            require(diag["raw_feature_schema"] == list(EXPECTED_FEATURES) and diag["n_features_transformed"] == 37, "Histogram schema differs")
            require(not diag["early_stopping"] and not diag["internal_random_validation"], "Random validation used")
            require(diag["n_iter"] == record["params"]["max_iter"], "Histogram iteration count differs")
        else:
            require(diag["input_features"] == list(EXPECTED_FEATURES) and len(diag["transformed_features"]) == 57, "Logistic schema differs")
            require(not diag["resampling"] and diag["estimator_class_weight"] is None, "Logistic weighting applied twice")
            require(all(epoch["rows_seen"] == total and epoch["positives_seen"] == positive for epoch in diag["epochs"]), "Logistic epoch lost rows")
        key = (family, candidate, year)
        require(key not in records, "Duplicate fold identity")
        records[key] = record
    for record in compare["fold_results"]:
        require(record == records[(record["family"], record["candidate"], record["fold"])], "Comparison differs from saved checkpoint")
    for summary in compare["candidate_summary"]:
        values = {str(year): records[(summary["family"], summary["candidate"], year)]["metrics"]["average_precision"] for year in range(2012, 2017)}
        require(summary["fold_average_precision"] == values, "Candidate fold summary differs")
        require(abs(summary["mean_fold_average_precision"] - np.mean(list(values.values()))) < 1e-12, "Mean AP differs")
    selected = max(compare["candidate_summary"], key=lambda row: row["mean_fold_average_precision"])
    require(selected == compare["selected"] == freeze["selected"], "Final choice not development winner")
    require(freeze["system_sha256"] == weather.sha256(directory / "selected_system.joblib"), "Frozen model hash differs")
    require(freeze["configuration_sha256"] == weather.sha256(directory / "configuration.json"), "Frozen config hash differs")
    bundle = joblib.load(directory / "selected_system.joblib")
    require(bundle["features"] == list(EXPECTED_FEATURES) and bundle["cache_version"] == manifest["cache_version"], "Saved system schema differs")
    require(bundle["calibrator"] is None and not freeze["calibration_used"], "Unexpected final calibration")
    require(all(bundle["pipeline"]["classifier"].get_params()[name] == value for name, value in selected["params"].items()), "Saved selected classifier parameters differ")
    final_train = data.year_slice(manifest, 2010, 2016)
    for family, count in (("logistic", 57), ("hgb", 37)):
        pipeline = joblib.load(directory / f"final_{family}.joblib")
        require(list(pipeline["preprocessor"].feature_names_in_) == list(EXPECTED_FEATURES), "Final raw pipeline schema differs")
        require(len(pipeline["preprocessor"].output_names_) == count, "Final derived schema differs")
        require(pipeline["preprocessor"].n_samples_seen_ == final_train.stop - final_train.start, "Final preprocessor fit period differs")
        chosen = max((row for row in compare["candidate_summary"] if row["family"] == family), key=lambda row: row["mean_fold_average_precision"])
        diag = json.loads((directory / f"final_{family}_training.json").read_text())
        total, positive = final_train.stop - final_train.start, int(y[final_train].sum())
        require(diag["params"] == chosen["params"] and diag.get("training_rows", diag.get("n_rows")) == total, "Final training configuration/counts differ")
        require(diag["class_counts"] == {"0": total - positive, "1": positive}, "Final training class counts differ")
    threshold_slice = data.year_slice(manifest, 2018)
    with threadpool_limits(limits=2):
        p2018 = run.predict_bundle(bundle, X[threshold_slice])
    expected_thresholds = np.unique(np.quantile(p2018, [.5, .9, .95, .99, .995, .999, .9995, .9999]))
    require(np.array_equal(expected_thresholds, freeze["thresholds"]) and freeze["thresholds"] == bundle["thresholds"], "Thresholds do not originate from frozen 2018 scores")
    decision = json.loads((directory / "calibration_decision.json").read_text())
    match_metrics(independent_metrics(y[threshold_slice], p2018), decision["raw"])
    require(decision["fit_year"] == 2017 and decision["decision_year"] == 2018 and not decision["used"], "Calibration selection timing differs")
    require(decision["sigmoid"]["log_loss"] >= decision["raw"]["log_loss"], "Recorded calibration rejection rule disagrees")
    test = data.year_slice(manifest, 2019)
    report = json.loads((directory / "final_test.json").read_text())
    with np.load(directory / "test_predictions_2019.npz") as saved:
        labels, probability = saved["y"], saved["p"]
        require(np.array_equal(labels, y[test]), "Test rows/order/prevalence changed")
        require(np.array_equal(saved["dates"], data.dates_for(manifest, test)), "Test dates differ")
        missing = saved["weather_missing"]
        require(np.array_equal(missing, X[test, EXPECTED_FEATURES.index("cwfis_missing")]), "Test weather strata differ")
        metrics = independent_metrics(labels, probability)
        match_metrics(metrics, report)
        for flag in (0, 1):
            group = missing == flag
            match_metrics(independent_metrics(labels[group], probability[group]), report["by_weather_missing"][str(flag)])
        positions = np.unique(np.linspace(0, len(labels) - 1, 512, dtype=int))
        with threadpool_limits(limits=2):
            predicted = run.predict_bundle(bundle, X[test][positions])
        require(np.allclose(predicted, probability[positions], atol=1e-14, rtol=0), "Reloaded final test subset differs")
        for row, threshold in zip(report["threshold_tradeoffs"], expected_thresholds):
            alerted = probability >= threshold
            tp, fp = int(labels[alerted].sum()), int(alerted.sum() - labels[alerted].sum())
            require(row["threshold"] == threshold and row["true_positive"] == tp and row["false_positive"] == fp, "Frozen test threshold counts differ")
            require(np.isclose(row["false_alerts_per_10000_cell_days"], 10000 * fp / len(labels)), "False-alert denominator differs")
    require(report["final_system_freeze"] == freeze and report["n_dates"] == 184, "Final report freeze/date count differs")
    times = [(directory / name).stat().st_mtime_ns for name in ("selected_system.joblib", "final_freeze.json", "test_predictions_2019.npz", "final_test.json")]
    require(times == sorted(times), "Artifact write order does not support freeze before test outputs")
    return {"status": "PASS", "folds_verified": 40, "selected": selected,
            "final_test_metrics": metrics, "test_subset_reload_rows": len(positions),
            "threshold_origin": "2018 raw selected-system score quantiles; no operating threshold selected",
            "calibration_used": False, "system_sha256": freeze["system_sha256"]}


class ContractAcceptanceTest(unittest.TestCase):
    def test_exact_allowlist_and_labels_excluded(self):
        self.assertEqual(contract.FEATURE_NAMES, EXPECTED_FEATURES)
        self.assertEqual(len(set(contract.FEATURE_NAMES)), 36)
        self.assertFalse(set(contract.FEATURE_NAMES) & {
            "ignition", "ignition_count", "large_fire_200ha", "grid_id",
            "ignition_date", "issue_time_utc", "cwfis_observation_date"})
        contract.check_matrix(np.zeros((2, 36)))
        for value in (np.zeros((2, 35)), np.zeros((2, 37)), np.zeros(36)):
            with self.assertRaises(ValueError):
                contract.check_matrix(value)

    def test_partition_is_date_only_and_complete_dates_stay_together(self):
        dates = np.array(["2019-05-01", "2010-10-31", "2017-05-01",
                          "2018-10-31", "2016-05-01", "2017-05-01"])
        np.testing.assert_array_equal(splits.get_partition(dates), [3, 0, 1, 2, 0, 1])
        order = np.array([5, 2, 0, 4, 1, 3])
        np.testing.assert_array_equal(splits.get_partition(dates[order]),
                                      splits.get_partition(dates)[order])

    def test_expanding_folds_never_include_reserved_or_future_years(self):
        dates = np.array([f"{year}-05-01" for year in range(2010, 2020)
                          for _ in range(2)], dtype="datetime64[D]")
        folds = list(splits.development_folds(dates))
        self.assertEqual(len(folds), 5)
        for fold, validation_year in zip(folds, range(2012, 2017)):
            training, validation = dates[fold["train_mask"]], dates[fold["validation_mask"]]
            self.assertEqual(len(validation), 2)
            self.assertEqual(len(training), 2 * (validation_year - 2010))
            self.assertLess(training.max(), validation.min())
            self.assertEqual(str(validation.min()), f"{validation_year}-05-01")
            self.assertFalse(np.any(fold["train_mask"] & fold["validation_mask"]))
            self.assertFalse(np.any((fold["train_mask"] | fold["validation_mask"])[14:]))

    def test_invalid_dates_and_intraday_timestamps_are_rejected(self):
        for values in (["2019-11-01"], ["2010-04-30"], ["2019-02-30"],
                       ["NaT"], ["2019-05-01T12:00:00"],
                       np.array(["2019-05-01T12:00"], dtype="datetime64[m]")):
            with self.subTest(values=values), self.assertRaises(ValueError):
                splits.get_partition(values)

    def test_cache_reload_preserves_schema_rows_labels_and_nan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = np.arange(108, dtype=np.float64).reshape(3, 36)
            matrix[1, 18] = np.nan
            matrix.tofile(root / "X.f64")
            np.array([0, 1, 0], dtype=np.uint8).tofile(root / "y.u8")
            manifest = {"rows": 3, "features": list(EXPECTED_FEATURES),
                        "cache_version": contract.CACHE_VERSION}
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            X, y, restored = data.load_cache(root)
            np.testing.assert_equal(X, matrix)
            np.testing.assert_array_equal(y, [0, 1, 0])
            self.assertEqual(restored, manifest)
            del X, y
            for change in ({"features": list(reversed(EXPECTED_FEATURES))},
                           {"cache_version": "obsolete"}):
                path.write_text(json.dumps(manifest | change))
                with self.assertRaises(ValueError):
                    data.load_cache(root)

    def test_year_slices_and_dates_preserve_all_cell_rows(self):
        manifest = {"date_slices": {"2016-05-01": [0, 2], "2016-05-02": [2, 4],
                                    "2017-05-01": [4, 6], "2018-05-01": [6, 8]}}
        selection = data.year_slice(manifest, 2016, 2017)
        self.assertEqual((selection.start, selection.stop), (0, 6))
        np.testing.assert_array_equal(data.dates_for(manifest, selection).astype(str),
                                      ["2016-05-01"] * 2 + ["2016-05-02"] * 2 + ["2017-05-01"] * 2)


class WeatherAcceptanceTest(unittest.TestCase):
    def test_global_index_recovers_unsorted_cross_chunk_dates_and_first_valid_duplicate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                {"rep_date": "2015-05-01 12:00:00", "fwi": "", "temp": "1"},
                {"rep_date": "2016-05-01 12:00:00", "fwi": "7", "temp": "16"},
                {"rep_date": "2015-05-01 12:00:00", "fwi": "8", "temp": "22"},
                {"rep_date": "2015-05-01 12:00:00", "fwi": "9", "temp": "99"},
                {"rep_date": "2014-05-01 12:00:00", "fwi": "6", "temp": "14"},
            ]
            archive = write_archive(root, rows)
            path = root / "weather.sqlite"
            with patch.object(weather, "ROOT", root):
                audit = weather.index_archive(archive, path, one_cell_grid(), chunk_size=2)
            with closing(sqlite3.connect(path)) as db:
                records = db.execute("SELECT day,temp,fwi,duplicates FROM records ORDER BY day").fetchall()
            self.assertEqual(records, [("2014-05-01", 14.0, 6.0, 0),
                                       ("2015-05-01", 22.0, 8.0, 2),
                                       ("2016-05-01", 16.0, 7.0, 0)])
            self.assertEqual(audit["source_rows"], 5)
            self.assertEqual(audit["unique_date_station_records"], 3)
            self.assertEqual(audit["duplicate_records"], 2)
            self.assertEqual(audit["backward_date_transitions"], 2)

    def test_frozen_checksum_mismatch_is_rejected_before_index_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = write_archive(root, [{"rep_date": "2010-04-30 12:00:00"}])
            (root / "data/raw/cwfis/manifest.json").write_text(json.dumps({"archive_sha256": "wrong"}))
            path = root / "weather.sqlite"
            with patch.object(weather, "ROOT", root), self.assertRaises(ValueError):
                weather.index_archive(archive, path, one_cell_grid())
            self.assertFalse(path.exists())

    def test_inserting_nearer_future_stations_cannot_change_past_weather(self):
        grid = one_cell_grid()
        old = [(f"old{i}", -85.0 + 0.1 * i, 48.0, "2010-04-01") for i in range(1, 6)]
        future = [(f"future{i}", -85.0 + 0.001 * i, 48.0, "2018-04-01") for i in range(1, 5)]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "weather.sqlite"
            rows = [{"day": "2010-04-30", "key": key, "lon": lon, "lat": lat,
                     "temp": 10 + i} for i, (key, lon, lat, _) in enumerate(old)]
            rows += [{"day": "2018-04-01", "key": key, "lon": lon, "lat": lat,
                      "temp": 99} for key, lon, lat, _ in future]
            write_weather_db(path, rows)
            with closing(sqlite3.connect(path)) as db:
                baseline = weather.daily_weather(db, old, weather.AsOfStations(grid, old), "2010-04-30")
                expanded = weather.daily_weather(db, old + future,
                                                 weather.AsOfStations(grid, old + future), "2010-04-30")
            np.testing.assert_equal(baseline, expanded)
            self.assertEqual(expanded[0, 0], 4)
            self.assertEqual(expanded[0, 2], 1)
            self.assertLess(expanded[0, 6], 15)

    def test_quality_flags_and_missing_weather_are_preserved(self):
        grid = one_cell_grid()
        stations = [("a", -85.0, 48.0, "2010-04-01"),
                    ("b", -84.9, 48.0, "2010-04-01")]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "weather.sqlite"
            write_weather_db(path, [{"day": "2010-04-30", "key": "a", "temp": 20,
                                     "imputed": 1, "calcstatus": 1},
                                    {"day": "2010-04-30", "key": "b", "lon": -84.9,
                                     "fwi": None, "temp": 999}])
            with closing(sqlite3.connect(path)) as db:
                choices = weather.AsOfStations(grid, stations)
                observed = weather.daily_weather(db, stations, choices, "2010-04-30")[0]
                missing = weather.daily_weather(db, stations, choices, "2010-05-01")[0]
            np.testing.assert_equal(observed[:6], [1, 0, 0.5, 1, 1, 0])
            self.assertEqual(observed[6], 20)
            self.assertEqual(missing[0], 0)
            self.assertEqual(missing[2], 0)
            self.assertEqual(missing[5], 1)
            self.assertTrue(np.isnan(missing[[1, 3, 4]]).all())
            self.assertTrue(np.isnan(missing[6:]).all())

    def test_seven_day_rain_uses_strict_past_and_requires_all_seven_days(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "weather.sqlite"
            start = date(2010, 4, 24)
            rows = [{"day": (start + timedelta(days=i)).isoformat(), "precip": i + 1,
                     "temp": 10 + i} for i in range(7)]
            rows += [{"day": "2010-05-01", "precip": 9999, "temp": 9999},
                     {"day": "2010-05-02", "precip": None}]
            write_weather_db(path, rows)
            actual = dict(weather.corrected_weather_days(path, one_cell_grid(),
                                                         ["2010-05-01", "2010-05-02", "2010-05-03"]))
            self.assertEqual(actual["2010-05-01"][0, 6], 16)
            self.assertEqual(actual["2010-05-01"][0, -2], 28)
            self.assertEqual(actual["2010-05-01"][0, -1], 7)
            self.assertEqual(actual["2010-05-02"][0, -2], 10026)
            self.assertEqual(actual["2010-05-02"][0, -1], 7)
            self.assertTrue(np.isnan(actual["2010-05-03"][0, -2]))
            self.assertEqual(actual["2010-05-03"][0, -1], 6)

    def test_rain_window_crosses_year_and_season_gaps_without_stale_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "weather.sqlite"
            rows = [{"day": (date(year, 4, 24) + timedelta(days=i)).isoformat(),
                     "precip": value} for year, value in [(2010, 1), (2011, 2)] for i in range(7)]
            write_weather_db(path, rows)
            values = list(weather.corrected_weather_days(path, one_cell_grid(), ["2010-05-01", "2011-05-01"]))
            self.assertEqual([matrix[0, -2] for _, matrix in values], [7, 14])
            self.assertEqual([matrix[0, -1] for _, matrix in values], [7, 7])

    def test_corrected_weather_rejects_duplicate_and_decreasing_issue_dates(self):
        for days in (["2010-05-02", "2010-05-01"], ["2010-05-01", "2010-05-01"]):
            with self.subTest(days=days), self.assertRaises(ValueError):
                list(weather.corrected_weather_days("must-not-be-opened.sqlite", one_cell_grid(), days))


class HistoryAcceptanceTest(unittest.TestCase):
    def test_current_future_events_excluded_and_completed_window_edges_counted(self):
        grid = {"a": GridCell("a", 0, 0, -85, 48),
                "b": GridCell("b", 10000, 0, -84.9, 48)}
        history = FireHistory(grid)
        events = deque([(date(2010, 5, 1), "a"), (date(2010, 5, 2), "a"),
                        (date(2010, 5, 31), "b"), (date(2010, 6, 1), "a"),
                        (date(2010, 6, 2), "a")])
        history.advance(date(2010, 6, 1), events)
        current = history.values("a", date(2010, 6, 1))
        self.assertEqual(current["fire_history_same_cell_30d"], "1")
        self.assertEqual(current["fire_history_neighborhood_30d"], "2")
        self.assertEqual(current["fire_history_same_cell_365d"], "2")
        self.assertEqual(current["fire_history_days_since_same_cell"], "30")
        self.assertEqual(list(events), [(date(2010, 6, 1), "a"), (date(2010, 6, 2), "a")])
        history.advance(date(2010, 6, 2), events)
        self.assertEqual(history.values("a", date(2010, 6, 2))["fire_history_days_since_same_cell"], "1")
        self.assertEqual(list(events), [(date(2010, 6, 2), "a")])

    def test_no_observed_prior_event_remains_censored(self):
        history = FireHistory({"a": GridCell("a", 0, 0, -85, 48)})
        events = deque([(date(2010, 5, 1), "a")])
        history.advance(date(2010, 5, 1), events)
        self.assertEqual(history.values("a", date(2010, 5, 1))["fire_history_days_since_same_cell"], "")
        self.assertEqual(history.values("a", date(2010, 5, 1))["fire_history_same_cell_30d"], "0")


class EvaluationAcceptanceTest(unittest.TestCase):
    def test_independent_metric_audit_handles_unsigned_labels_and_ties(self):
        result = independent_metrics(np.array([0, 1, 0, 1], dtype=np.uint8), [0.1, 0.9, 0.7, 0.4])
        self.assertAlmostEqual(result["average_precision"], 5 / 6)
        self.assertAlmostEqual(result["roc_auc"], 0.75)
        self.assertAlmostEqual(result["log_loss"], -(2 * np.log(0.9) + np.log(0.3) + np.log(0.4)) / 4)
        tied = independent_metrics(np.array([0, 1], dtype=np.uint8), [0.5, 0.5])
        self.assertEqual(tied["roc_auc"], 0.5)
        self.assertEqual(tied["average_precision"], 0.5)

    def test_exact_ranking_calibration_and_false_alert_denominators(self):
        y, probabilities = [0, 1, 0, 1], [0.1, 0.9, 0.7, 0.4]
        dates = ["2018-05-01", "2018-05-01", "2018-05-02", "2018-05-02"]
        result = evaluation.evaluate_predictions(y, probabilities, dates=dates, weather_missing=[0, 0, 1, 1])
        self.assertAlmostEqual(result["average_precision"], 5 / 6)
        self.assertEqual(result["prevalence"], 0.5)
        self.assertEqual(result["n_dates"], 2)
        self.assertAlmostEqual(result["brier_score"], (0.01 + 0.01 + 0.49 + 0.36) / 4)
        self.assertEqual(sum(bin_["n_samples"] for bin_ in result["reliability_bins"]), 4)
        threshold = evaluation.threshold_tradeoffs(y, probabilities, [0.5], dates=dates)[0]
        for field in ("true_positive", "false_positive", "true_negative", "false_negative"):
            self.assertEqual(threshold[field], 1)
        self.assertEqual(threshold["false_positive_rate"], 0.5)
        self.assertEqual(threshold["false_alerts_per_10000_cell_days"], 2500)
        self.assertEqual(threshold["false_alerts_per_day"], 0.5)
        self.assertEqual(threshold["alerts_per_day"], 1)

    def test_ties_use_inclusive_threshold_and_one_class_metrics_are_explicit(self):
        row = evaluation.threshold_tradeoffs([1, 0, 1], [0.5, 0.5, 0.1], [0.5])[0]
        self.assertEqual((row["true_positive"], row["false_positive"], row["false_negative"]), (1, 1, 1))
        result = evaluation.evaluate_predictions([0, 0], [0.1, 0.2])
        self.assertIsNone(result["average_precision"])
        self.assertIsNone(result["roc_auc"])
        self.assertAlmostEqual(result["brier_score"], 0.025)
        for bad in ([np.nan, 0.2], [1.1, 0.2], [0.2]):
            with self.assertRaises(ValueError):
                evaluation.evaluate_predictions([0, 1], bad)


class PreprocessingAcceptanceTest(unittest.TestCase):
    def fixture(self):
        values = np.full((4, 36), 2.0)
        values[:, EXPECTED_FEATURES.index("cwfis_temp_c")] = [1, 3, np.nan, 5]
        values[:, EXPECTED_FEATURES.index("cwfis_wind_speed_kmh")] = np.nan
        values[:, EXPECTED_FEATURES.index("fire_history_days_since_same_cell")] = [np.nan, 1, 3, 5]
        values[:, EXPECTED_FEATURES.index("calendar_doy_sin")] = [0.1, 0.2, 0.3, 0.4]
        return values

    def test_manual_training_medians_scaling_all_missing_and_censoring(self):
        X = self.fixture()
        fitted = NumericPreprocessor(chunk_size=2).fit(X)
        transformed = fitted.transform(X)
        temp = EXPECTED_FEATURES.index("cwfis_temp_c")
        wind = EXPECTED_FEATURES.index("cwfis_wind_speed_kmh")
        history = EXPECTED_FEATURES.index("fire_history_days_since_same_cell")
        self.assertEqual(transformed.shape, (4, 57))
        self.assertEqual(fitted.n_samples_seen_, 4)
        self.assertEqual(fitted.medians_[temp], 3)
        self.assertEqual(fitted.training_mean_[temp], 3)
        self.assertAlmostEqual(fitted.training_variance_[temp], 2)
        np.testing.assert_allclose(transformed[:, temp], np.array([-2, 0, 0, 2]) / np.sqrt(2))
        self.assertTrue(fitted.all_missing_[wind])
        self.assertEqual((fitted.medians_[wind], fitted.mean_[wind], fitted.scale_[wind]), (0, 0, 1))
        np.testing.assert_equal(transformed[:, wind], np.zeros(4))
        self.assertEqual(fitted.medians_[history], 3)
        names = list(fitted.get_feature_names_out())
        np.testing.assert_array_equal(transformed[:, names.index("cwfis_temp_c__missing")], [0, 0, 1, 0])
        np.testing.assert_array_equal(transformed[:, names.index("cwfis_wind_speed_kmh__missing")], [1, 1, 1, 1])
        np.testing.assert_array_equal(transformed[:, names.index("fire_history_days_since_same_cell__censored")], [1, 0, 0, 0])
        sine = EXPECTED_FEATURES.index("calendar_doy_sin")
        np.testing.assert_array_equal(transformed[:, sine], X[:, sine])
        self.assertTrue(np.isfinite(transformed).all())
        self.assertTrue(np.isin(transformed[:, 36:], [0, 1]).all())

    def test_holdout_mutation_cannot_change_fitted_training_statistics(self):
        fitted = NumericPreprocessor(chunk_size=1).fit(self.fixture())
        names = ("medians_", "observed_counts_", "all_missing_", "training_mean_",
                 "training_variance_", "mean_", "scale_")
        before = {name: getattr(fitted, name).copy() for name in names}
        held_out = self.fixture() * 1000
        held_out[0, :] = np.nan
        held_out[:, EXPECTED_FEATURES.index("cwfis_wind_speed_kmh")] = [11, 22, 33, 44]
        output = fitted.transform(held_out)
        self.assertEqual(output[0, EXPECTED_FEATURES.index("cwfis_temp_c")], 0)
        self.assertEqual(output[0, EXPECTED_FEATURES.index("cwfis_wind_speed_kmh")], 11)
        fitted.transform(np.full((3, 36), -1e8))
        for name in names:
            np.testing.assert_array_equal(getattr(fitted, name), before[name])
        self.assertEqual(fitted.n_samples_seen_, 4)

    def test_schema_errors_infinite_values_and_empty_training_fail(self):
        X = pd.DataFrame(self.fixture(), columns=EXPECTED_FEATURES)
        for invalid in (X.iloc[:, ::-1], X.assign(ignition=1), X.astype(str), np.empty((0, 36))):
            with self.subTest(shape=invalid.shape), self.assertRaises(ValueError):
                NumericPreprocessor().fit(invalid)
        for transformer in (NumericPreprocessor(), HistoryIndicator()):
            invalid = self.fixture()
            invalid[0, 0] = np.inf
            with self.assertRaises(ValueError):
                transformer.fit(invalid)
            transformer.fit(self.fixture())
            with self.assertRaises(ValueError):
                transformer.transform(invalid)

    def test_native_missing_transform_retains_values_and_adds_only_history_flag(self):
        X = self.fixture()
        transformed = HistoryIndicator(chunk_size=1).fit_transform(X)
        self.assertEqual(transformed.shape, (4, 37))
        np.testing.assert_equal(transformed[:, :36], X)
        np.testing.assert_array_equal(transformed[:, 36], [1, 0, 0, 0])

    def test_disk_chunking_and_reload_preserve_transform_and_schema(self):
        X = self.fixture()
        reference = NumericPreprocessor(chunk_size=4).fit_transform(X)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fitted = NumericPreprocessor(scratch_dir=root, chunk_size=1)
            disk = fitted.fit_transform(X)
            self.assertIsInstance(disk, np.memmap)
            np.testing.assert_allclose(disk, reference, atol=1e-12, rtol=0)
            model_path = root / "preprocessing.joblib"
            joblib.dump(fitted, model_path)
            reloaded = joblib.load(model_path)
            np.testing.assert_allclose(reloaded.transform(X), reference, atol=1e-12, rtol=0)
            np.testing.assert_array_equal(reloaded.get_feature_names_out(), fitted.get_feature_names_out())
            disk._mmap.close()


class ModelIntegrationAcceptanceTest(unittest.TestCase):
    def fixture(self):
        X = np.full((200, 36), 2.0)
        X[:, EXPECTED_FEATURES.index("cwfis_temp_c")] = np.linspace(5, 30, 200)
        X[::3, EXPECTED_FEATURES.index("cwfis_precip_mm")] = np.nan
        X[::2, EXPECTED_FEATURES.index("fire_history_days_since_same_cell")] = np.nan
        y = (np.arange(200) % 10 == 0).astype(np.uint8)
        return X, y

    def assert_reload(self, pipeline, root, X):
        path = root / "pipeline.joblib"
        expected = pipeline.predict_proba(X)
        joblib.dump(pipeline, path)
        restored = joblib.load(path)
        np.testing.assert_allclose(restored.predict_proba(X), expected, atol=1e-14, rtol=0)
        self.assertEqual(expected.shape, (len(X), 2))
        self.assertTrue(np.isfinite(expected).all())
        np.testing.assert_allclose(expected.sum(axis=1), 1)
        frame = pd.DataFrame(X, columns=EXPECTED_FEATURES)
        np.testing.assert_allclose(restored.predict_proba(frame), expected, atol=1e-14, rtol=0)
        with self.assertRaises(ValueError):
            restored.predict_proba(frame.iloc[:, ::-1])
        bundle = {"pipeline": restored, "calibrator": None, "features": list(EXPECTED_FEATURES)}
        np.testing.assert_allclose(run.predict_bundle(bundle, X), expected[:, 1], atol=1e-14, rtol=0)
        bundle["features"] = list(reversed(EXPECTED_FEATURES))
        with self.assertRaises(ValueError):
            run.predict_bundle(bundle, X)

    def test_logistic_weights_apply_once_and_every_fixture_row_is_seen_each_epoch(self):
        X, y = self.fixture()
        observed = []
        original = logistic.SGDClassifier.partial_fit

        def trace(estimator, values, labels, *args, **kwargs):
            observed.append((labels.copy(), kwargs["sample_weight"].copy()))
            return original(estimator, values, labels, *args, **kwargs)

        with patch.object(logistic.SGDClassifier, "partial_fit", trace):
            pipeline, report = logistic.train_logistic(X, y, {
                "epochs": 2, "batch_size": 60, "class_weight": "balanced"})
        self.assertEqual(report["class_counts"], {"0": 180, "1": 20})
        self.assertEqual(report["training_class_weights"], {"0": 200 / 360, "1": 5})
        self.assertIsNone(pipeline["classifier"].class_weight)
        self.assertEqual(sum(len(labels) for labels, _ in observed), 400)
        self.assertEqual(sum(int(labels.sum()) for labels, _ in observed), 40)
        for labels, weights in observed:
            np.testing.assert_array_equal(weights, np.where(labels == 1, 5, 200 / 360))
        self.assertEqual(pipeline["preprocessor"].n_samples_seen_, 200)
        self.assertEqual(pipeline["classifier"].loss, "log_loss")
        self.assertFalse(report["resampling"])
        self.assertFalse(report["convergence_proven"])
        for epoch in report["epochs"]:
            self.assertEqual((epoch["rows_seen"], epoch["positives_seen"]), (200, 20))
            self.assertTrue(np.isfinite(epoch["weighted_train_objective"]))
        with tempfile.TemporaryDirectory() as temporary:
            self.assert_reload(pipeline, Path(temporary), X)

    def test_hgb_native_missing_weights_early_stopping_and_saved_pipeline(self):
        X, y = self.fixture()
        with tempfile.TemporaryDirectory() as temporary, threadpool_limits(limits=2):
            root = Path(temporary)
            pipeline, report = histogram.train_hgb(X, y, {
                "max_iter": 4, "min_samples_leaf": 5, "class_weight": "balanced"}, scratch_dir=root)
            classifier = pipeline["classifier"]
            self.assertFalse(classifier.early_stopping)
            self.assertEqual(classifier.class_weight, "balanced")
            self.assertEqual(classifier.n_iter_, 4)
            self.assertEqual(classifier.n_features_in_, 37)
            self.assertEqual(report["class_counts"], {"0": 180, "1": 20})
            self.assertEqual(report["effective_class_weights"], {"0": 200 / 360, "1": 5})
            self.assertEqual(report["n_rows"], 200)
            self.assertFalse(report["internal_random_validation"])
            np.testing.assert_equal(pipeline["preprocessor"].transform(X)[:, :36], X)
            self.assertFalse(list(root.glob("HistoryIndicator-*.f64")))
            self.assert_reload(pipeline, root, X)

    def test_resume_rejects_stale_run_configuration_before_any_fit(self):
        X, y = self.fixture()
        manifest = {"features": list(EXPECTED_FEATURES), "cache_version": contract.CACHE_VERSION,
                    "original_panel_sha256": "fixture"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text("{}")
            output = root / "run"
            output.mkdir()
            stale = {"seed": -1, "features": list(EXPECTED_FEATURES)}
            (output / "configuration.json").write_text(json.dumps(stale))
            with patch.object(run, "load_cache", return_value=(X, y, manifest)), \
                 patch.object(run, "check_gate"), patch.object(run, "train_model") as trainer:
                with self.assertRaisesRegex(ValueError, "configuration/code/cache differs"):
                    run.execute(root, output)
                trainer.assert_not_called()
            self.assertEqual(json.loads((output / "configuration.json").read_text()), stale)


if __name__ == "__main__":
    unittest.main()
