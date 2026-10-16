"""Base-map images for the viewer, from the Canada Base Map - Transportation service.

Natural Resources Canada draws this map in EPSG:3978, the grid the simulator
runs on, so an image requested for a fire's window lines up with the 90 m
cells without any warping.  Each image is fetched once, kept under
`data/raw/basemap/`, and listed with its request and checksum in the manifest
there.  The map is free to use under the Open Government Licence - Canada and
must be credited wherever it is shown.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from spread_common import RAW, USER_AGENT, sha256


SERVICE = "https://maps.geogratis.gc.ca/wms/CBMT"
LAYER = "CBMT"
CREDIT = "Base map: Natural Resources Canada"
LICENCE = "Open Government Licence - Canada, https://open.canada.ca/en/open-government-licence-canada"
FOLDER = RAW / "basemap"
MANIFEST = FOLDER / "manifest.json"


def _manifest() -> dict[str, dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["images"] if MANIFEST.exists() else {}


def fetch(name: str, west: float, south: float, east: float, north: float, longest_px: int) -> tuple[Path, bool]:
    """The map image for an EPSG:3978 box, and whether it had to be downloaded just now."""
    scale = longest_px / max(east - west, north - south)
    width, height = max(1, round((east - west) * scale)), max(1, round((north - south) * scale))
    request = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": LAYER, "STYLES": "", "SRS": "EPSG:3978",
        "BBOX": f"{west:.1f},{south:.1f},{east:.1f},{north:.1f}", "WIDTH": width, "HEIGHT": height, "FORMAT": "image/png",
    }  # fmt: skip
    url = SERVICE + "?" + urllib.parse.urlencode(request)
    path = FOLDER / f"{name}_cbmt.png"
    images = _manifest()
    if path.exists() and images.get(name, {}).get("url") == url:
        return path, False
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=180) as response:
        kind, content = response.headers.get_content_type(), response.read()
    if kind != "image/png":
        raise RuntimeError(f"the base-map service answered {name} with {kind}, not an image: {content[:200]!r}")
    FOLDER.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    images[name] = {
        "filename": path.name, "url": url, "crs": "EPSG:3978", "west": west, "south": south, "east": east, "north": north,
        "width": width, "height": height, "bytes": len(content), "sha256": sha256(path), "downloaded_at_utc": datetime.now(UTC).isoformat(),
    }  # fmt: skip
    MANIFEST.write_text(json.dumps({"source": SERVICE, "layer": LAYER, "licence": LICENCE, "images": dict(sorted(images.items()))}, indent=2) + "\n", encoding="utf-8")
    return path, True


def data_uri(path: Path, quality: int = 80) -> str:
    """The image re-encoded small enough to write into the viewer page."""
    from PIL import Image

    packed = io.BytesIO()
    Image.open(path).convert("RGB").save(packed, "WEBP", quality=quality, method=6)
    return "data:image/webp;base64," + base64.b64encode(packed.getvalue()).decode("ascii")
