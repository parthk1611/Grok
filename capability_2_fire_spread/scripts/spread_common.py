"""Shared paths and the action log for the fire-spread dataset build.

Every spread script records what it did, with counts, through `action_log`.
Entries are appended to `logs/fire_spread_build_log.txt` so the whole build can
be read back in order.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path


# Everything for this capability lives under one folder, capability_2_fire_spread/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW = PROJECT_ROOT / "data/raw"
INTERIM = PROJECT_ROOT / "data/interim"
PROCESSED = PROJECT_ROOT / "data/processed"
LOG_FILE = PROJECT_ROOT / "logs/fire_spread_build_log.txt"
USER_AGENT = "COMP-385-spread-dataset-builder/1.0 (+educational-research)"
DATASET_VERSION = "fire-spread-v1.0.0"


def action_log(step: str) -> logging.Logger:
    """A logger that writes `time | step | message` to the console and the build log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"spread.{step}")
    if not logger.handlers:
        formatter = logging.Formatter(f"%(asctime)s | {step} | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        for handler in (logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()):
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def load_perimeters_3978() -> dict[str, object]:
    """Each fire's cleaned final perimeter as a shapely geometry in EPSG:3978 metres."""
    import json

    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform

    to_grid = Transformer.from_crs("EPSG:4326", "EPSG:3978", always_xy=True)
    features = json.loads((INTERIM / "spread_fire_perimeters.geojson").read_text(encoding="utf-8"))["features"]
    return {feature["properties"]["fire_id"]: transform(to_grid.transform, shape(feature["geometry"])) for feature in features}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
