"""Stream the entire ignition v1 panel without dropping, imputing or fitting rows."""
from __future__ import annotations

import argparse
import csv
import ctypes
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NA_TOKENS = {"", "NA", "N/A", "NULL", "NaN", "nan", "null"}
DAILY_WEATHER = ["cwfis_temp_c", "cwfis_rh_pct", "cwfis_wind_speed_kmh", "cwfis_precip_mm", "cwfis_ffmc", "cwfis_dmc", "cwfis_dc", "cwfis_isi", "cwfis_bui", "cwfis_fwi", "cwfis_dsr"]
WEATHER_QUALITY = ["cwfis_nearest_station_km", "cwfis_imputed_station_fraction", "cwfis_calcstatus_nonzero_fraction"]
TERRAIN = ["terrain_elevation_mean_m", "terrain_slope_deg", "terrain_aspect_sin", "terrain_aspect_cos", "terrain_ruggedness_m", "terrain_sample_count", "terrain_coverage_fraction"]
HISTORY = ["fire_history_same_cell_30d", "fire_history_neighborhood_30d", "fire_history_same_cell_365d", "fire_history_neighborhood_365d", "fire_history_days_since_same_cell"]
EXPECTED_METADATA = {"feature_set_version": "ignition-feature-v1.0.0", "terrain_source_id": "nrcan_cdem_1945_2011", "terrain_source_vintage_end_year": "2011", "cwfis_source_id": "cwfis_fwi_2010s_v3_ll", "fire_history_source_id": "nfdb_ontario_ignition_labels"}
BOUNDS = {"calendar_day_of_year": (1, 366), "calendar_day_length_hours": (0, 24), "terrain_slope_deg": (0, 90), "terrain_sample_count": (0, 9), "cwfis_station_count": (0, 4), "cwfis_precip_7d_coverage_days": (0, 7), "cwfis_rh_pct": (0, 100), "cwfis_ffmc": (0, 101)}
BOUNDS.update({column: (-1, 1) for column in ["calendar_doy_sin", "calendar_doy_cos", "terrain_aspect_sin", "terrain_aspect_cos"]})
BOUNDS.update({column: (0, 1) for column in ["terrain_coverage_fraction", "cwfis_observation_coverage_fraction", "cwfis_imputed_station_fraction", "cwfis_calcstatus_nonzero_fraction"]})
NONNEGATIVE = {"ignition_count", "terrain_ruggedness_m", "cwfis_nearest_station_km", "cwfis_wind_speed_kmh", "cwfis_precip_mm", "cwfis_precip_7d_mm", *DAILY_WEATHER[5:], *HISTORY}


def memory_snapshot() -> dict[str, int | None]:
    """Return physical availability and process peak; measurements are informational."""
    if os.name != "nt":
        import resource
        factor = 1 if sys.platform == "darwin" else 1024
        return {"available_physical_bytes": None, "process_peak_working_set_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * factor}
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), *[(name, ctypes.c_ulonglong) for name in ["total_physical", "available_physical", "total_page", "available_page", "total_virtual", "available_virtual", "extended_virtual"]]]
    class ProcessMemory(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong), *[(name, ctypes.c_size_t) for name in ["peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile"]]]
    state, process = MemoryStatus(), ProcessMemory()
    state.length, process.cb = ctypes.sizeof(state), ctypes.sizeof(process)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state))
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    get_process_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_process_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemory), ctypes.c_ulong]
    if not get_process_memory(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(process), process.cb):
        raise OSError("Could not read process memory counters")
    return {"available_physical_bytes": int(state.available_physical), "process_peak_working_set_bytes": int(process.peak_working_set)}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def numeric_values(raw: pd.DataFrame, columns: list[str], checks: Counter) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Check original numeric precision before converting bounded model inputs to float32."""
    values, nulls = {}, {}
    integers = {"ignition", "ignition_count", "large_fire_200ha", "calendar_day_of_year", "calendar_is_weekend", "terrain_sample_count", "cwfis_station_count", "cwfis_missing", "cwfis_precip_7d_coverage_days", "terrain_source_vintage_end_year", *HISTORY}
    for column in columns:
        missing = raw[column].isin(NA_TOKENS).to_numpy(dtype=bool)
        original = pd.to_numeric(raw[column].mask(missing), errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
        checks[f"numeric_conversion_failure_cells:{column}"] += int((~missing & np.isnan(original)).sum())
        checks[f"numeric_infinite_cells:{column}"] += int(np.isinf(original).sum())
        finite = np.isfinite(original)
        checks[f"float32_overflow_cells:{column}"] += int((finite & (np.abs(original) > np.finfo(np.float32).max)).sum())
        if column in BOUNDS:
            lo, hi = BOUNDS[column]
            checks[f"outside_documented_range_rows:{column}"] += int(((original < lo) | (original > hi)).sum())
        if column in NONNEGATIVE:
            checks[f"negative_rows:{column}"] += int((original < 0).sum())
        if column in integers:
            checks[f"noninteger_cells:{column}"] += int((finite & (original != np.floor(original))).sum())
            checks[f"integer_outside_exact_float32_cells:{column}"] += int((finite & (np.abs(original) > 2 ** 24)).sum())
        with np.errstate(over="ignore", invalid="ignore"):
            values[column] = original.astype(np.float32)
        nulls[column] = missing
    return values, nulls


def _markdown(report: dict) -> str:
    totals = report["totals"]
    lines = ["# Ignition data quality report", "", f"Status: **{report['status']}**. Full scan: **{report['full_scan_completed']}**. No rows dropped, changed, imputed or fitted.", "", f"Source: `{report['source']['filename']}`; {report['source']['bytes']:,} bytes; SHA-256 `{report['source']['sha256']}`.", "", f"Scanned {totals['rows']:,} rows, {len(report['schema']['columns'])} columns, {totals['positive_cell_days']:,} positive cell-days, {totals['event_records']:,} event records and {totals['large_fire_positive_cell_days']:,} large-fire labels. Ignition prevalence: {totals['ignition_prevalence_pct']:.6f}%.", "", "| Year | Rows | Ignitions | Weather missing | Rain 7-day missing | History censored |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for annual in report["annual"]:
        lines.append(f"| {annual['year']} | {annual['rows']:,} | {annual['positive_cell_days']:,} | {annual['weather_missing_rows']:,} ({annual['weather_missing_pct']:.4f}%) | {annual['rain_7d_missing_rows']:,} | {annual['history_censored_rows']:,} |")
    lines += ["", "Weather missing means no usable selected prior-day station. Seven-day precipitation is blank unless all seven completed observations are available. Blank days-since-fire means no earlier observed same-cell event in the available 2010+ history; it is censoring, not zero days or weather missingness.", "", f"Validation: {len(report['validation_violation_counts'])} listed checks; {sum(report['validation_violation_counts'].values()):,} total check violations (a cell may trigger multiple checks). Exact counts and all-column null counts are in the JSON.", "", "Additional checks beyond the EDA include explicit numeric token conversion failures, float32 overflow and integer precision, required-field nulls, all quality fractions, terrain ranges/static consistency, calendar cycles/weekend/day length, and cross-field history ordering.", "", "Memory: chunk limit " + f"{report['memory']['chunk_rows']:,}; peak process working set {report['memory']['observed_process_peak_working_set_bytes'] / 2 ** 20:.1f} MiB. The full 36-predictor float32 matrix alone needs {report['memory']['full_predictor_float32_bytes'] / 2 ** 30:.2f} GiB, before copies, indicators or model workspace. Use bounded training samples or suitably provisioned infrastructure.", "", "Audit limits:", ""]
    lines += [f"- {limit}" for limit in report["audit_limits"]]
    failures = {key: count for key, count in report["validation_violation_counts"].items() if count}
    if failures:
        lines += ["", "Nonzero checks:", "", *[f"- `{key}`: {count:,}" for key, count in failures.items()]]
    return "\n".join(lines) + "\n"


def validate_dataset(input_path: str | Path, output_dir: str | Path | None = None, *, chunk_size: int = 100_000, feature_selection_path: str | Path | None = None, grid_path: str | Path | None = None) -> dict:
    if not 1 <= chunk_size <= 100_000:
        raise ValueError("chunk_size must be between 1 and 100000")
    start = time.monotonic()
    source = Path(input_path).resolve()
    selection_path = Path(feature_selection_path or ROOT / "data/metadata/ignition_feature_selection_v1.json")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    predictors = selection["predictors"]
    if selection["target"] != "ignition" or len(predictors) != 36 or len(set(predictors)) != 36:
        raise ValueError("Expected the frozen v1 target and unique 36-column allowlist")
    labels = [selection["target"], *selection["other_labels"]]
    if set(predictors) & set(labels + selection["metadata"]):
        raise ValueError("Label/metadata leakage in predictor allowlist")
    numeric = [*labels, *predictors, "terrain_source_vintage_end_year"]
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", newline="", encoding="utf-8") as stream:
        header = next(csv.reader(stream))
    expected_columns = set(labels + predictors + selection["metadata"])
    if len(header) != 48 or len(set(header)) != 48 or set(header) != expected_columns:
        raise ValueError(f"Expected unique 48-column schema; missing={expected_columns - set(header)}, extra={set(header) - expected_columns}")
    provenance_path = ROOT / "docs/eda/source_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
    grid = pd.read_csv(grid_path or ROOT / "data/interim/ontario_fire_region_10km_grid.csv", dtype={"grid_id": "string"})
    if grid.grid_id.isna().any() or grid.grid_id.duplicated().any():
        raise ValueError("Reference grid keys must be non-null and unique")
    grid_index = pd.Index(grid.grid_id)
    dates = pd.DatetimeIndex([date for year in range(2010, 2020) for date in pd.date_range(f"{year}-05-01", f"{year}-10-31")])
    date_index = pd.Index(dates.strftime("%Y-%m-%d"))
    prior = (dates - pd.Timedelta(days=1)).strftime("%Y-%m-%d").to_numpy()
    issue = dates.tz_localize("America/Toronto").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ").to_numpy()
    phase = 2 * np.pi * dates.dayofyear.to_numpy() / np.where(dates.is_leap_year, 366, 365)
    declination = np.deg2rad(-23.44) * np.cos(2 * np.pi * (dates.dayofyear.to_numpy() + 10) / 365.2422)
    expected_rows = len(grid) * len(dates)
    seen = np.zeros(expected_rows, dtype=np.uint8)
    cell_rows, date_rows = np.zeros(len(grid), dtype=np.int64), np.zeros(len(dates), dtype=np.int64)
    terrain_reference = {column: np.full(len(grid), np.nan, dtype=np.float32) for column in TERRAIN}
    terrain_seen = np.zeros(len(grid), dtype=bool)
    checks, missing_counts, status_counts = Counter(), Counter(), Counter()
    annual = {year: Counter() for year in range(2010, 2020)}
    ranges = {column: [None, None] for column in numeric}
    before = memory_snapshot()
    rows = positives = events = large = duplicates = 0
    max_chunk_bytes = 0
    nullable = {*DAILY_WEATHER, *WEATHER_QUALITY, "cwfis_precip_7d_mm", "fire_history_days_since_same_cell", *TERRAIN[:5]}

    def record(name, invalid):
        checks[name] += int(np.count_nonzero(invalid))

    for part, raw in enumerate(pd.read_csv(source, dtype="string", keep_default_na=False, chunksize=chunk_size, on_bad_lines="error"), 1):
        n = len(raw)
        rows += n
        max_chunk_bytes = max(max_chunk_bytes, int(raw.memory_usage(index=True, deep=True).sum()))
        value, nulls = numeric_values(raw, numeric, checks)
        for column in header:
            null = nulls[column] if column in nulls else raw[column].isin(NA_TOKENS).to_numpy(dtype=bool)
            missing_counts[column] += int(null.sum())
            if column not in nullable:
                record(f"required_null_cells:{column}", null)
        for column, expected in EXPECTED_METADATA.items():
            record(f"metadata_value_mismatch_rows:{column}", raw[column].ne(expected).to_numpy(dtype=bool))
        for column, values in value.items():
            finite = values[np.isfinite(values)]
            if finite.size:
                lo, hi = float(finite.min()), float(finite.max())
                ranges[column] = [lo if ranges[column][0] is None else min(lo, ranges[column][0]), hi if ranges[column][1] is None else max(hi, ranges[column][1])]
        y, weather_missing = value["ignition"], value["cwfis_missing"]
        positive = y == 1
        positives += int(positive.sum())
        events += int(np.where(np.isfinite(value["ignition_count"]), value["ignition_count"], 0).sum(dtype=np.float64))
        large += int((value["large_fire_200ha"] == 1).sum())
        for target in (0, 1):
            for missing in (0, 1):
                status_counts[f"ignition={target},cwfis_missing={missing}"] += int(((y == target) & (weather_missing == missing)).sum())
        gi, di = grid_index.get_indexer(raw.grid_id), date_index.get_indexer(raw.ignition_date)
        valid = (gi >= 0) & (di >= 0)
        record("unknown_grid_id_rows", gi < 0)
        record("date_outside_expected_fire_seasons_rows", di < 0)
        keys = di[valid] * len(grid) + gi[valid]
        unique_keys = np.unique(keys)
        duplicates += len(keys) - len(unique_keys) + int(seen[unique_keys].sum())
        seen[unique_keys] = 1
        cell_rows += np.bincount(gi[gi >= 0], minlength=len(grid))
        date_rows += np.bincount(di[di >= 0], minlength=len(dates))
        safe_di = np.maximum(di, 0)
        record("prior_day_observation_date_mismatch_rows", (di < 0) | raw.cwfis_observation_date.ne(prior[safe_di]).to_numpy(dtype=bool))
        record("local_midnight_issue_time_mismatch_rows", (di < 0) | raw.issue_time_utc.ne(issue[safe_di]).to_numpy(dtype=bool))
        record("day_of_year_mismatch_rows", (di < 0) | (value["calendar_day_of_year"] != dates.dayofyear.to_numpy()[safe_di]))
        record("weekend_flag_mismatch_rows", (di < 0) | (value["calendar_is_weekend"] != (dates.dayofweek.to_numpy()[safe_di] >= 5)))
        for column, expected in [("calendar_doy_sin", np.sin(phase)), ("calendar_doy_cos", np.cos(phase))]:
            record(f"calendar_cycle_mismatch_rows:{column}", (di < 0) | ~np.isclose(value[column], expected[safe_di], atol=2e-6, rtol=0))
        for column in ["ignition", "large_fire_200ha", "cwfis_missing", "calendar_is_weekend"]:
            record(f"nonbinary_rows:{column}", ~np.isin(value[column], (0, 1)))
        record("ignition_count_label_mismatch_rows", y != (value["ignition_count"] > 0))
        record("large_fire_without_ignition_rows", value["large_fire_200ha"] > y)
        record("station_coverage_fraction_mismatch_rows", ~np.isclose(value["cwfis_observation_coverage_fraction"], value["cwfis_station_count"] / 4, atol=2e-6, rtol=0))
        record("terrain_coverage_fraction_mismatch_rows", ~np.isclose(value["terrain_coverage_fraction"], value["terrain_sample_count"] / 9, atol=2e-6, rtol=0))
        record("weather_missing_station_count_mismatch_rows", weather_missing != (value["cwfis_station_count"] == 0))
        for column in DAILY_WEATHER + WEATHER_QUALITY:
            record(f"daily_weather_null_pattern_mismatch_rows:{column}", nulls[column] != (weather_missing == 1))
        record("rain_7d_null_coverage_mismatch_rows", nulls["cwfis_precip_7d_mm"] != (value["cwfis_precip_7d_coverage_days"] < 7))
        for column in ["terrain_elevation_mean_m", "terrain_ruggedness_m"]:
            record(f"terrain_null_coverage_mismatch_rows:{column}", nulls[column] != (value["terrain_sample_count"] == 0))
        for column in ["terrain_slope_deg", "terrain_aspect_sin", "terrain_aspect_cos"]:
            record(f"terrain_null_coverage_mismatch_rows:{column}", nulls[column] != (value["terrain_sample_count"] < 9))
        aspect_observed = ~nulls["terrain_aspect_sin"] & ~nulls["terrain_aspect_cos"]
        record("terrain_aspect_unit_circle_mismatch_rows", aspect_observed & ~np.isclose(value["terrain_aspect_sin"] ** 2 + value["terrain_aspect_cos"] ** 2, 1, atol=3e-6, rtol=0))
        for window in (30, 365):
            record(f"same_cell_history_exceeds_neighborhood_rows:{window}d", value[f"fire_history_same_cell_{window}d"] > value[f"fire_history_neighborhood_{window}d"])
        for area in ("same_cell", "neighborhood"):
            record(f"history_30d_exceeds_365d_rows:{area}", value[f"fire_history_{area}_30d"] > value[f"fire_history_{area}_365d"])
        days_since = value["fire_history_days_since_same_cell"]
        record("days_since_nonpositive_observed_rows", ~nulls["fire_history_days_since_same_cell"] & (days_since <= 0))
        for window in (30, 365):
            same_count = value[f"fire_history_same_cell_{window}d"]
            record(f"history_days_since_window_mismatch_rows:{window}d", ((same_count > 0) & (np.isnan(days_since) | (days_since > window))) | ((same_count == 0) & (days_since <= window)))
        record("history_days_since_pre2010_rows", (di >= 0) & (days_since > (dates - pd.Timestamp("2010-01-01")).days.to_numpy()[safe_di]))
        valid_grid_rows = np.flatnonzero(gi >= 0)
        unique_grid, first = np.unique(gi[valid_grid_rows], return_index=True)
        initial = ~terrain_seen[unique_grid]
        for column in TERRAIN:
            terrain_reference[column][unique_grid[initial]] = value[column][valid_grid_rows[first[initial]]]
            record(f"terrain_changes_within_cell_rows:{column}", ~np.isclose(value[column][valid_grid_rows], terrain_reference[column][gi[valid_grid_rows]], atol=1e-5, rtol=0, equal_nan=True))
        terrain_seen[unique_grid] = True
        if valid.any():
            latitude = grid.centroid_latitude.to_numpy()[gi[valid]]
            hour_angle = np.arccos(np.clip(-np.tan(np.deg2rad(latitude)) * np.tan(declination[di[valid]]), -1, 1))
            record("day_length_formula_mismatch_rows", ~np.isclose(value["calendar_day_length_hours"][valid], 24 * hour_angle / np.pi, atol=3e-6, rtol=0))
        for year in range(2010, 2020):
            mask = (di >= 0) & (dates.year.to_numpy()[safe_di] == year)
            if not mask.any():
                continue
            row = annual[year]
            row["rows"] += int(mask.sum())
            row["positive_cell_days"] += int((positive & mask).sum())
            row["event_records"] += int(np.where(mask & np.isfinite(value["ignition_count"]), value["ignition_count"], 0).sum(dtype=np.float64))
            row["large_fire_positive_cell_days"] += int(((value["large_fire_200ha"] == 1) & mask).sum())
            row["weather_missing_rows"] += int(((weather_missing == 1) & mask).sum())
            row["positive_weather_missing_rows"] += int((positive & (weather_missing == 1) & mask).sum())
            for field, column in [("rain_7d_missing_rows", "cwfis_precip_7d_mm"), ("history_censored_rows", "fire_history_days_since_same_cell"), ("terrain_elevation_missing_rows", "terrain_elevation_mean_m"), ("terrain_slope_missing_rows", "terrain_slope_deg")]:
                row[field] += int((nulls[column] & mask).sum())
        if part % 10 == 0:
            print(f"validation: {rows:,} rows scanned", flush=True)
        del raw, value, nulls

    checks.update({"duplicate_key_excess_rows": duplicates, "expected_keys_missing": int((seen == 0).sum()), "row_count_mismatch": int(rows != expected_rows), "reference_grid_cell_count_mismatch": int(len(grid) != 9673), "dates_with_incorrect_cell_count": int((date_rows != len(grid)).sum()), "cells_with_incorrect_date_count": int((cell_rows != len(dates)).sum())})
    digest = file_sha256(source)
    pinned_checksum_applicable = source.name == provenance.get("source_file_name") and "sha256" in provenance
    checks["pinned_source_sha256_mismatch"] = int(pinned_checksum_applicable and digest != provenance["sha256"])
    if provenance.get("columns"):
        checks["source_column_order_mismatch"] = int(header != provenance["columns"])
    after = memory_snapshot()
    annual_rows = []
    for year, counts in annual.items():
        fields = ["rows", "positive_cell_days", "event_records", "large_fire_positive_cell_days", "weather_missing_rows", "positive_weather_missing_rows", "rain_7d_missing_rows", "history_censored_rows", "terrain_elevation_missing_rows", "terrain_slope_missing_rows"]
        row = {"year": year, **{field: counts[field] for field in fields}}
        row["ignition_prevalence_pct"] = 100 * row["positive_cell_days"] / row["rows"] if row["rows"] else 0
        row["weather_missing_pct"] = 100 * row["weather_missing_rows"] / row["rows"] if row["rows"] else 0
        annual_rows.append(row)
    report = {"status": "PASS" if not any(checks.values()) else "FAIL", "created_at_utc": datetime.now(timezone.utc).isoformat(), "full_scan_completed": True, "source": {"filename": source.name, "bytes": source.stat().st_size, "sha256": digest, "pinned_checksum_applicable": pinned_checksum_applicable, "matches_pinned_eda_sha256": digest == provenance.get("sha256")}, "schema": {"columns": header, "predictors": predictors, "predictor_count": len(predictors), "target": "ignition", "excluded_labels": selection["other_labels"], "metadata_excluded_from_predictors": selection["metadata"], "numeric_columns": numeric, "missing_tokens": sorted(NA_TOKENS)}, "totals": {"rows": rows, "expected_rows": expected_rows, "grid_cells": len(grid), "fire_season_dates": len(dates), "positive_cell_days": positives, "negative_cell_days": rows - positives, "event_records": events, "large_fire_positive_cell_days": large, "ignition_prevalence_pct": 100 * positives / rows if rows else 0, "weather_label_counts": dict(status_counts)}, "annual": annual_rows, "missingness": {column: {"rows": missing_counts[column], "pct": 100 * missing_counts[column] / rows if rows else 0} for column in header}, "numeric_ranges_float32": ranges, "validation_violation_counts": dict(sorted(checks.items())), "memory": {"chunk_rows": chunk_size, "maximum_raw_string_chunk_bytes": max_chunk_bytes, "key_bitmap_bytes": seen.nbytes, "numeric_chunk_float32_bytes_upper_bound": chunk_size * len(numeric) * 4, "available_physical_bytes_before": before["available_physical_bytes"], "available_physical_bytes_after": after["available_physical_bytes"], "observed_process_peak_working_set_bytes": after["process_peak_working_set_bytes"], "full_predictor_float32_bytes": rows * len(predictors) * 4, "full_predictor_float64_bytes": rows * len(predictors) * 8}, "library_versions": {"python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__}, "scan_seconds": round(time.monotonic() - start, 2), "audit_limits": ["Checks validate the downloaded panel and internal contracts; NFDB labels/history, raw station selection, and precipitation sums are not independently rebuilt.", "Archives lack per-record historical available_at timestamps. This is a retrospective hindcast, not an operational forecast replay.", "The station candidate set and study-period grid universe may introduce selection effects; full source-level sensitivity analysis remains outstanding.", "Numeric ranges are reported after float32 conversion; token conversion, infinities, integer validity and overflow were checked at original float64 precision first. Continuous-value float32 rounding is expected.", "No rows are deleted, no imputation/scaling or model fitting is performed; invalid data cause FAIL. Source parsing/schema exceptions terminate the CLI with a nonzero exit.", "Peak working set covers this process only, including imports/parser/transient arrays. Model matrix estimates exclude copies, missingness indicators, transformed arrays and fitting workspace."]}
    if output_dir is not None:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / "data_quality_report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (output / "data_quality_report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/processed/ontario_fire_region_10km_daily_ignition_features_v1.csv.gz")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/modeling")
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--feature-selection", type=Path)
    parser.add_argument("--grid", type=Path)
    args = parser.parse_args()
    report = validate_dataset(args.input, args.output_dir, chunk_size=args.chunk_size, feature_selection_path=args.feature_selection, grid_path=args.grid)
    print(json.dumps({"status": report["status"], "rows": report["totals"]["rows"], "positive_cell_days": report["totals"]["positive_cell_days"], "nonzero_checks": {key: count for key, count in report["validation_violation_counts"].items() if count}, "scan_seconds": report["scan_seconds"], "peak_working_set_mib": round(report["memory"]["observed_process_peak_working_set_bytes"] / 2 ** 20, 1)}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
