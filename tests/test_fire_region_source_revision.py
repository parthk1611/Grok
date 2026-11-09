"""Guards the Ontario Fire Region source revision (shapefile -> File Geodatabase, T1.0).

Run: python -m unittest tests.test_fire_region_source_revision -v
"""
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/raw/ontario_fire_region"
GRID = ROOT / "data/interim/ontario_fire_region_10km_grid.csv"

OLD_ARCHIVE = "1368d14e5faa3503ce4c8d2de20da553e9dffcc74e1309e24378e711ffa8f4a9"
NEW_ARCHIVE = "b0a551e3b1cf49dcbde4bbbc366e044f70e4c461b0bb1002c521b90f89e7934c"
GRID_SHA = "b22741d83b64c2b78c19fbf182848566644a23fad4aeef3301ff25ef7c6c3f35"


class FireRegionSourceRevisionTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((RAW / "fire_response_plan_sectors_manifest.json").read_text())

    def test_manifest_records_new_archive_and_old_revision(self):
        self.assertEqual(self.manifest["archive_sha256"], NEW_ARCHIVE)
        self.assertEqual(self.manifest["feature_count"], 15)
        previous = {r["archive_sha256"]: r for r in self.manifest["previous_revisions"]}
        self.assertIn(OLD_ARCHIVE, previous)
        self.assertEqual(previous[OLD_ARCHIVE]["feature_count"], 15)

    def test_tracked_grid_is_unchanged_across_source_revisions(self):
        self.assertEqual(hashlib.sha256(GRID.read_bytes()).hexdigest(), GRID_SHA)
        self.assertEqual(len(GRID.read_text().splitlines()) - 1, 9673)

    def test_local_geojson_matches_manifest_when_present(self):
        geojson = RAW / "fire_response_plan_sectors.geojson"
        if not geojson.exists():
            self.skipTest("fire_response_plan_sectors.geojson not built (run scripts/download_ontario_fire_region.py)")
        data = geojson.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.manifest["geojson_sha256"])
        features = json.loads(data)["features"]
        self.assertEqual(len(features), self.manifest["feature_count"])
        self.assertNotIn("OFR", {f["properties"]["RESPONSE_SECTOR"] for f in features})


if __name__ == "__main__":
    unittest.main()
