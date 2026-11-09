"""Tests for Datamart publication-lag helpers (ATLAS T1.1). Uses committed fixtures only."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "data_sources" / "measure_live_cadence.py"
FIXTURES = ROOT / "tests" / "fixtures" / "live"


def _load():
    spec = importlib.util.spec_from_file_location("measure_live_cadence", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class MeasureLiveCadenceFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    def test_parse_run_listing_fixture(self):
        html = (FIXTURES / "datamart_hrdps_run_listing.html").read_text(encoding="utf-8")
        runs = self.m.parse_run_listing(html)
        self.assertEqual([r["run_hh"] for r in runs], ["00", "06", "12", "18"])
        eighteen = next(r for r in runs if r["run_hh"] == "18")
        self.assertEqual(eighteen["dir_mtime_utc"], "2026-11-02 20:56")

    def test_wind_lead001_lag_from_fixture(self):
        html = (FIXTURES / "datamart_hrdps_18Z_lead001_listing.html").read_text(encoding="utf-8")
        rows = self.m.parse_grib_rows(html)
        self.assertGreater(len(rows), 100)
        wind = next(r for r in rows if "WIND_AGL-10m" in r[0] and "PT001H" in r[0])
        name, mtime, _size = wind
        run_time = self.m.run_time_from_filename(name)
        pub = self.m.parse_mtime(mtime)
        self.assertEqual(name.startswith("20261102T18Z_"), True)
        self.assertEqual(self.m.iso_z(run_time), "2026-11-02T18:00:00Z")
        self.assertEqual(mtime, "2026-11-02 20:58")
        self.assertEqual(self.m.lag_minutes(pub, run_time), 178.0)

    def test_relay_evidence_missing_is_recorded_not_invented(self):
        summary = self.m.summarise_relay_evidence(Path("/nonexistent/feed_cadence.jsonl"))
        self.assertFalse(summary["present"])
        self.assertEqual(summary["n_lines"], 0)
        self.assertIsNotNone(summary["error"])
        self.assertIsNone(summary["hrdps_wms"])


if __name__ == "__main__":
    unittest.main()
