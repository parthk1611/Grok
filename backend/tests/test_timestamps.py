import unittest
from datetime import datetime, timedelta, timezone

from backend.app.timestamps import (ImplausibleTimestamp, TimestampPolicy, check_source_time, is_plausible,
                                    latest_plausible, parse_utc)

NOW = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)


class SourceTimeTests(unittest.TestCase):
    def reason(self, v, **kw):
        with self.assertRaises(ImplausibleTimestamp) as cm:
            check_source_time(v, NOW, **kw)
        return cm.exception.reason

    def test_accepts_recent_and_small_future_skew(self):
        self.assertEqual(check_source_time("2026-11-02T23:31:00.000Z", NOW), NOW + timedelta(minutes=1))
        self.assertTrue(is_plausible("2026-11-03T01:29:59Z", NOW))   # just under +2 h

    def test_rejects_the_real_2101_swob_record(self):
        # seen live on api.weather.gc.ca swob-realtime on 2026-11-02 (station AZMP-HLX)
        self.assertEqual(self.reason("2101-06-16T11:58:16.000Z"), "in_future")

    def test_rejects_more_than_two_hours_ahead(self):
        self.assertEqual(self.reason("2026-11-03T01:31:00Z"), "in_future")

    def test_rejects_before_2000(self):
        self.assertEqual(self.reason("1999-12-31T23:59:59Z"), "too_old")
        self.assertEqual(self.reason("1970-01-01T00:00:00Z"), "too_old")
        self.assertTrue(is_plausible("2000-01-01T00:00:00Z", NOW))

    def test_rejects_malformed(self):
        for bad in ("2026-11-02", "2026-11-02T23:30:00", "2026-11-02T23:30:00+00:00", "not a time", "", None, 123,
                    "2026-13-02T23:30:00Z", "2026-02-30T00:00:00Z"):
            with self.subTest(bad=bad):
                self.assertEqual(self.reason(bad), "malformed")

    def test_rejects_naive_datetime(self):
        self.assertEqual(self.reason(datetime(2026, 11, 2, 23, 0)), "naive")

    def test_aware_datetime_in_other_zone_is_normalised(self):
        local = datetime(2026, 11, 2, 18, 30, tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(check_source_time(local, NOW), NOW)

    def test_custom_policy(self):
        p = TimestampPolicy(max_future=timedelta(minutes=5))
        self.assertFalse(is_plausible("2026-11-02T23:40:00Z", NOW, p))

    def test_now_must_be_aware(self):
        with self.assertRaises(ValueError):
            check_source_time("2026-11-02T23:30:00Z", datetime(2026, 11, 2))

    def test_parse_utc(self):
        self.assertEqual(parse_utc("2026-11-02T23:30:00.5Z").microsecond, 500000)

    def test_latest_plausible_ignores_bogus_values(self):
        vals = ["2101-06-16T11:58:16.000Z", "2026-11-02T22:00:00Z", "garbage", "2026-11-02T23:00:00Z"]
        self.assertEqual(latest_plausible(vals, NOW), datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc))
        self.assertIsNone(latest_plausible(["2101-01-01T00:00:00Z"], NOW))


if __name__ == "__main__":
    unittest.main()
