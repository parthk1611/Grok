"""Mock nits 1-3 (BEACON review of #5): mock weather/spread/status times come from the data clock.

Data clock = RELAY_MOCK_NOW when pinned, else the server clock. valid_time = clock - scenario age,
retrieved_at = valid_time + 5 min, spread run_timestamp = clock. Spread start_time limits stay on
the server clock, so a client sending real times is never rejected because the mock clock is pinned.
"""
import unittest
from datetime import datetime, timedelta, timezone

from backend.tests.test_api import MISSING, MOCK_NOW, SPREAD_BODY
from backend.tests.test_rulings import _client

AGE = {"fresh": 10, "stale": 120, "expired": 720}


def _t(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _weather(body):
    return body["records"][0] if isinstance(body, dict) and "records" in body else (
        body[0] if isinstance(body, list) else body)


@unittest.skipIf(MISSING is not None, f"backend deps missing: {MISSING}")
class MockClockTests(unittest.TestCase):
    def test_weather_times_agree_with_age_on_pinned_clock(self):
        for scenario, age in AGE.items():
            with self.subTest(scenario=scenario):
                r = _client(scenario).get("/api/v1/weather/latest?lat=50&lon=-90")
                self.assertEqual(r.status_code, 200)
                rec = _weather(r.json())
                valid = _t(MOCK_NOW) - timedelta(minutes=age)
                self.assertEqual(rec["valid_time"], _iso(valid))
                self.assertEqual(rec["retrieved_at"], _iso(valid + timedelta(minutes=5)))
                self.assertEqual(rec["age_minutes"], age)
                self.assertEqual(rec["stale_level"], scenario)
                self.assertIs(rec["from_cache"], scenario != "fresh")

    def test_spread_run_timestamp_and_weather_time_follow_clock(self):
        for scenario, age in AGE.items():
            with self.subTest(scenario=scenario):
                body = dict(SPREAD_BODY, confirm_degraded=True) if scenario == "expired" else SPREAD_BODY
                r = _client(scenario).post("/api/v1/spread", json=body)
                self.assertEqual(r.status_code, 200, r.text)
                out = r.json()
                self.assertEqual(out["run_timestamp"], MOCK_NOW)
                self.assertEqual(out["inputs"]["weather_valid_time"],
                                 _iso(_t(MOCK_NOW) - timedelta(minutes=age)))
                self.assertEqual(out["start_time"], SPREAD_BODY["start_time"])

    def test_spread_start_time_is_checked_on_server_clock_not_mock_clock(self):
        # SPREAD_BODY.start_time is ~Nov 2026, months after the pinned July MOCK_NOW: still accepted.
        self.assertGreater(_t(SPREAD_BODY["start_time"]) - _t(MOCK_NOW), timedelta(days=48))
        self.assertEqual(_client("fresh").post("/api/v1/spread", json=SPREAD_BODY).status_code, 200)

    def test_ingestion_status_uses_pinned_clock(self):
        body = _client("stale").get("/api/v1/ingestion/status").json()
        self.assertEqual(body["generated_at"], MOCK_NOW)
        valid = _t(MOCK_NOW) - timedelta(minutes=120)
        for s in body["sources"]:
            self.assertEqual(s["last_source_time"], _iso(valid))
            self.assertEqual(s["last_success_retrieved_at"], _iso(valid + timedelta(minutes=5)))
            self.assertEqual(s["age_minutes"], 120)
            self.assertLessEqual(_t(s["last_attempt_at"]), _t(MOCK_NOW))

    def test_unpinned_clock_tracks_server_time(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        rec = _weather(_client("fresh", mock_now="").get("/api/v1/weather/latest?lat=50&lon=-90").json())
        after = datetime.now(timezone.utc)
        age = timedelta(minutes=10)
        self.assertTrue(before - age <= _t(rec["valid_time"]) <= after - age + timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()
