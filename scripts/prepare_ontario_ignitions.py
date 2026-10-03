#!/usr/bin/env python3
"""Create an audited Ontario NFDB ignition-label table for 2010–2019."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT = PROJECT_ROOT / "data/raw/nfdb/extracted/NFDB_point_20260811.txt"
OUTPUT = PROJECT_ROOT / "data/interim/ontario_nfdb_ignitions_2010_2019.csv"
AUDIT = PROJECT_ROOT / "data/interim/ontario_nfdb_ignitions_2010_2019_audit.json"
START_YEAR, END_YEAR = 2010, 2019
OUTPUT_COLUMNS = [
    "nfdb_fire_id",
    "fire_id",
    "ignition_date",
    "year",
    "month",
    "day",
    "latitude",
    "longitude",
    "size_ha",
    "large_fire_200ha",
    "cause",
    "cause_detail",
    "fire_type",
    "response",
    "protection_zone",
    "is_prescribed",
]


def text(row: dict[str, str], field: str) -> str:
    return (row.get(field) or "").strip()


def main() -> None:
    if not INPUT.exists():
        raise FileNotFoundError(f"NFDB source is missing: {INPUT}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    audit: Counter[str] = Counter()
    by_year: Counter[str] = Counter()
    by_cause: Counter[str] = Counter()
    invalid_examples: list[dict[str, str]] = []

    with INPUT.open("r", encoding="utf-8-sig", newline="") as source, OUTPUT.open(
        "w", encoding="utf-8", newline=""
    ) as destination:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError("NFDB file has no header.")
        required = {"NFDBFIREID", "SRC_AGENCY", "YEAR", "MONTH", "DAY", "LATITUDE", "LONGITUDE"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"NFDB schema is missing expected fields: {sorted(missing)}")

        writer = csv.DictWriter(destination, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in reader:
            audit["source_rows"] += 1
            if text(row, "SRC_AGENCY") != "ON":
                continue
            audit["ontario_rows"] += 1

            try:
                year, month, day = (int(text(row, field)) for field in ("YEAR", "MONTH", "DAY"))
                ignition_date = date(year, month, day)
                latitude, longitude = float(text(row, "LATITUDE")), float(text(row, "LONGITUDE"))
            except (TypeError, ValueError):
                audit["excluded_invalid_date_or_coordinates"] += 1
                if len(invalid_examples) < 10:
                    invalid_examples.append(
                        {
                            "nfdb_fire_id": text(row, "NFDBFIREID"),
                            "year": text(row, "YEAR"),
                            "month": text(row, "MONTH"),
                            "day": text(row, "DAY"),
                            "latitude": text(row, "LATITUDE"),
                            "longitude": text(row, "LONGITUDE"),
                        }
                    )
                continue

            if not START_YEAR <= year <= END_YEAR:
                audit["excluded_outside_study_years"] += 1
                continue
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                audit["excluded_out_of_range_coordinates"] += 1
                continue

            size_text = text(row, "SIZE_HA")
            try:
                size_ha = float(size_text) if size_text else None
            except ValueError:
                size_ha = None
                audit["invalid_size_ha"] += 1

            cause = text(row, "CAUSE") or "UNKNOWN"
            writer.writerow(
                {
                    "nfdb_fire_id": text(row, "NFDBFIREID"),
                    "fire_id": text(row, "FIRE_ID"),
                    "ignition_date": ignition_date.isoformat(),
                    "year": year,
                    "month": month,
                    "day": day,
                    "latitude": f"{latitude:.6f}",
                    "longitude": f"{longitude:.6f}",
                    "size_ha": "" if size_ha is None else size_ha,
                    "large_fire_200ha": "" if size_ha is None else int(size_ha >= 200),
                    "cause": cause,
                    "cause_detail": text(row, "CAUSE2"),
                    "fire_type": text(row, "FIRE_TYPE"),
                    "response": text(row, "RESPONSE"),
                    "protection_zone": text(row, "PROTZONE"),
                    "is_prescribed": text(row, "PRESCRIBED"),
                }
            )
            audit["included_rows"] += 1
            by_year[str(year)] += 1
            by_cause[cause] += 1

    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": str(INPUT.relative_to(PROJECT_ROOT)),
        "selection": {
            "source_agency": "ON",
            "years_inclusive": [START_YEAR, END_YEAR],
            "requires_valid_calendar_date_and_wgs84_coordinates": True,
        },
        "counts": dict(audit),
        "included_fires_by_year": dict(sorted(by_year.items())),
        "included_fires_by_cause": dict(sorted(by_cause.items())),
        "invalid_record_examples": invalid_examples,
        "notes": [
            "This is an event table, not yet a grid-by-day modelling dataset.",
            "NFDB point positions have variable precision and must not be treated as exact ignition coordinates.",
            "The Ontario Crown Protection Area boundary filter is a later spatial processing step.",
        ],
    }
    AUDIT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {audit['included_rows']} Ontario fire events: {OUTPUT}")
    print(f"Wrote audit report: {AUDIT}")


if __name__ == "__main__":
    main()
