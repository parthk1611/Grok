#!/usr/bin/env python3
"""Measure MSC Datamart HRDPS GRIB2 publication lag (ATLAS T1.1).

Publication lag = Apache directory Last-modified of a lead-001 GRIB2 file
minus the model run time encoded in the filename (YYYYMMDDTHHZ).

This script does **not** re-poll GeoMet / CWFIS / SWOB. Those feeds are covered
by RELAY's probe. If RELAY's evidence file is present (default
``/workspace/relay-evidence/feed_cadence.jsonl``), it is read and summarised
for context only.

Examples::

  python scripts/data_sources/measure_live_cadence.py
  python scripts/data_sources/measure_live_cadence.py --json-out /tmp/lag.json

Stdlib only. Failures are recorded; nothing is fabricated.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

USER_AGENT = "COMP-385-ATLAS-measure-live-cadence/1.0 (research; T1.1)"
TIMEOUT_S = 30
DEFAULT_DATAMART_BASE = (
    "https://dd.weather.gc.ca/today/model_hrdps/continental/2.5km/"
)
DEFAULT_RELAY_EVIDENCE = Path("/workspace/relay-evidence/feed_cadence.jsonl")
# Variables used for lag: wind is the live contract field; TMP is a second check.
TARGET_VARS = (
    "WIND_AGL-10m",
    "TMP_AGL-2m",
)
LEAD = "001"
RUN_HREF_RE = re.compile(r'href="(\d{2})/"')
RUN_MTIME_RE = re.compile(
    r'href="(\d{2})/"[^<]*</a>\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})'
)
GRIB_ROW_RE = re.compile(
    r'href="([^"]+\.grib2)"[^<]*</a>\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(\S+)'
)
RUN_IN_NAME_RE = re.compile(r"^(\d{8})T(\d{2})Z_")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str) -> dict[str, Any]:
    """Return status/body/latency/error; never raises."""
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read()
            return {
                "url": url,
                "http_status": resp.status,
                "latency_s": round(time.monotonic() - t0, 3),
                "content_type": resp.headers.get("Content-Type"),
                "bytes": len(body),
                "body": body,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {
            "url": url,
            "http_status": exc.code,
            "latency_s": round(time.monotonic() - t0, 3),
            "content_type": None,
            "bytes": 0,
            "body": None,
            "error": f"HTTPError {exc.code}",
        }
    except Exception as exc:  # timeout, DNS, TLS, ...
        return {
            "url": url,
            "http_status": None,
            "latency_s": round(time.monotonic() - t0, 3),
            "content_type": None,
            "bytes": 0,
            "body": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def parse_run_listing(html: str) -> list[dict[str, str]]:
    """Parse Datamart run-hour folders (00/06/12/18) and their dir mtimes."""
    mtimes = {hh: mt for hh, mt in RUN_MTIME_RE.findall(html)}
    runs = sorted(set(RUN_HREF_RE.findall(html)))
    return [{"run_hh": hh, "dir_mtime_utc": mtimes.get(hh)} for hh in runs]


def parse_grib_rows(html: str) -> list[tuple[str, str, str]]:
    """Return (filename, mtime_utc, size_token) rows from an Apache index."""
    return [(m.group(1), m.group(2), m.group(3)) for m in GRIB_ROW_RE.finditer(html)]


def run_time_from_filename(name: str) -> datetime | None:
    match = RUN_IN_NAME_RE.match(name)
    if not match:
        return None
    return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H").replace(
        tzinfo=timezone.utc
    )


def parse_mtime(token: str) -> datetime:
    return datetime.strptime(token, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def lag_minutes(published: datetime, run_time: datetime) -> float:
    return round((published - run_time).total_seconds() / 60.0, 1)


def measure_datamart(base_url: str) -> dict[str, Any]:
    """Query Datamart indexes and compute per-run publication lag for lead 001."""
    measured_at = iso_z(utc_now())
    out: dict[str, Any] = {
        "source": "msc_datamart_hrdps_continental_2.5km",
        "base_url": base_url,
        "lead": LEAD,
        "measured_at_utc": measured_at,
        "runs": [],
        "summary": None,
        "error": None,
    }
    listing = http_get(base_url)
    out["run_listing"] = {
        k: listing[k] for k in ("url", "http_status", "latency_s", "content_type", "bytes", "error")
    }
    if listing["body"] is None:
        out["error"] = listing["error"] or f"unreachable status={listing['http_status']}"
        return out

    html = listing["body"].decode("utf-8", "replace")
    runs = parse_run_listing(html)
    if not runs:
        out["error"] = "no run-hour folders parsed from Datamart index"
        return out

    wind_lags: list[float] = []
    earliest_lags: list[float] = []

    for run in runs:
        hh = run["run_hh"]
        lead_url = f"{base_url}{hh}/{LEAD}/"
        lead = http_get(lead_url)
        entry: dict[str, Any] = {
            "run_hh": hh,
            "dir_mtime_utc": run.get("dir_mtime_utc"),
            "lead_url": lead_url,
            "http_status": lead["http_status"],
            "latency_s": lead["latency_s"],
            "error": lead["error"],
            "variables": {},
            "earliest_grib2": None,
        }
        if lead["body"] is None:
            out["runs"].append(entry)
            continue
        rows = parse_grib_rows(lead["body"].decode("utf-8", "replace"))
        entry["n_grib2"] = len(rows)
        if rows:
            earliest_name, earliest_mt, earliest_sz = min(rows, key=lambda r: r[1])
            rt = run_time_from_filename(earliest_name)
            pub = parse_mtime(earliest_mt)
            earliest_info = {
                "file": earliest_name,
                "mtime_utc": earliest_mt.replace(" ", "T") + ":00Z",
                "size_token": earliest_sz,
                "run_time_utc": iso_z(rt) if rt else None,
                "publication_lag_min": lag_minutes(pub, rt) if rt else None,
            }
            entry["earliest_grib2"] = earliest_info
            if earliest_info["publication_lag_min"] is not None:
                earliest_lags.append(earliest_info["publication_lag_min"])

        for var in TARGET_VARS:
            match = next((r for r in rows if var in r[0]), None)
            if match is None:
                entry["variables"][var] = {"error": "file not found in lead listing"}
                continue
            name, mt, size = match
            rt = run_time_from_filename(name)
            pub = parse_mtime(mt)
            var_info = {
                "file": name,
                "mtime_utc": mt.replace(" ", "T") + ":00Z",
                "size_token": size,
                "run_time_utc": iso_z(rt) if rt else None,
                "publication_lag_min": lag_minutes(pub, rt) if rt else None,
            }
            if rt is None:
                var_info["error"] = "could not parse run time from filename"
            entry["variables"][var] = var_info
            if var == "WIND_AGL-10m" and var_info["publication_lag_min"] is not None:
                wind_lags.append(var_info["publication_lag_min"])
        out["runs"].append(entry)

    if wind_lags:
        out["summary"] = {
            "wind_lead001_publication_lag_min": {
                "n": len(wind_lags),
                "min": min(wind_lags),
                "max": max(wind_lags),
                "mean": round(sum(wind_lags) / len(wind_lags), 1),
                "values": wind_lags,
            },
            "earliest_any_grib2_publication_lag_min": {
                "n": len(earliest_lags),
                "min": min(earliest_lags) if earliest_lags else None,
                "max": max(earliest_lags) if earliest_lags else None,
                "mean": round(sum(earliest_lags) / len(earliest_lags), 1) if earliest_lags else None,
                "values": earliest_lags,
            },
            "note": (
                "Lag is Apache Last-modified minus run time in the filename. "
                "Minute resolution only (listing has no seconds). "
                "Lead 001 is the first forecast hour; later leads publish later."
            ),
        }
    elif out["error"] is None:
        out["error"] = "no WIND_AGL-10m lead-001 files measured"
    return out


def summarise_relay_evidence(path: Path) -> dict[str, Any]:
    """Read RELAY feed_cadence.jsonl if present; do not poll those feeds again."""
    out: dict[str, Any] = {
        "path": str(path),
        "present": path.is_file(),
        "error": None,
        "n_lines": 0,
        "hrdps_wms": None,
        "note": (
            "RELAY polls GeoMet WMS / CWFIS / SWOB. This file is read only; "
            "ATLAS does not run a second poller for those feeds."
        ),
    }
    if not path.is_file():
        out["error"] = "file not present"
        return out
    ages_by_run: dict[str, list[float]] = {}
    first_seen: dict[str, dict[str, Any]] = {}
    span: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            out["n_lines"] += 1
            started = row.get("iteration_started_at")
            if started:
                span.append(started)
            h = row.get("hrdps_wms") or {}
            lr = h.get("latest_reference_time")
            age = h.get("latest_run_age_min")
            if lr and age is not None and h.get("error") is None:
                ages_by_run.setdefault(lr, []).append(float(age))
                if lr not in first_seen:
                    first_seen[lr] = {
                        "fetched_at": h.get("fetched_at"),
                        "latest_run_age_min": age,
                        "reference_time_extent": h.get("reference_time_extent"),
                    }
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    per_run = []
    for run, ages in sorted(ages_by_run.items()):
        per_run.append(
            {
                "latest_reference_time": run,
                "n_samples": len(ages),
                "min_latest_run_age_min": min(ages),
                "max_latest_run_age_min": max(ages),
                "first_seen": first_seen.get(run),
            }
        )
    out["hrdps_wms"] = {
        "iterations_with_timestamp": [span[0], span[-1]] if span else None,
        "n_distinct_latest_runs": len(per_run),
        "per_run": per_run,
        "note": (
            "latest_run_age_min is serving_time - GeoMet reference_time, not "
            "Datamart GRIB2 publication lag. Sampling is sparse, so the minimum "
            "age is an upper bound on GeoMet appearance lag, not a precise publish time."
        ),
    }
    return out


def build_report(datamart_base: str, relay_path: Path) -> dict[str, Any]:
    return {
        "task": "T1.1",
        "measured_at_utc": iso_z(utc_now()),
        "datamart_grib2_publication_lag": measure_datamart(datamart_base),
        "relay_feed_cadence_evidence": summarise_relay_evidence(relay_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datamart-base",
        default=DEFAULT_DATAMART_BASE,
        help="Datamart HRDPS continental 2.5km 'today' index URL",
    )
    parser.add_argument(
        "--relay-evidence",
        type=Path,
        default=DEFAULT_RELAY_EVIDENCE,
        help="RELAY feed_cadence.jsonl path (read if present; never re-polled)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write the full JSON report",
    )
    args = parser.parse_args(argv)
    report = build_report(args.datamart_base, args.relay_evidence)
    text = json.dumps(report, indent=2, sort_keys=False)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)

    dm = report["datamart_grib2_publication_lag"]
    if dm.get("error") and not dm.get("summary"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
