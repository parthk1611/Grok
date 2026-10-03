"""Executable contract tests for the ignition feature panel builder."""

from __future__ import annotations

import bz2
import csv
import gzip
import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "scripts/build_ignition_feature_panel.py"
SPEC = importlib.util.spec_from_file_location("ignition_feature_panel", MODULE_PATH)
assert SPEC and SPEC.loader
FEATURES = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FEATURES
SPEC.loader.exec_module(FEATURES)

CDEM_SPEC = importlib.util.spec_from_file_location("cdem_terrain_summary", PROJECT_ROOT / "scripts/build_cdem_terrain_summary.py")
assert CDEM_SPEC and CDEM_SPEC.loader
CDEM = importlib.util.module_from_spec(CDEM_SPEC)
sys.modules[CDEM_SPEC.name] = CDEM
CDEM_SPEC.loader.exec_module(CDEM)


def all_panel_dates() -> list[date]:
    return FEATURES.expected_fire_season_dates()


class IgnitionFeaturePanelTest(unittest.TestCase):
    def write_fixture(self, root: Path) -> Namespace:
        grid_path = root / "grid.csv"
        labels_path = root / "labels.csv.gz"
        events_path = root / "events.csv"
        terrain_path = root / "terrain.csv"
        cwfis_path = root / "cwfis.csv.bz2"
        output_path = root / "features.csv.gz"
        audit_path = root / "audit.json"
        cells = [
            {"grid_id": "ofr10km_0_0", "grid_x_m": "0", "grid_y_m": "0", "centroid_longitude": "-85.000000", "centroid_latitude": "48.000000"},
            {"grid_id": "ofr10km_10000_0", "grid_x_m": "10000", "grid_y_m": "0", "centroid_longitude": "-84.850000", "centroid_latitude": "48.000000"},
        ]
        with grid_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=FEATURES.GRID_FIELDS)
            writer.writeheader()
            writer.writerows(cells)
        with terrain_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=FEATURES.TERRAIN_FIELDS)
            writer.writeheader()
            for cell in cells:
                writer.writerow(
                    {
                        "grid_id": cell["grid_id"],
                        "terrain_elevation_mean_m": "300",
                        "terrain_slope_deg": "2",
                        "terrain_aspect_sin": "0",
                        "terrain_aspect_cos": "1",
                        "terrain_ruggedness_m": "4",
                        "terrain_sample_count": "9",
                        "terrain_coverage_fraction": "1",
                        "terrain_source_id": "nrcan_cdem_1945_2011",
                        "terrain_source_vintage_end_year": "2011",
                        "terrain_feature_version": FEATURES.FEATURE_SET_VERSION,
                    }
                )
        with gzip.open(labels_path, "wt", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=FEATURES.LABEL_FIELDS)
            writer.writeheader()
            for issue_date in all_panel_dates():
                for cell in cells:
                    writer.writerow(
                        {
                            "grid_id": cell["grid_id"],
                            "ignition_date": issue_date.isoformat(),
                            "ignition": "0",
                            "ignition_count": "0",
                            "large_fire_200ha": "0",
                        }
                    )
        with events_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=["ignition_date", "grid_id"])
            writer.writeheader()
            writer.writerow({"ignition_date": "2010-05-01", "grid_id": cells[0]["grid_id"]})
            writer.writerow({"ignition_date": "2010-05-02", "grid_id": cells[0]["grid_id"]})
        required_weather_dates = {
            issue_date - timedelta(days=lag)
            for issue_date in all_panel_dates()
            for lag in range(1, 8)
        }
        cwfis_fields = ["rep_date", "aes", "wmo", "lon", "lat", "temp", "rh", "ws", "precip", "ffmc", "dmc", "dc", "isi", "bui", "fwi", "dsr", "opts", "calcstatus"]
        with bz2.open(cwfis_path, "wt", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=cwfis_fields)
            writer.writeheader()
            for observed in sorted(required_weather_dates):
                writer.writerow(
                    {
                        "rep_date": f"{observed.isoformat()} 12:00:00",
                        "aes": "1",
                        "wmo": "2",
                        "lon": "-85.0",
                        "lat": "48.0",
                        "temp": "20",
                        "rh": "35",
                        "ws": "12",
                        "precip": "1",
                        "ffmc": "85",
                        "dmc": "10",
                        "dc": "20",
                        "isi": "4",
                        "bui": "15",
                        "fwi": "7",
                        "dsr": "1",
                        "opts": "",
                        "calcstatus": "0",
                    }
                )
        return Namespace(
            grid=grid_path,
            labels=labels_path,
            events=events_path,
            terrain=terrain_path,
            cwfis=cwfis_path,
            cwfis_manifest=root / "absent-cwfis-manifest.json",
            source_registry=PROJECT_ROOT / "data/metadata/ignition_feature_source_registry_v1.json",
            output=output_path,
            audit=audit_path,
            start_date=None,
            end_date=None,
            station_count=1,
            force=False,
        )

    def test_builder_preserves_panel_and_excludes_same_day_history_and_weather(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            args = self.write_fixture(Path(temporary_directory))
            audit = FEATURES.build(args)
            self.assertEqual(audit["counts"]["output_rows"], 2 * len(all_panel_dates()))
            self.assertEqual(audit["validation"]["cwfis_observation_before_issue_violations"], 0)
            self.assertFalse(audit["validation"]["past_fire_history_includes_issue_or_future_date"])
            with gzip.open(args.output, "rt", encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 2 * len(all_panel_dates()))
            self.assertEqual(len({(row["grid_id"], row["ignition_date"]) for row in rows}), len(rows))
            first_day = [row for row in rows if row["ignition_date"] == "2010-05-01" and row["grid_id"] == "ofr10km_0_0"][0]
            second_day = [row for row in rows if row["ignition_date"] == "2010-05-02" and row["grid_id"] == "ofr10km_0_0"][0]
            self.assertEqual(first_day["fire_history_same_cell_30d"], "0")
            self.assertEqual(second_day["fire_history_same_cell_30d"], "1")
            self.assertEqual(second_day["cwfis_observation_date"], "2010-05-01")
            self.assertLess(second_day["cwfis_observation_date"], second_day["ignition_date"])
            persisted_audit = json.loads(args.audit.read_text(encoding="utf-8"))
            self.assertEqual(persisted_audit["validation"]["output_row_count_matches_selected_panel"], True)

    def test_cdem_summary_exposes_coverage_and_a_plane_slope(self) -> None:
        summary = CDEM.summarize([0.0, 10.0, 20.0, 0.0, 10.0, 20.0, 0.0, 10.0, 20.0])
        self.assertEqual(summary["terrain_sample_count"], "9")
        self.assertEqual(summary["terrain_coverage_fraction"], "1.000000")
        self.assertGreater(float(summary["terrain_slope_deg"]), 0.0)
        self.assertNotEqual(summary["terrain_aspect_sin"], "")

    def test_cwfis_lookup_does_not_scan_unneeded_unsorted_archive_tail(self) -> None:
        first, second = date(2010, 4, 29), date(2010, 4, 30)

        def source():
            yield first, {}
            yield second, {}
            raise AssertionError("The lookup must not read past its final requested date.")

        lookup = FEATURES.CwfisDayLookup(source())
        self.assertEqual(lookup.records_for(first), {})
        self.assertEqual(lookup.records_for(second), {})


if __name__ == "__main__":
    unittest.main()
