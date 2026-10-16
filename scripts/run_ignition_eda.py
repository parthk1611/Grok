"""Read-only EDA of the downloaded v1 panel; no imputation or model fitting."""
import argparse
import csv
import gzip
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input", type=Path, default=ROOT / "data/processed/ontario_fire_region_10km_daily_ignition_features_v1.csv.gz")
parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/eda")
parser.add_argument("--grid", type=Path, default=ROOT / "data/interim/ontario_fire_region_10km_grid.csv")
parser.add_argument("--feature-selection", type=Path, default=ROOT / "data/metadata/ignition_feature_selection_v1.json")
args = parser.parse_args()
OUT, SOURCE = args.output_dir.resolve(), args.input.resolve()
CHUNK_SIZE, SAMPLE_SIZE, SEED = 200_000, 100_000, 385
OUT.mkdir(parents=True, exist_ok=True)
selection = json.loads(args.feature_selection.read_text(encoding="utf-8"))
predictors = selection["predictors"]
numeric = ["ignition", "ignition_count", "large_fire_200ha", *predictors]
grid = pd.read_csv(args.grid)
grid_index = pd.Index(grid.grid_id)
dates = pd.DatetimeIndex([d for y in range(2010, 2020) for d in pd.date_range(f"{y}-05-01", f"{y}-10-31")])
date_strings = pd.Index(dates.strftime("%Y-%m-%d"))
issue_map = dict(zip(date_strings, dates.tz_localize("America/Toronto").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")))
prior_map = dict(zip(date_strings, (dates - pd.Timedelta(days=1)).strftime("%Y-%m-%d")))
doy_map = dict(zip(date_strings, dates.dayofyear))
expected_rows = len(grid) * len(dates)
seen = np.zeros(expected_rows, dtype=np.uint8)
opener = gzip.open if SOURCE.suffix == ".gz" else open
with opener(SOURCE, "rt", newline="", encoding="utf-8") as f:
    header = next(csv.reader(f))
expected_columns = set(numeric + selection["metadata"])
if len(header) != 48 or len(set(header)) != 48 or set(header) != expected_columns:
    raise ValueError(f"Expected the unique 48-column v1 schema; missing={expected_columns-set(header)}, extra={set(header)-expected_columns}")
dtypes = {c: "float64" if c in numeric else "string" for c in header}
stats = {g: {k: np.zeros(len(numeric)) for k in ["count", "sum", "sumsq"]} for g in ["all", "positive", "negative"]}
stats["all"].update(min=np.full(len(numeric), np.inf), max=np.full(len(numeric), -np.inf))
nulls = pd.Series(0, index=header, dtype="int64")
checks = {}
daily_parts, positive_parts = [], []
cell_rows = np.zeros(len(grid), dtype=np.int64)
cell_positives, cell_missing = cell_rows.copy(), cell_rows.copy()
sample, sample_priorities = None, np.empty(0)
rng = np.random.default_rng(SEED)
rows, positive_count, event_count, large_count, duplicate_excess = 0, 0, 0, 0, 0
status_by_label = {(y, m): 0 for y in [0, 1] for m in [0, 1]}
source_values = {c: set() for c in selection["metadata"] if c not in ["grid_id", "ignition_date", "issue_time_utc", "cwfis_observation_date"]}
daily_weather = ["cwfis_temp_c", "cwfis_rh_pct", "cwfis_wind_speed_kmh", "cwfis_precip_mm", "cwfis_ffmc", "cwfis_dmc", "cwfis_dc", "cwfis_isi", "cwfis_bui", "cwfis_fwi", "cwfis_dsr"]
integer_cols = ["ignition_count", "terrain_sample_count", "cwfis_station_count", "cwfis_precip_7d_coverage_days", *[p for p in predictors if p.startswith("fire_history_")]]

def record(name, invalid):
    checks[name] = checks.get(name, 0) + int(np.asarray(invalid).sum())

def aggregate(name, frame):
    vals = frame[numeric]
    stats[name]["count"] += vals.count().to_numpy()
    stats[name]["sum"] += vals.sum().to_numpy()
    stats[name]["sumsq"] += vals.pow(2).sum().to_numpy()

start = time.monotonic()
for part, df in enumerate(pd.read_csv(SOURCE, dtype=dtypes, chunksize=CHUNK_SIZE, keep_default_na=True, na_values=["NULL", "NA", "N/A"], on_bad_lines="error"), 1):
    n = len(df)
    rows += n
    nulls += df.isna().sum()
    y, m = df.ignition, df.cwfis_missing
    pos = y.eq(1)
    positive_count += int(pos.sum())
    event_count += int(df.ignition_count.sum())
    large_count += int(df.large_fire_200ha.sum())
    aggregate("all", df)
    aggregate("positive", df.loc[pos])
    aggregate("negative", df.loc[y.eq(0)])
    stats["all"]["min"] = np.fmin(stats["all"]["min"], df[numeric].min().to_numpy())
    stats["all"]["max"] = np.fmax(stats["all"]["max"], df[numeric].max().to_numpy())
    for c in source_values:
        source_values[c].update(df[c].dropna().unique().tolist())
    gi, di = grid_index.get_indexer(df.grid_id), date_strings.get_indexer(df.ignition_date)
    valid = (gi >= 0) & (di >= 0)
    record("unknown_grid_id_rows", gi < 0)
    record("date_outside_expected_fire_seasons_rows", di < 0)
    keys = di[valid] * len(grid) + gi[valid]
    unique_keys = np.unique(keys)
    duplicate_excess += len(keys) - len(unique_keys) + int(seen[unique_keys].sum())
    seen[unique_keys] = 1
    np.add.at(cell_rows, gi[valid], 1)
    np.add.at(cell_positives, gi[valid], pos.to_numpy()[valid].astype(int))
    np.add.at(cell_missing, gi[valid], m.to_numpy()[valid].astype(int))
    for a in [0, 1]:
        for b in [0, 1]:
            status_by_label[(a, b)] += int((y.eq(a) & m.eq(b)).sum())
    daily_parts.append(df.assign(weather_available=1-m).groupby("ignition_date").agg(rows=("ignition", "size"), positives=("ignition", "sum"), events=("ignition_count", "sum"), large=("large_fire_200ha", "sum"), weather_available=("weather_available", "sum")))
    record("nonbinary_ignition_rows", ~y.isin([0, 1]))
    record("nonbinary_large_fire_rows", ~df.large_fire_200ha.isin([0, 1]))
    record("nonbinary_weather_missing_rows", ~m.isin([0, 1]))
    record("ignition_count_label_mismatch_rows", y.ne(df.ignition_count.gt(0).astype(int)))
    record("large_fire_without_ignition_rows", df.large_fire_200ha.gt(y))
    record("numeric_infinite_values", np.isinf(df[numeric].to_numpy()).sum())
    record("prior_day_observation_date_mismatch_rows", df.cwfis_observation_date.ne(df.ignition_date.map(prior_map)).fillna(True))
    record("local_midnight_issue_time_mismatch_rows", df.issue_time_utc.ne(df.ignition_date.map(issue_map)).fillna(True))
    record("day_of_year_mismatch_rows", df.calendar_day_of_year.ne(df.ignition_date.map(doy_map)))
    record("station_count_outside_0to4_rows", ~df.cwfis_station_count.between(0, 4))
    record("station_coverage_fraction_mismatch_rows", (df.cwfis_observation_coverage_fraction-df.cwfis_station_count/4).abs().gt(1e-6))
    record("terrain_sample_count_outside_0to9_rows", ~df.terrain_sample_count.between(0, 9))
    record("terrain_coverage_fraction_mismatch_rows", (df.terrain_coverage_fraction-df.terrain_sample_count/9).abs().gt(1e-6))
    record("weather_missing_station_count_mismatch_rows", m.ne(df.cwfis_station_count.eq(0).astype(int)))
    record("daily_weather_null_pattern_mismatch_rows", df[daily_weather].isna().ne(m.eq(1), axis=0).any(axis=1))
    record("rain_7d_coverage_outside_0to7_rows", ~df.cwfis_precip_7d_coverage_days.between(0, 7))
    record("rain_7d_null_coverage_mismatch_rows", df.cwfis_precip_7d_mm.isna().ne(df.cwfis_precip_7d_coverage_days.lt(7)))
    record("humidity_outside_0to100_rows", df.cwfis_rh_pct.notna() & ~df.cwfis_rh_pct.between(0, 100))
    record("ffmc_outside_0to101_rows", df.cwfis_ffmc.notna() & ~df.cwfis_ffmc.between(0, 101))
    for c in ["cwfis_wind_speed_kmh", "cwfis_precip_mm", "cwfis_precip_7d_mm", "cwfis_dmc", "cwfis_dc", "cwfis_isi", "cwfis_bui", "cwfis_fwi", "cwfis_dsr"]:
        record(f"{c}_negative_rows", df[c].lt(0))
    for c in integer_cols:
        vals = df[c]
        record(f"{c}_negative_or_noninteger_rows", vals.lt(0) | (vals.notna() & vals.ne(np.floor(vals))))
    record("days_since_fire_nonpositive_observed_rows", df.fire_history_days_since_same_cell.notna() & df.fire_history_days_since_same_cell.le(0))
    if pos.any():
        positive_parts.append(df.loc[pos, ["grid_id", "ignition_date", *numeric]].copy())
    priorities = rng.random(n)
    if len(sample_priorities) == SAMPLE_SIZE:
        keep = priorities < sample_priorities.max()
        candidate = df.loc[keep, ["grid_id", "ignition_date", *numeric]]
        priorities = priorities[keep]
    else:
        candidate = df[["grid_id", "ignition_date", *numeric]]
    combined = pd.concat([sample, candidate], ignore_index=True) if sample is not None else candidate.reset_index(drop=True)
    combined_priorities = np.concatenate([sample_priorities, priorities])
    pick = np.argpartition(combined_priorities, SAMPLE_SIZE-1)[:SAMPLE_SIZE] if len(combined) > SAMPLE_SIZE else np.arange(len(combined))
    sample = combined.iloc[pick].reset_index(drop=True)
    sample_priorities = combined_priorities[pick]
    if part % 5 == 0:
        print(f'PROGRESS rows={rows:,}/{expected_rows:,}; positives={positive_count:,}; elapsed={time.monotonic()-start:.0f}s', flush=True)

daily = pd.concat(daily_parts).groupby(level=0).sum().sort_index()
daily.index.name = "ignition_date"
daily["year"] = daily.index.str[:4].astype(int)
daily["month"] = daily.index.str[5:7].astype(int)
daily["ignition_rate"] = daily.positives / daily.rows
daily["weather_missing_fraction"] = 1-daily.weather_available/daily.rows
daily.to_csv(OUT / "daily_summary.csv")
annual = daily.groupby("year")[["rows", "positives", "events", "large", "weather_available"]].sum()
annual["ignition_rate"] = annual.positives / annual.rows
annual["weather_missing_fraction"] = 1-annual.weather_available/annual.rows
annual.to_csv(OUT / "annual_summary.csv")
monthly = daily.groupby("month")[["rows", "positives", "events", "weather_available"]].sum()
monthly["ignition_rate"] = monthly.positives/monthly.rows
monthly["weather_missing_fraction"] = 1-monthly.weather_available/monthly.rows
monthly.to_csv(OUT / "monthly_summary.csv")
grid["rows"] = cell_rows
grid["positive_cell_days"] = cell_positives
grid["weather_missing_fraction"] = cell_missing / np.maximum(cell_rows, 1)
grid.to_csv(OUT / "cell_summary.csv", index=False)
sample = sample.sort_values(["ignition_date", "grid_id"])
sample.to_csv(OUT / "uniform_sample_100k.csv.gz", index=False, compression="gzip")
positives = pd.concat(positive_parts, ignore_index=True)
positives.to_csv(OUT / "positive_cell_days.csv.gz", index=False, compression="gzip")
missing = pd.DataFrame({"column": header, "missing_rows": nulls.to_numpy(), "missing_pct": 100*nulls.to_numpy()/rows})
missing.to_csv(OUT / "missingness.csv", index=False)
feature_stats = []
quantiles = sample[numeric].quantile([.01,.25,.5,.75,.99])
for i,c in enumerate(numeric):
    count = stats["all"]["count"][i]
    mean = stats["all"]["sum"][i] / max(count, 1)
    entry = {"column": c, "observed_rows": int(count), "missing_pct": 100*(rows-count)/rows, "mean": mean, "std": np.sqrt(max(0,(stats["all"]["sumsq"][i]-count*mean**2)/max(count-1,1))), "min": stats["all"]["min"][i], "max": stats["all"]["max"][i]}
    entry.update({f"sample_p{int(q*100):02d}": quantiles.loc[q,c] for q in quantiles.index})
    for group in ["positive", "negative"]:
        obs = stats[group]["count"][i]
        entry[f"{group}_observed_rows"] = int(obs)
        entry[f"{group}_mean"] = stats[group]["sum"][i] / obs if obs else None
    feature_stats.append(entry)
pd.DataFrame(feature_stats).to_csv(OUT / "feature_statistics.csv", index=False)
correlations = sample[predictors].corr(min_periods=1000)
correlations.to_csv(OUT / "sample_feature_correlations.csv")
pairs = [{"feature_a": a,"feature_b": b,"pearson_r": correlations.loc[a,b], "pairwise_sample_rows": int(sample[[a,b]].notna().all(axis=1).sum())} for i,a in enumerate(predictors) for b in predictors[i+1:] if np.isfinite(correlations.loc[a,b]) and abs(correlations.loc[a,b]) >= .9]
pd.DataFrame(pairs).sort_values("pearson_r", key=abs, ascending=False).to_csv(OUT / "strong_correlations.csv", index=False)
checks.update(duplicate_key_excess_rows=duplicate_excess, expected_keys_missing=int((seen==0).sum()), row_count_mismatch=abs(rows-expected_rows), dates_with_incorrect_cell_count=int(daily.rows.ne(len(grid)).sum()), cells_with_incorrect_date_count=int((cell_rows!=len(dates)).sum()))
summary = {"source_file": SOURCE.name, "source_bytes": SOURCE.stat().st_size, "rows": rows, "columns": len(header), "grid_cells": len(grid), "dates": len(daily), "date_start": str(daily.index.min()), "date_end": str(daily.index.max()), "positive_cell_days": positive_count, "event_records": event_count, "large_fire_positive_cell_days": large_count, "ignition_prevalence_pct": 100*positive_count/rows, "all_negative_accuracy_pct": 100*(1-positive_count/rows), "negative_to_positive_ratio": (rows-positive_count)/positive_count, "weather_label_counts": [{"ignition":y,"cwfis_missing":m,"rows":n} for (y,m),n in status_by_label.items()], "unique_metadata_values": {c:sorted(v) for c,v in source_values.items()}, "validation_violation_counts": checks, "uniform_sample_rows": len(sample), "uniform_sample_positive_rows": int(sample.ignition.sum()), "sample_seed": SEED, "method": "Every row scanned. Counts, missingness, means, ranges, cell/date summaries and listed checks are exact; quantiles and Pearson correlations use a uniform priority sample. All positive cell-days retained for distribution plots. No rows altered, imputation, training or source-record revalidation.", "library_versions": {"pandas": pd.__version__, "numpy": np.__version__}, "scan_seconds": round(time.monotonic()-start,2)}
(OUT / "eda_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
print(json.dumps({k:v for k,v in summary.items() if k not in ["validation_violation_counts","unique_metadata_values"]}, indent=2), flush=True)
print("CHECK_VIOLATIONS", {k:v for k,v in checks.items() if v}, flush=True)
