#!/usr/bin/env python3
"""Download and safely extract the official NFDB point-text archive."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path


URL = (
    "https://cwfis.cfs.nrcan.gc.ca/downloads/nfdb/fire_pnt/"
    "current_version/NFDB_point_txt.zip"
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESTINATION = PROJECT_ROOT / "data" / "raw" / "nfdb"
ARCHIVE = DESTINATION / "NFDB_point_txt.zip"
EXTRACTED = DESTINATION / "extracted"
MANIFEST = DESTINATION / "manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> list[str]:
    destination_root = destination.resolve()
    members = archive.infolist()
    for member in members:
        member_path = (destination / member.filename).resolve()
        if not member_path.is_relative_to(destination_root):
            raise ValueError(f"Unsafe archive member: {member.filename}")
    archive.extractall(destination)
    return [member.filename for member in members if not member.is_dir()]


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    if not ARCHIVE.exists():
        print(f"Downloading {URL}")
        request = urllib.request.Request(URL, headers={"User-Agent": "ai-capstone/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response, ARCHIVE.open("wb") as output:
            shutil.copyfileobj(response, output)
    else:
        print(f"Using existing archive: {ARCHIVE}")

    if EXTRACTED.exists():
        shutil.rmtree(EXTRACTED)
    EXTRACTED.mkdir()
    with zipfile.ZipFile(ARCHIVE) as archive:
        files = safe_extract(archive, EXTRACTED)

    manifest = {
        "source_name": "Canadian National Fire Database (NFDB) point data",
        "source_url": URL,
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "archive_filename": ARCHIVE.name,
        "archive_sha256": sha256(ARCHIVE),
        "extracted_files": files,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(files)} files to {EXTRACTED}")
    print(f"Wrote manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
