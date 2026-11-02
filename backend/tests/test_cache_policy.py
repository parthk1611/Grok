import unittest
from datetime import datetime, timedelta, timezone

from backend.app.cache_policy import (AgeThresholds, CachePolicy, DATA_AGE_THRESHOLDS, DATA_AGE_THRESHOLDS_TBD,
                                      DEFAULT_POLICY, StaleLevel, age_minutes, combine, decide, level_for_age,
                                      should_alert, worst)

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
F, S, E, N = StaleLevel.FRESH, StaleLevel.STALE, StaleLevel.EXPIRED, StaleLevel.NO_CACHE


def ago(**kw):
    return NOW - timedelta(**kw)


class LevelTests(unittest.TestCase):
    def test_defaults_are_the_proposed_values_and_data_age_is_tbd(self):
        p = DEFAULT_POLICY
        self.assertTrue(CachePolicy.PROPOSAL)
        self.assertTrue(DATA_AGE_THRESHOLDS_TBD)
        self.assertEqual((p.poll_interval_minutes, p.fresh_max_minutes, p.stale_max_minutes, p.fetch_timeout_seconds),
                         (15.0, 30.0, 360.0, 20.0))
        self.assertEqual(set(DATA_AGE_THRESHOLDS), {"hrdps_geomet_wms", "cwfis_firewx_stns_current", "msc_swob_realtime"})

    def test_boundaries(self):
        self.assertIs(level_for_age(0), F)
        self.assertIs(level_for_age(30), F)        # <= 30 is fresh
        self.assertIs(level_for_age(30.01), S)
        self.assertIs(level_for_age(360), S)       # <= 6 h is stale
        self.assertIs(level_for_age(360.01), E)

    def test_negative_age_rejected(self):
        with self.assertRaises(ValueError):
            level_for_age(-1)

    def test_thresholds_validate_ordering(self):
        for bad in (lambda: CachePolicy(fresh_max_minutes=60, stale_max_minutes=30),
                    lambda: AgeThresholds(10, 10), lambda: AgeThresholds(0, 5)):
            with self.assertRaises(ValueError):
                bad()

    def test_custom_thresholds_are_respected(self):
        t = AgeThresholds(10, 20)
        self.assertIs(level_for_age(15, t), S)
        self.assertIs(level_for_age(21, t), E)

    def test_worst(self):
        self.assertIs(worst(F, E, S), E)
        self.assertIs(worst(E, N), N)


class AgeTests(unittest.TestCase):
    def test_age(self):
        self.assertEqual(age_minutes(NOW, ago(minutes=90)), 90)

    def test_small_future_skew_clamps(self):
        self.assertEqual(age_minutes(NOW, NOW + timedelta(minutes=3)), 0.0)

    def test_large_future_skew_rejected(self):
        with self.assertRaises(ValueError):
            age_minutes(NOW, NOW + timedelta(hours=1))

    def test_naive_rejected(self):
        with self.assertRaises(ValueError):
            age_minutes(NOW, datetime(2026, 7, 15, 11, 0))


class DecideFetchHealthTests(unittest.TestCase):
    def test_no_cache_means_503_path(self):
        d = decide(NOW, None, source="hrdps_geomet_wms")
        self.assertEqual((d.level, d.serve, d.stale_flag, d.age_minutes), (N, False, True, None))
        self.assertIn("hrdps", d.reason)

    def test_fresh(self):
        d = decide(NOW, ago(minutes=10))
        self.assertEqual((d.level, d.stale_flag, d.serve, d.degraded, d.reason), (F, False, True, False, None))

    def test_stale_fetch_served_with_flag(self):
        d = decide(NOW, ago(hours=2), source="x")
        self.assertEqual((d.level, d.stale_flag, d.serve, d.degraded, d.age_minutes), (S, True, True, False, 120))
        self.assertIn("fetch stale", d.reason)

    def test_expired_fetch_served_degraded(self):
        d = decide(NOW, ago(hours=7))
        self.assertEqual((d.level, d.stale_flag, d.serve, d.degraded), (E, True, True, True))

    def test_stale_flag_always_matches_level(self):
        for mins in (0, 30, 31, 360, 361):
            d = decide(NOW, ago(minutes=mins))
            self.assertEqual(d.stale_flag, d.level is not F)


class DecideTwoAgeTests(unittest.TestCase):
    def test_fresh_fetch_but_old_observation_is_expired(self):
        # polled 5 min ago, but the observation itself is 8 h old; unknown source -> fetch thresholds
        d = decide(NOW, ago(minutes=5), kind="observation", valid_time=ago(hours=8), source="unknown_feed")
        self.assertEqual((d.level, d.fetch_level, d.data_level), (E, F, E))
        self.assertAlmostEqual(d.age_minutes, 480)       # age of the input that sets the level

    def test_per_source_data_age_thresholds(self):
        # the same 11 h-old report is fresh for CWFIS (placeholder 15 h) but expired for swob (6 h)
        cw = decide(NOW, ago(minutes=5), source="cwfis_firewx_stns_current", valid_time=ago(hours=11))
        sw = decide(NOW, ago(minutes=5), source="msc_swob_realtime", valid_time=ago(hours=11))
        self.assertIs(cw.level, F)
        self.assertIs(sw.level, E)

    def test_fetch_stale_and_data_fresh_reports_fetch_age(self):
        d = decide(NOW, ago(hours=2), source="cwfis_firewx_stns_current", valid_time=ago(hours=1))
        self.assertEqual((d.level, d.fetch_level, d.data_level), (S, S, F))
        self.assertAlmostEqual(d.age_minutes, 120)

    def test_level_is_worse_of_the_two(self):
        d = decide(NOW, ago(hours=2), source="msc_swob_realtime", valid_time=ago(hours=7))   # fetch stale, data expired
        self.assertEqual((d.level, d.fetch_level, d.data_level), (E, S, E))
        self.assertIn("fetch stale", d.reason)
        self.assertIn("data expired", d.reason)

    def test_forecast_uses_source_run_time(self):
        # HRDPS run 7 h old (placeholder fresh 8 h): still fresh
        d = decide(NOW, ago(minutes=5), kind="forecast", source="hrdps_geomet_wms",
                   source_run_time=ago(hours=7), valid_time=NOW + timedelta(hours=3))
        self.assertIs(d.level, F)
        d = decide(NOW, ago(minutes=5), kind="forecast", source="hrdps_geomet_wms",
                   source_run_time=ago(hours=19), valid_time=NOW + timedelta(hours=3))
        self.assertIs(d.level, E)

    def test_forecast_valid_time_in_past_is_at_least_stale(self):
        d = decide(NOW, ago(minutes=5), kind="forecast", source="hrdps_geomet_wms",
                   source_run_time=ago(hours=1), valid_time=ago(minutes=1))
        self.assertIs(d.level, S)

    def test_forecast_past_valid_time_does_not_downgrade_expired(self):
        d = decide(NOW, ago(hours=9), kind="forecast", valid_time=ago(hours=1))
        self.assertIs(d.level, E)

    def test_missing_data_time_judges_fetch_health_only(self):
        d = decide(NOW, ago(minutes=5), kind="forecast")
        self.assertEqual((d.level, d.data_level, d.data_age_minutes), (F, None, None))

    def test_bad_kind(self):
        with self.assertRaises(ValueError):
            decide(NOW, ago(minutes=1), kind="nowcast")

    def test_custom_policy_data_age(self):
        p = CachePolicy(data_age={"s": AgeThresholds(5, 10)})
        self.assertIs(decide(NOW, ago(minutes=1), source="s", valid_time=ago(minutes=7), policy=p).level, S)


class CombineAndAlertTests(unittest.TestCase):
    def test_combine_takes_worst_feed(self):
        a = decide(NOW, ago(minutes=5), source="a")
        b = decide(NOW, ago(hours=2), source="b")
        c = combine([a, b])
        self.assertEqual((c.level, c.stale_flag, c.age_minutes), (S, True, 120))
        self.assertIn("b:", c.reason)

    def test_combine_with_a_never_succeeded_feed_cannot_serve(self):
        c = combine([decide(NOW, ago(minutes=5)), decide(NOW, None, source="b")])
        self.assertEqual((c.level, c.serve), (N, False))

    def test_combine_empty(self):
        with self.assertRaises(ValueError):
            combine([])

    def test_alert_rules(self):
        self.assertFalse(should_alert(F, 0))
        self.assertFalse(should_alert(S, 3))   # needs MORE than 3
        self.assertTrue(should_alert(S, 4))
        self.assertTrue(should_alert(E, 0))
        self.assertTrue(should_alert(N, 0))


if __name__ == "__main__":
    unittest.main()
