#!/usr/bin/env python3
"""Download and checksum the CWFIS 2010s long/latitude FWI archive.

The archive is deliberately kept out of git because it is a source download.
This command records the exact checksum that the feature build consumed so a
changed upstream archive cannot silently alter a retrospective experiment.
"""

from __future__ import annotations

import argparse
import bz2
import csv
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
URL = "https://cwfis.cfs.nrcan.gc.ca/downloads/fwi_obs/cwfis_fwi2010sv3.0_ll.csv.bz2"
EXPECTED_SHA256 = "67e706160316e2b1e496bc3b316d17d5c1623954f29231d5cb71e618f0329246"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/raw/cwfis/cwfis_fwi2010sv3.0_ll.csv.bz2"
DEFAULT_MANIFEST = PROJECT_ROOT / "data/raw/cwfis/manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect(path: Path) -> dict[str, object]:
    required = {"rep_date", "aes", "wmo", "lon", "lat", "temp", "rh", "ws", "precip", "ffmc", "dmc", "dc", "isi", "bui", "fwi", "dsr", "opts", "calcstatus"}
    row_count = 0
    first_date: str | None = None
    last_date: str | None = None
    with bz2.open(path, "rt", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"CWFIS archive schema is missing fields: {sorted(required - set(reader.fieldnames or []))}")
        for row in reader:
            date_text = (row["rep_date"] or "")[:10]
            if len(date_text) != 10:
                raise ValueError(f"Invalid CWFIS rep_date: {row['rep_date']!r}")
            first_date = date_text if first_date is None else min(first_date, date_text)
            last_date = date_text if last_date is None else max(last_date, date_text)
            row_count += 1
    return {"rows": row_count, "first_observation_date": first_date, "last_observation_date": last_date}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--allow-source-change", action="store_true", help="Record a new checksum after consciously reviewing an upstream revision.")
    parser.add_argument("--use-existing", action="store_true", help="Validate and manifest an already downloaded archive without another network request.")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    if args.use_existing:
        if not args.output.exists():
            raise FileNotFoundError(f"No archive exists at {args.output}; omit --use-existing to download it.")
        downloaded_sha256 = sha256(args.output)
    else:
        with tempfile.NamedTemporaryFile("wb", dir=args.output.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            request = Request(URL, headers={"User-Agent": "COMP-385-ignition-feature-builder/1.0 (+educational-research)"})
            with urlopen(request, timeout=180) as response:  # noqa: S310 - fixed public CWFIS URL
                shutil.copyfileobj(response, temporary)
        downloaded_sha256 = sha256(temporary_path)
    if downloaded_sha256 != EXPECTED_SHA256 and not args.allow_source_change:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise RuntimeError(
            "CWFIS source checksum changed. Review the upstream revision and rerun with --allow-source-change; "
            f"expected {EXPECTED_SHA256}, got {downloaded_sha256}."
        )
    if temporary_path is not None:
        os.replace(temporary_path, args.output)
    profile = inspect(args.output)
    manifest = {
        "source_id": "cwfis_fwi_2010s_v3_ll",
        "source_url": URL,
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "archive_filename": args.output.name,
        "archive_sha256": downloaded_sha256,
        "expected_sha256": EXPECTED_SHA256,
        "source_change_accepted": downloaded_sha256 != EXPECTED_SHA256,
        "profile": profile,
        "availability_semantics": {
            "publication_or_available_at_per_record": None,
            "use": "retrospective hindcast only",
            "operational_eligible": False,
            "feature_rule": "The feature builder uses only the source record dated t-1 for issue date t.",
        },
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    print(f"Wrote source manifest: {args.manifest}")


if __name__ == "__main__":
    main()
