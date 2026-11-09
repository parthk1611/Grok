"""Executable tests for scripts/data_sources/alignment.py (time, leakage and grid rules)."""

from __future__ import annotations

import csv
import importlib.util
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("alignment", PROJECT_ROOT / "scripts/data_sources/alignment.py")
assert SPEC and SPEC.loader
A = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = A
SPEC.loader.exec_module(A)

try:
    import pyproj  # noqa: F401
except ImportError:  # keep `unittest discover` usable without the optional dependency
    pyproj = None

UTC = timezone.utc
TORONTO = ZoneInfo("America/Toronto")


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


class UtcAndLocalDateTest(unittest.TestCase):
    def test_naive_and_zoneless_input_is_rejected(self):
        with self.assertRaises(A.AlignmentError):
            A.utc_to_local_date(datetime(2019, 7, 1, 12))
        with self.assertRaises(A.AlignmentError):
            A.parse_utc("2019-07-01T12:00:00")
        with self.assertRaises(A.AlignmentError):
            A.parse_utc("not a time")

    def test_parse_and_format_round_trip_matches_contract_pattern(self):
        for text in ("2019-07-01T05:00:00Z", "2019-07-01T01:00:00-04:00"):
            parsed = A.parse_utc(text)
            self.assertEqual(parsed, utc(2019, 7, 1, 5))
            self.assertEqual(A.format_utc_z(parsed), "2019-07-01T05:00:00Z")
            self.assertRegex(A.format_utc_z(parsed), A.UTC_Z_PATTERN.pattern)

    def test_local_date_is_derived_from_utc_with_dst(self):
        # Summer (EDT, UTC-4): local midnight is 04:00Z.
        self.assertEqual(A.utc_to_local_date(utc(2019, 7, 1, 3, 59, 59)), date(2019, 6, 30))
        self.assertEqual(A.utc_to_local_date(utc(2019, 7, 1, 4, 0, 0)), date(2019, 7, 1))
        # Winter (EST, UTC-5): local midnight is 05:00Z.
        self.assertEqual(A.utc_to_local_date(utc(2019, 1, 1, 4, 59, 59)), date(2018, 12, 31))
        self.assertEqual(A.utc_to_local_date(utc(2019, 1, 1, 5, 0, 0)), date(2019, 1, 1))

    def test_issue_time_is_dst_aware_and_not_a_fixed_offset(self):
        self.assertEqual(A.issue_time_utc(date(2019, 7, 1)), utc(2019, 7, 1, 4))
        self.assertEqual(A.issue_time_utc(date(2019, 1, 1)), utc(2019, 1, 1, 5))
        # A fixed UTC-5 rule would give 05:00Z in July; it must not.
        self.assertNotEqual(A.issue_time_utc(date(2019, 7, 1)), utc(2019, 7, 1, 5))

    def test_source_has_no_hard_coded_offsets(self):
        text = (PROJECT_ROOT / "scripts/data_sources/alignment.py").read_text()
        for needle in ("timedelta(hours=5", "timedelta(hours=-5", "timezone(timedelta", "hours=-4", "hours=4)"):
            self.assertNotIn(needle, text)


class DstLabelWindowTest(unittest.TestCase):
    def test_spring_forward_day_is_23_hours(self):
        day = date(2019, 3, 10)  # clocks jump 02:00 EST -> 03:00 EDT
        start, end = A.label_window_utc(day)
        self.assertEqual(start, utc(2019, 3, 10, 5))   # midnight EST
        self.assertEqual(end, utc(2019, 3, 11, 4))     # midnight EDT
        self.assertEqual(A.label_window_hours(day), 23)
        self.assertEqual(A.utc_to_local_date(end - timedelta(seconds=1)), day)
        self.assertEqual(A.utc_to_local_date(end), date(2019, 3, 11))

    def test_fall_back_day_is_25_hours(self):
        day = date(2019, 11, 3)  # clocks fall 02:00 EDT -> 01:00 EST
        start, end = A.label_window_utc(day)
        self.assertEqual(start, utc(2019, 11, 3, 4))   # midnight EDT
        self.assertEqual(end, utc(2019, 11, 4, 5))     # midnight EST
        self.assertEqual(A.label_window_hours(day), 25)
        # The repeated 01:00 hour (EDT and EST) both fall inside the same local date.
        for stamp in (utc(2019, 11, 3, 5), utc(2019, 11, 3, 6)):
            self.assertEqual(A.utc_to_local_date(stamp), day)
            self.assertTrue(A.in_label_window(stamp, day))

    def test_ordinary_day_is_24_hours_and_windows_tile_without_gaps(self):
        self.assertEqual(A.label_window_hours(date(2019, 7, 15)), 24)
        day = date(2019, 3, 8)
        previous_end = None
        for _ in range(6):  # across the spring-forward weekend
            start, end = A.label_window_utc(day)
            if previous_end is not None:
                self.assertEqual(start, previous_end)
            previous_end = end
            day += timedelta(days=1)

    def test_window_is_half_open(self):
        day = date(2019, 7, 1)
        start, end = A.label_window_utc(day)
        self.assertTrue(A.in_label_window(start, day))
        self.assertFalse(A.in_label_window(end, day))
        self.assertFalse(A.in_label_window(start - timedelta(seconds=1), day))

    def test_matches_stdlib_zoneinfo_for_every_fire_season_midnight_of_2019(self):
        day = date(2019, 5, 1)
        while day <= date(2019, 10, 31):
            expected = datetime(day.year, day.month, day.day, tzinfo=TORONTO).astimezone(UTC)
            self.assertEqual(A.issue_time_utc(day), expected)
            day += timedelta(days=1)


class CutoffTest(unittest.TestCase):
    TARGET = date(2019, 7, 15)

    def test_prior_day_and_earlier_are_accepted(self):
        A.assert_t_minus_1_cutoff(date(2019, 7, 14), self.TARGET)
        A.assert_t_minus_1_cutoff(date(2019, 7, 1), self.TARGET)

    def test_same_day_and_future_are_rejected(self):
        with self.assertRaisesRegex(A.LeakageError, "same-day"):
            A.assert_t_minus_1_cutoff(self.TARGET, self.TARGET)
        with self.assertRaisesRegex(A.LeakageError, "future"):
            A.assert_t_minus_1_cutoff(date(2019, 7, 16), self.TARGET)

    def test_instant_cutoff_uses_local_midnight_not_a_date_string(self):
        issue = A.issue_time_utc(self.TARGET)
        A.assert_available_before_issue(issue, self.TARGET)  # interval ending exactly at issue is fine
        A.assert_available_before_issue(issue - timedelta(hours=1), self.TARGET)
        with self.assertRaises(A.LeakageError):
            A.assert_available_before_issue(issue + timedelta(seconds=1), self.TARGET)

    def test_row_checks_reject_same_day_and_future_rows(self):
        good = [{"obs": "2019-07-14", "target": "2019-07-15"}, {"obs": date(2019, 7, 1), "target": date(2019, 7, 15)}]
        self.assertEqual(A.assert_rows_before_cutoff(good, "obs", "target"), 2)
        for bad_obs in ("2019-07-15", "2019-07-16"):
            with self.assertRaises(A.LeakageError):
                A.assert_rows_before_cutoff(good + [{"obs": bad_obs, "target": "2019-07-15"}], "obs", "target")

    def test_no_future_rows_by_utc_timestamp(self):
        issue = A.issue_time_utc(self.TARGET)
        ok = [{"t": format_z(issue - timedelta(hours=1))}, {"t": issue - timedelta(days=3)}]
        self.assertEqual(A.assert_no_future_rows(ok, "t", self.TARGET), 2)
        for stamp in (issue, issue + timedelta(hours=5)):
            with self.assertRaises(A.LeakageError):
                A.assert_no_future_rows(ok + [{"t": stamp}], "t", self.TARGET)

    def test_interval_end_rows_may_equal_issue(self):
        issue = A.issue_time_utc(self.TARGET)
        self.assertEqual(A.assert_no_future_rows([{"t": issue}], "t", self.TARGET, timestamp_marks="end"), 1)
        with self.assertRaises(A.LeakageError):
            A.assert_no_future_rows([{"t": issue + timedelta(seconds=1)}], "t", self.TARGET, timestamp_marks="end")
        with self.assertRaises(ValueError):
            A.assert_no_future_rows([{"t": issue}], "t", self.TARGET, timestamp_marks="middle")

    def test_naive_row_timestamps_are_rejected(self):
        with self.assertRaises(A.AlignmentError):
            A.assert_no_future_rows([{"t": datetime(2019, 7, 14)}], "t", self.TARGET)


def format_z(value: datetime) -> str:
    return A.format_utc_z(value)


class EcccTimeTest(unittest.TestCase):
    def test_lst_is_one_hour_behind_civil_time_in_summer(self):
        # Measured from api.weather.gc.ca climate-hourly (Armstrong AUT, 2019-07-01):
        # LOCAL_DATE 00:00:00 with UTC_DATE 2019-07-01T05:00:00.
        self.assertEqual(A.eccc_lst_to_utc(A.parse_eccc_lst("2019-07-01 00:00")), utc(2019, 7, 1, 5))
        self.assertEqual(A.eccc_lst_to_utc(A.parse_eccc_lst("2019-01-01 00:00")), utc(2019, 1, 1, 5))

    def test_standard_offset_is_read_from_the_tz_database(self):
        self.assertEqual(A.standard_offset("America/Toronto", date(2019, 7, 1)), timedelta(hours=-5))
        self.assertEqual(A.standard_offset("America/Toronto", date(2019, 1, 1)), timedelta(hours=-5))
        self.assertEqual(A.standard_offset("America/Winnipeg", date(2019, 7, 1)), timedelta(hours=-6))

    def test_lst_wall_time_with_zone_is_rejected(self):
        with self.assertRaises(A.AlignmentError):
            A.eccc_lst_to_utc(datetime(2019, 7, 1, tzinfo=UTC))

    def test_api_record_utc_and_lst_must_agree(self):
        good = {"LOCAL_DATE": "2019-07-01 00:00:00", "UTC_DATE": "2019-07-01T05:00:00"}
        self.assertEqual(A.eccc_hourly_utc(good), utc(2019, 7, 1, 5))
        with self.assertRaises(A.AlignmentError):
            A.eccc_hourly_utc({"LOCAL_DATE": "2019-07-01 00:00:00", "UTC_DATE": "2019-07-01T04:00:00"})
        self.assertEqual(A.eccc_hourly_utc({"LOCAL_DATE": "2019-07-01 00:00:00"}), utc(2019, 7, 1, 5))
        with self.assertRaises(A.AlignmentError):
            A.eccc_hourly_utc({})

    def test_api_utc_date_with_offset_is_converted_not_overwritten(self):
        self.assertEqual(A.eccc_hourly_utc({"UTC_DATE": "2019-07-01T05:00:00Z"}), utc(2019, 7, 1, 5))
        self.assertEqual(A.eccc_hourly_utc({"UTC_DATE": "2019-07-01T01:00:00-04:00"}), utc(2019, 7, 1, 5))

    def test_lst_climate_day_window_is_always_24_hours(self):
        for lst_day in (date(2019, 3, 10), date(2019, 11, 3), date(2019, 7, 1)):
            start, end = A.eccc_lst_day_window_utc(lst_day)
            self.assertEqual(end - start, timedelta(hours=24))

    def test_summer_daily_record_for_t_minus_1_is_not_complete_at_issue(self):
        # LST day 2019-07-01 ends 2019-07-02T05:00Z; issue for 2019-07-02 is 04:00Z.
        self.assertFalse(A.eccc_daily_available_by_issue(date(2019, 7, 1), date(2019, 7, 2)))
        self.assertTrue(A.eccc_daily_available_by_issue(date(2019, 6, 30), date(2019, 7, 2)))
        # In winter the LST day ends exactly at local midnight.
        self.assertTrue(A.eccc_daily_available_by_issue(date(2019, 1, 1), date(2019, 1, 2)))


class TwoAgeStalenessTest(unittest.TestCase):
    """cache_fallback.md v0.2.1: fetch health from retrieved_at, data age from valid_time/source_run_time."""

    NOW = utc(2026, 11, 2, 23, 30)
    THRESHOLDS = dict(data_age_fresh_max_minutes=360.0, data_age_stale_max_minutes=720.0)  # test values, not policy

    def call(self, **overrides):
        args = dict(
            now_utc=self.NOW, kind="forecast", retrieved_at_utc=self.NOW - timedelta(minutes=5),
            valid_time_utc=self.NOW + timedelta(hours=1), source_run_time_utc=self.NOW - timedelta(hours=1),
            **self.THRESHOLDS,
        )
        args.update(overrides)
        return A.staleness(**args)

    def test_fresh_fetch_of_a_old_model_run_is_not_fresh(self):
        # Measured 2026-11-02 23:31Z: newest HRDPS run on GeoMet was 18:00Z (5.5 h old) while a
        # poll made seconds earlier is fresh by retrieved_at alone.
        result = self.call(source_run_time_utc=self.NOW - timedelta(hours=5, minutes=30),
                           data_age_fresh_max_minutes=300.0)
        self.assertEqual(result["fetch_level"], "fresh")
        self.assertEqual(result["data_level"], "stale")
        self.assertEqual(result["stale_level"], "stale")
        self.assertTrue(result["stale_flag"])
        self.assertEqual(result["age_minutes"], 330)          # v0.2.1: data age
        self.assertLess(result["fetch_age_minutes"], 30)      # fetch health reported separately

    def test_worse_of_the_two_ages_wins(self):
        result = self.call(retrieved_at_utc=self.NOW - timedelta(hours=7))
        self.assertEqual((result["fetch_level"], result["stale_level"]), ("expired", "expired"))
        self.assertEqual(result["fetch_age_minutes"], 420)
        self.assertEqual(result["age_minutes"], 60)           # data age stays the data age

    def test_all_fresh(self):
        result = self.call()
        self.assertEqual(result["stale_level"], "fresh")
        self.assertFalse(result["stale_flag"])

    def test_forecast_needs_source_run_time_and_observation_uses_valid_time(self):
        with self.assertRaises(A.AlignmentError):
            self.call(source_run_time_utc=None)
        obs = self.call(kind="observation", valid_time_utc=self.NOW - timedelta(hours=11), source_run_time_utc=None)
        self.assertEqual(obs["age_minutes"], 660)
        self.assertEqual(obs["stale_level"], "stale")

    def test_past_forecast_hour_is_at_least_stale(self):
        result = self.call(valid_time_utc=self.NOW - timedelta(minutes=10))
        self.assertEqual(result["stale_level"], "stale")

    def test_future_timestamps_and_naive_times_are_rejected(self):
        with self.assertRaises(A.AlignmentError):
            self.call(retrieved_at_utc=self.NOW + timedelta(minutes=1))
        with self.assertRaises(A.AlignmentError):
            self.call(now_utc=datetime(2026, 11, 2, 23, 30))

    def test_data_age_thresholds_have_no_default(self):
        with self.assertRaises(TypeError):
            A.staleness(now_utc=self.NOW, kind="observation", retrieved_at_utc=self.NOW,
                        valid_time_utc=self.NOW, source_run_time_utc=None)


class HourlyRebuildGateTest(unittest.TestCase):
    """Gate: daily inputs for target t must be rebuilt from hourly values that end by local midnight."""

    def hourly_for(self, day, value=1.0, skip=None):
        data = {}
        for i, start in enumerate(A.civil_day_hour_instants(day)):
            if i != skip:
                data[start + timedelta(hours=1)] = value
        return data

    def test_civil_day_hours_are_23_24_25_on_dst_days(self):
        self.assertEqual(len(A.civil_day_hour_instants(date(2019, 3, 10))), 23)
        self.assertEqual(len(A.civil_day_hour_instants(date(2019, 7, 1))), 24)
        self.assertEqual(len(A.civil_day_hour_instants(date(2019, 11, 3))), 25)

    def test_summer_t_minus_1_is_rebuilt_from_hourly_values_ending_at_issue(self):
        day, target = date(2019, 7, 1), date(2019, 7, 2)
        data = self.hourly_for(day)
        self.assertEqual(A.rebuild_daily_total_from_hourly(data, day, target), 24.0)
        # The same record taken as an ECCC LST daily summary is NOT safe in summer ...
        self.assertFalse(A.eccc_daily_available_by_issue(day, target))
        # ... and the last hour used ends exactly at the issue instant.
        self.assertEqual(max(data), A.issue_time_utc(target))

    def test_dst_days_sum_23_and_25_hours(self):
        self.assertEqual(A.rebuild_daily_total_from_hourly(self.hourly_for(date(2019, 3, 10)), date(2019, 3, 10), date(2019, 3, 11)), 23.0)
        self.assertEqual(A.rebuild_daily_total_from_hourly(self.hourly_for(date(2019, 11, 3)), date(2019, 11, 3), date(2019, 11, 4)), 25.0)

    def test_missing_hour_gives_none_not_a_partial_sum_or_zero(self):
        day, target = date(2019, 7, 1), date(2019, 7, 2)
        self.assertIsNone(A.rebuild_daily_total_from_hourly(self.hourly_for(day, skip=5), day, target))
        data = self.hourly_for(day)
        data[sorted(data)[3]] = None
        self.assertIsNone(A.rebuild_daily_total_from_hourly(data, day, target))

    def test_same_day_or_future_day_is_rejected(self):
        target = date(2019, 7, 2)
        for day in (target, date(2019, 7, 3)):
            with self.assertRaises(A.LeakageError):
                A.rebuild_daily_total_from_hourly(self.hourly_for(day), day, target)


class UnitsTest(unittest.TestCase):
    def test_wind_ms_to_kmh(self):
        self.assertAlmostEqual(A.ms_to_kmh(10.0), 36.0)
        self.assertAlmostEqual(A.ms_to_kmh(3.3199999), 11.95199964)  # measured GeoMet HRDPS sample value
        self.assertIsNone(A.ms_to_kmh(None))
        self.assertEqual(A.ms_to_kmh(0.0), 0.0)


class GridJoinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grid = A.load_grid()

    def test_grid_matches_grid_spec_v0(self):
        self.assertEqual(A.GRID_VERSION, "ofr10km-v1")
        self.assertEqual(A.GRID_CRS, "EPSG:3978")
        self.assertEqual(A.GRID_CELL_M, 10_000)
        self.assertEqual(len(self.grid), 9673)
        xs = [c.x_m for c in self.grid.values()]
        ys = [c.y_m for c in self.grid.values()]
        self.assertEqual((min(xs), max(xs), min(ys), max(ys)), (-20_000, 1_480_000, -450_000, 870_000))
        self.assertIn("ofr10km_-10000_100000", self.grid)

    def test_id_formula_floor_snaps_negative_coordinates(self):
        self.assertEqual(A.grid_id_from_xy(-10_000, 100_000), "ofr10km_-10000_100000")
        self.assertEqual(A.grid_id_from_xy(-9_999.9, 100_000.1), "ofr10km_-10000_100000")
        self.assertEqual(A.grid_id_from_xy(-10_000.1, 109_999.9), "ofr10km_-20000_100000")
        self.assertEqual(A.grid_id_from_xy(5_000, 5_000), "ofr10km_0_0")

    @unittest.skipIf(pyproj is None, "pyproj not installed: pip install -r requirements-data.txt")
    def test_every_centroid_projects_into_its_own_cell(self):
        for cell in self.grid.values():
            self.assertEqual(A.lonlat_to_grid_id(cell.centroid_longitude, cell.centroid_latitude), cell.grid_id)

    def test_join_adds_grid_attributes_without_mutating_input(self):
        rows = [{"grid_id": "ofr10km_-10000_100000", "value": 1}]
        joined = A.join_grid(rows, self.grid)
        self.assertEqual(rows, [{"grid_id": "ofr10km_-10000_100000", "value": 1}])
        self.assertEqual(joined[0]["grid_x_m"], -10_000)
        self.assertEqual(joined[0]["grid_y_m"], 100_000)
        self.assertAlmostEqual(joined[0]["centroid_longitude"], -95.069909)
        self.assertAlmostEqual(joined[0]["centroid_latitude"], 49.945759)
        self.assertEqual(joined[0]["value"], 1)

    def test_unknown_grid_id_fails_strict_and_is_flagged_otherwise(self):
        rows = [{"grid_id": "ofr10km_999999_999999"}]
        with self.assertRaises(A.AlignmentError):
            A.join_grid(rows, self.grid)
        loose = A.join_grid(rows, self.grid, strict=False)
        self.assertIsNone(loose[0]["grid_x_m"])

    def test_load_grid_rejects_inconsistent_or_duplicate_ids(self):
        header = "grid_id,grid_x_m,grid_y_m,centroid_longitude,centroid_latitude\n"
        cases = {
            "id_mismatch": header + "ofr10km_0_0,10000,0,-95.0,50.0\n",
            "off_lattice": header + "ofr10km_5_0,5,0,-95.0,50.0\n",
            "duplicate": header + "ofr10km_0_0,0,0,-95.0,50.0\nofr10km_0_0,0,0,-95.0,50.0\n",
        }
        for name, content in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "grid.csv"
                path.write_text(content)
                with self.assertRaises(A.AlignmentError):
                    A.load_grid(path)

    def test_panel_grid_file_has_no_duplicates_or_gaps_in_columns(self):
        with open(A.GRID_PATH, newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len({r["grid_id"] for r in rows}), len(rows))
        self.assertTrue(all(r["centroid_longitude"] and r["centroid_latitude"] for r in rows))


if __name__ == "__main__":
    unittest.main()
