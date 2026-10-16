#!/usr/bin/env python3
"""Download Ontario's Fire Disturbance Point and Area layers.

Step 1 of the fire-spread build.  Both layers come from the Ontario GeoHub map
service (Open Government Licence – Ontario) as GeoJSON, page by page.  The
point layer has one record per fire with its reported location; the area layer
has the final perimeter polygon of the larger fires.  Files are saved exactly
as served; cleaning happens in `build_spread_fire_catalogue.py`.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from spread_common import RAW, USER_AGENT, action_log, sha256


SERVICE = "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/LIO_Open09/MapServer"
LAYERS = {
    "point": {"id": 30, "page_size": 2000, "source_page": "https://geohub.lio.gov.on.ca/datasets/lio::fire-disturbance-point/about"},
    "area": {"id": 28, "page_size": 200, "source_page": "https://geohub.lio.gov.on.ca/datasets/lio::fire-disturbance-area/about"},
}
DESTINATION = RAW / "fire_disturbance"
MANIFEST = DESTINATION / "manifest.json"


def fetch_json(url: str, retries: int = 4) -> dict[str, object]:
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=300) as response:  # noqa: S310 - fixed public Ontario URL
                return json.load(response)
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))
    raise AssertionError("unreachable")


def download_layer(name: str, log) -> dict[str, object]:
    layer = LAYERS[name]
    base = f"{SERVICE}/{layer['id']}/query"
    expected = fetch_json(f"{base}?{urlencode({'where': '1=1', 'returnCountOnly': 'true', 'f': 'json'})}")["count"]
    features: list[dict[str, object]] = []
    while len(features) < expected:
        page = fetch_json(
            f"{base}?"
            + urlencode(
                {
                    "where": "1=1",
                    "outFields": "*",
                    "orderByFields": "OBJECTID",
                    "resultOffset": len(features),
                    "resultRecordCount": layer["page_size"],
                    "outSR": 4326,
                    "f": "geojson",
                }
            )
        )
        if not page.get("features"):
            break
        features.extend(page["features"])
        if len(features) % (layer["page_size"] * 5) == 0:
            log.info(f"{name} layer: {len(features):,} of {expected:,} records received")
    if len(features) != expected:
        raise RuntimeError(f"{name} layer: expected {expected} records, received {len(features)}")
    identifiers = [feature["properties"]["OBJECTID"] for feature in features]
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError(f"{name} layer: paging returned duplicate OBJECTID values")
    path = DESTINATION / f"fire_disturbance_{name}.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")
    years = [feature["properties"]["FIRE_YEAR"] for feature in features if feature["properties"].get("FIRE_YEAR")]
    entry = {
        "layer": name,
        "source_page": layer["source_page"],
        "service_url": f"{SERVICE}/{layer['id']}",
        "filename": path.name,
        "records": len(features),
        "fire_years": [min(years), max(years)],
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    log.info(f"downloaded {name} layer: {entry['records']:,} records, fire years {entry['fire_years'][0]}-{entry['fire_years'][1]}, {entry['bytes'] / 1_048_576:.1f} MB -> {path.name}")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=sorted(LAYERS), action="append", help="Download only this layer; repeatable. Default is both.")
    args = parser.parse_args()
    log = action_log("1 download fire disturbance")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    log.info(f"START: Ontario Fire Disturbance layers from {SERVICE}")
    entries = [download_layer(name, log) for name in (args.layer or ["point", "area"])]
    previous = json.loads(MANIFEST.read_text(encoding="utf-8")).get("layers", []) if MANIFEST.exists() else []
    kept = [entry for entry in previous if entry["layer"] not in {new["layer"] for new in entries}]
    manifest = {
        "source_name": "Ontario Fire Disturbance Point and Fire Disturbance Area",
        "publisher": "Ontario Ministry of Natural Resources, Land Information Ontario",
        "license": "Open Government Licence – Ontario",
        "coordinate_system": "EPSG:4326 as served (source datum NAD83)",
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "layers": sorted(kept + entries, key=lambda entry: entry["layer"]),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log.info(f"DONE: wrote manifest {MANIFEST.name}")


if __name__ == "__main__":
    main()
