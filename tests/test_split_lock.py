"""Lock the 2010-2019 chronological split contract recorded in docs/modeling/split_manifest.json."""

from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((PROJECT_ROOT / "docs/modeling/split_manifest.json").read_text())

# Team Lead decision (2026-11-02): splits are final for the 2010-19 panel.
EXPECTED = {
    "development": ("2010-05-01", "2016-10-31"),
    "calibration": ("2017-05-01", "2017-10-31"),
    "threshold": ("2018-05-01", "2018-10-31"),
    "test": ("2019-05-01", "2019-10-31"),
}
ORDER = ["development", "calibration", "threshold", "test"]
PANEL_DATES, PANEL_ROWS, PANEL_CELLS = 1840, 17_798_320, 9673


def season_dates(first_year: int, last_year: int) -> list[date]:
    days = []
    for year in range(first_year, last_year + 1):
        day = date(year, 5, 1)
        while day <= date(year, 10, 31):
            days.append(day)
            day += timedelta(days=1)
    return days


def span(name: str) -> tuple[date, date]:
    part = MANIFEST["partitions"][name]
    return date.fromisoformat(part["start"]), date.fromisoformat(part["end"])


class SplitLockTest(unittest.TestCase):
    def test_partition_names_and_codes(self):
        self.assertEqual(list(MANIFEST["partitions"]), ORDER)
        self.assertEqual(MANIFEST["partition_codes"], {name: i for i, name in enumerate(ORDER)})

    def test_boundaries_match_the_locked_values(self):
        for name, (start, end) in EXPECTED.items():
            with self.subTest(name):
                self.assertEqual((MANIFEST["partitions"][name]["start"], MANIFEST["partitions"][name]["end"]), (start, end))

    def test_date_contract_is_america_toronto_local_dates(self):
        contract = MANIFEST["date_contract"]
        self.assertEqual(contract["timezone"], "America/Toronto")
        self.assertEqual(contract["issue_time"], "00:00 local on ignition_date")
        self.assertIn("May 1 through October 31", contract["season"])

    def test_partitions_are_chronological_and_non_overlapping(self):
        spans = [span(name) for name in ORDER]
        for (start, end), name in zip(spans, ORDER):
            self.assertLessEqual(start, end, name)
        for (_, previous_end), (next_start, _), name in zip(spans, spans[1:], ORDER[1:]):
            self.assertLess(previous_end, next_start, f"{name} overlaps the previous partition")

    def test_each_partition_covers_exactly_its_fire_season_dates(self):
        for name in ORDER:
            start, end = span(name)
            expected = [d for d in season_dates(start.year, end.year) if start <= d <= end]
            self.assertEqual(MANIFEST["partitions"][name]["dates"], len(expected), name)

    def test_partitions_tile_the_whole_panel(self):
        self.assertEqual(sum(MANIFEST["partitions"][n]["dates"] for n in ORDER), PANEL_DATES)
        self.assertEqual(sum(MANIFEST["partitions"][n]["rows"] for n in ORDER), PANEL_ROWS)
        self.assertEqual(len(season_dates(2010, 2019)), PANEL_DATES)
        for name in ORDER:
            part = MANIFEST["partitions"][name]
            self.assertEqual(part["rows"], part["dates"] * PANEL_CELLS, name)
            self.assertEqual(part["positives"] + part["negatives"], part["rows"], name)
        totals = MANIFEST["totals"]
        self.assertEqual(totals["positives"], sum(MANIFEST["partitions"][n]["positives"] for n in ORDER))
        self.assertEqual((totals["start"], totals["end"]), ("2010-05-01", "2019-10-31"))

    def test_no_partition_reaches_2020_or_later(self):
        for name in ORDER:
            self.assertLess(span(name)[1].year, 2020, name)
        for fold in MANIFEST["development_folds"]:
            for part in fold.values():
                if isinstance(part, dict) and "end" in part:
                    self.assertLess(date.fromisoformat(part["end"]).year, 2020)

    def test_2019_is_the_locked_final_test(self):
        self.assertEqual(span("test"), (date(2019, 5, 1), date(2019, 10, 31)))
        # Nothing may be later than the test partition, and nothing else may use 2019.
        for name in ORDER[:-1]:
            self.assertLess(span(name)[1].year, 2019, name)
        self.assertEqual(MANIFEST["final_test"]["partition"], "test")
        self.assertIs(MANIFEST["final_test"]["locked"], True)
        freeze = json.loads((PROJECT_ROOT / "docs/modeling/final_freeze.json").read_text())
        self.assertEqual(freeze["fit_years"], [2010, 2016])
        self.assertEqual(freeze["calibration_fit_year"], 2017)
        self.assertEqual(freeze["calibration_selection_year"], 2018)

    def test_development_folds_train_strictly_before_they_validate(self):
        for fold in MANIFEST["development_folds"]:
            training_end = date.fromisoformat(fold["training"]["end"])
            validation = fold.get("validation") or fold.get("validate")
            self.assertIsNotNone(validation, fold["name"])
            self.assertLess(training_end, date.fromisoformat(validation["start"]), fold["name"])
            self.assertLess(training_end.year, 2017, fold["name"])


if __name__ == "__main__":
    unittest.main()
