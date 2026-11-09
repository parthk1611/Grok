"""Guards the Ontario Fire Region source revision (shapefile -> File Geodatabase, T1.0).

Run: python -m unittest tests.test_fire_region_source_revision -v
"""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/raw/ontario_fire_region"
GRID = ROOT / "data/interim/ontario_fire_region_10km_grid.csv"
EVENTS = ROOT / "data/interim/ontario_nfdb_ignitions_2010_2019.csv"
GEOJSON = RAW / "fire_response_plan_sectors.geojson"

OLD_ARCHIVE = "1368d14e5faa3503ce4c8d2de20da553e9dffcc74e1309e24378e711ffa8f4a9"
NEW_ARCHIVE = "b0a551e3b1cf49dcbde4bbbc366e044f70e4c461b0bb1002c521b90f89e7934c"
GRID_SHA = "b22741d83b64c2b78c19fbf182848566644a23fad4aeef3301ff25ef7c6c3f35"


def _load_build_labels():
    module_path = ROOT / "scripts/build_cpa_daily_ignition_labels.py"
    spec = importlib.util.spec_from_file_location("build_cpa_daily_ignition_labels", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FireRegionSourceRevisionTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((RAW / "fire_response_plan_sectors_manifest.json").read_text())

    def test_manifest_records_new_archive_and_old_revision(self):
        self.assertEqual(self.manifest["archive_sha256"], NEW_ARCHIVE)
        self.assertEqual(self.manifest["feature_count"], 15)
        previous = {r["archive_sha256"]: r for r in self.manifest["previous_revisions"]}
        self.assertIn(OLD_ARCHIVE, previous)
        self.assertEqual(previous[OLD_ARCHIVE]["feature_count"], 15)

    def test_tracked_grid_hash_guard(self):
        """Guard only: asserts the committed grid CSV still has the pinned sha256.

        This does not rebuild the grid. See test_rebuilt_grid_matches_tracked_sha256
        for the rebuild check against the GeoJSON + event inputs.
        """
        self.assertEqual(hashlib.sha256(GRID.read_bytes()).hexdigest(), GRID_SHA)
        self.assertEqual(len(GRID.read_text().splitlines()) - 1, 9673)

    def test_rebuilt_grid_matches_tracked_sha256(self):
        """Rebuild the 10 km grid from GeoJSON + NFDB events and match the tracked CSV."""
        if not GEOJSON.exists():
            self.skipTest(
                "fire_response_plan_sectors.geojson absent "
                "(run scripts/download_ontario_fire_region.py)"
            )
        if not EVENTS.exists():
            self.skipTest(
                "ontario_nfdb_ignitions_2010_2019.csv absent "
                "(run scripts/prepare_ontario_ignitions.py)"
            )
        if not GRID.exists():
            self.skipTest("tracked ontario_fire_region_10km_grid.csv absent")

        build = _load_build_labels()
        rebuilt_bytes, rebuilt_cells = build.rebuild_grid_csv_bytes(GEOJSON, EVENTS)
        tracked_bytes = GRID.read_bytes()
        self.assertEqual(hashlib.sha256(rebuilt_bytes).hexdigest(), GRID_SHA)
        self.assertEqual(hashlib.sha256(tracked_bytes).hexdigest(), GRID_SHA)
        self.assertEqual(rebuilt_bytes, tracked_bytes)
        self.assertEqual(rebuilt_cells, 9673)

    def test_local_geojson_matches_manifest_when_present(self):
        geojson = GEOJSON
        if not geojson.exists():
            self.skipTest("fire_response_plan_sectors.geojson not built (run scripts/download_ontario_fire_region.py)")
        data = geojson.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.manifest["geojson_sha256"])
        features = json.loads(data)["features"]
        self.assertEqual(len(features), self.manifest["feature_count"])
        self.assertNotIn("OFR", {f["properties"]["RESPONSE_SECTOR"] for f in features})

    def test_manifest_grid_equivalence_is_computed(self):
        equivalence = self.manifest.get("grid_equivalence")
        self.assertIsInstance(equivalence, dict)
        self.assertEqual(equivalence.get("status"), "checked")
        self.assertTrue(equivalence.get("byte_identical"))
        self.assertEqual(equivalence.get("rebuilt_sha256"), GRID_SHA)
        self.assertEqual(equivalence.get("tracked_sha256"), GRID_SHA)
        self.assertEqual(equivalence.get("rebuilt_cell_count"), 9673)


if __name__ == "__main__":
    unittest.main()
