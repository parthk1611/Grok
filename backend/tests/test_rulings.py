"""BEACON review / Team Lead rulings of 2026-11-09 (openapi 0.2.1-draft). Responses validated against openapi.yaml."""
import gzip
import json
import unittest
from dataclasses import replace

from backend.tests.test_api import (CACHE_TIME, CELL, CONTRACTS, DAY, DISCLAIMER, MANUAL_REASON, MISSING, MOCK_NOW,
                                    SPREAD_BODY, ApiBase, _build)

if MISSING is None:
    from fastapi.testclient import TestClient
    from jsonschema import Draft202012Validator

OVERRIDE = {"wind_speed_kmh": 22, "wind_direction_deg": 225, "ffmc": 88, "rh_pct": 35}
ENVELOPE_KEYS = {"mode", "stale_level", "age_minutes", "data_timestamp", "requested_date", "mock", "records"}


def _client(scenario="fresh", provider=None, mock_now=MOCK_NOW, **overrides):
    Settings, create_app = _build()
    s = replace(Settings.from_env({"RELAY_MOCK_SCENARIO": scenario, "RELAY_MOCK_NOW": mock_now}),
                log_level="ERROR", **overrides)
    return TestClient(create_app(s, provider=provider))


def _at(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _mock_with_dates(scenario, dates, now=MOCK_NOW):
    from backend.app.contracts import Contracts
    from backend.app.providers import MockProvider
    return MockProvider(Contracts(CONTRACTS), scenario, ignition_dates=dates, now_fn=lambda: _at(now))


class ExpiredSpreadNeedsConfirmation(ApiBase):
    scenario = "expired"

    def test_expired_without_confirm_or_override_is_409_confirmation_required(self):
        r = self.client.post("/api/v1/spread", json=SPREAD_BODY)
        body = self.assert_error(r, 409, "confirmation_required", "/api/v1/spread", "post")
        # expired weather was retrieved 715 min before MOCK_NOW, so the newest cache time is ignition's
        self.assertEqual(body["last_cache_time"], "2026-07-14T20:00:00Z")
        self.assertNotIn("Retry-After", r.headers)
        self.assertNotIn("features", body)

    def test_confirm_false_is_still_409(self):
        r = self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, confirm_degraded=False))
        self.assert_error(r, 409, "confirmation_required", "/api/v1/spread", "post")

    def test_confirm_degraded_runs_and_states_expired(self):
        out = self.check("/api/v1/spread", "post",
                         self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, confirm_degraded=True)))
        self.assertEqual((out["stale_level"], out["inputs"]["stale_flag"]), ("expired", True))
        self.assertNotEqual(out["inputs"]["weather_source"], "manual")

    def test_override_runs_as_manual(self):
        r = self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, weather_override=OVERRIDE))
        out = self.check("/api/v1/spread", "post", r)
        self.assertEqual(out["inputs"]["weather_source"], "manual")
        self.assertEqual((out["stale_level"], out["inputs"]["stale_flag"], out["inputs"]["stale_reason"]),
                         ("stale", True, MANUAL_REASON))
        self.assertEqual(r.headers["X-Stale-Level"], "stale")


class ManualOverride(ApiBase):
    def test_manual_fields_exact_values_echoed_and_omitted_fields_null(self):
        for scenario in ("fresh", "stale", "expired", "no_cache"):
            with self.subTest(scenario=scenario):
                c = _client(scenario)
                r = c.post("/api/v1/spread", json=dict(SPREAD_BODY, weather_override=OVERRIDE))
                out = self.check("/api/v1/spread", "post", r)
                inp = out["inputs"]
                self.assertEqual(inp["weather_source"], "manual")
                self.assertEqual(out["stale_level"], "stale")
                self.assertIs(inp["stale_flag"], True)
                self.assertEqual(inp["stale_reason"], "manual weather override; not live data")
                self.assertEqual((inp["wind_speed_kmh"], inp["wind_direction_deg"], inp["ffmc"], inp["rh_pct"]), (22, 225, 88, 35))
                self.assertEqual((inp["temp_c"], inp["bui"], inp["precip_mm"]), (None, None, None))
                self.assertEqual(inp["weather_valid_time"], SPREAD_BODY["start_time"])
                self.assertNotIn("weather_data_timestamp", inp)
                self.assertEqual((out["model_version"], out["disclaimer"]), ("example-not-a-real-model", DISCLAIMER))
                self.assertIn("run_timestamp", out)

    def test_override_validation(self):
        bad = {
            "not an object": [1, 2],
            "missing wind speed": {"wind_direction_deg": 90},
            "missing direction": {"wind_speed_kmh": 10},
            "direction 361": {"wind_speed_kmh": 10, "wind_direction_deg": 361},
            "negative speed": {"wind_speed_kmh": -1, "wind_direction_deg": 90},
            "rh 101": {"wind_speed_kmh": 10, "wind_direction_deg": 90, "rh_pct": 101},
            "string value": {"wind_speed_kmh": "10", "wind_direction_deg": 90},
            "bool value": {"wind_speed_kmh": True, "wind_direction_deg": 90},
            "unknown field": {"wind_speed_kmh": 10, "wind_direction_deg": 90, "gust": 3},
        }
        for name, ov in bad.items():
            with self.subTest(name):
                self.assert_error(self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, weather_override=ov)),
                                  400, "invalid_request", "/api/v1/spread", "post")
        self.assert_error(self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, confirm_degraded="yes")),
                          400, "invalid_request", "/api/v1/spread", "post")


class NoCacheSpread(ApiBase):
    scenario = "no_cache"

    def test_confirm_degraded_without_cache_is_503_no_cached_data(self):
        r = self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, confirm_degraded=True))
        body = self.assert_error(r, 503, "no_cached_data", "/api/v1/spread", "post")
        self.assertIsNone(body["last_cache_time"])
        self.assertEqual(r.headers["Retry-After"], "900")
        self.assertNotIn("features", body)

    def test_override_without_cache_runs_manual_stale(self):
        r = self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, weather_override=OVERRIDE))
        out = self.check("/api/v1/spread", "post", r)
        self.assertEqual(r.status_code, 200)
        self.assertEqual((out["stale_level"], out["inputs"]["stale_flag"], out["inputs"]["weather_source"],
                          out["inputs"]["stale_reason"]),
                         ("stale", True, "manual", "manual weather override; not live data"))
        self.assertEqual(r.headers["X-Stale-Level"], "stale")

    def test_override_with_confirm_without_cache_still_runs_manual(self):
        r = self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, confirm_degraded=True, weather_override=OVERRIDE))
        self.assertEqual((r.status_code, r.json()["inputs"]["weather_source"]), (200, "manual"))


class SpreadDefaults(ApiBase):
    def test_horizons_default_n_runs_and_seed_echo(self):
        body = {k: v for k, v in SPREAD_BODY.items() if k not in ("n_runs", "seed")}
        out = self.check("/api/v1/spread", "post", self.client.post("/api/v1/spread", json=body))
        self.assertEqual(out["n_runs"], 200)
        self.assertIsInstance(out["seed"], int)
        self.assertTrue(0 <= out["seed"] <= 2**31 - 1)
        pairs = {(f["properties"]["horizon_h"], f["properties"]["band"]) for f in out["features"]}
        self.assertEqual(pairs, {(h, b) for h in (1, 3, 6) for b in ("p10", "p50", "p90")})
        out = self.client.post("/api/v1/spread", json=dict(SPREAD_BODY, seed=7, n_runs=12)).json()
        self.assertEqual((out["seed"], out["n_runs"]), (7, 12))

    def test_default_runs_env(self):
        from backend.app.config import ConfigError, Settings
        self.assertEqual(Settings.from_env({}).spread_default_runs, 200)
        self.assertEqual(Settings.from_env({"RELAY_SPREAD_DEFAULT_RUNS": "50"}).spread_default_runs, 50)
        for v in ("0", "1001", "x"):
            with self.assertRaises(ConfigError):
                Settings.from_env({"RELAY_SPREAD_DEFAULT_RUNS": v})


class IgnitionDatesAndStaleDay(ApiBase):
    def test_dates_endpoint(self):
        r = self.client.get("/api/v1/ignition/dates")
        body = self.check("/api/v1/ignition/dates", "get", r)
        self.assertEqual((body["dates"], body["latest"], body["mock"], body["disclaimer"]), ([DAY], DAY, True, DISCLAIMER))
        self.assertEqual(r.headers["X-Mock-Data"], "true")
        self.assert_error(self.client.get("/api/v1/ignition/dates?x=1"), 400, "invalid_request", "/api/v1/ignition/dates")

    def test_dates_endpoint_no_cache_is_503(self):
        r = _client("no_cache").get("/api/v1/ignition/dates")
        self.assert_error(r, 503, "no_cached_data", "/api/v1/ignition/dates")

    def test_latest(self):
        r = self.client.get("/api/v1/ignition?date=latest")
        rec = self.check("/api/v1/ignition", "get", r)[0]
        self.assertEqual((rec["date"], rec["stale_flag"], rec["stale_level"]), (DAY, False, "fresh"))
        rec = self.check("/api/v1/ignition/{cell_id}", "get", self.client.get(f"/api/v1/ignition/{CELL}?date=latest"))
        self.assertEqual(rec["date"], DAY)

    def test_later_day_serves_latest_map_at_or_before_with_original_date_and_stale_flag(self):
        for url, tpl in ((f"/api/v1/ignition?date=2026-07-17", "/api/v1/ignition"),
                         (f"/api/v1/ignition/{CELL}?date=2026-07-17", "/api/v1/ignition/{cell_id}")):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200)
                body = self.check(tpl, "get", r)
                rec = body[0] if isinstance(body, list) else body
                self.assertEqual(rec["date"], DAY)                       # original date kept
                self.assertEqual((rec["stale_flag"], rec["stale_level"]), (True, "stale"))
                self.assertEqual(rec["age_minutes"], 10.0)               # data age is NOT the day gap
                self.assertIn("2026-07-17", rec["stale_reason"])
                self.assertEqual(r.headers["X-Stale-Level"], "stale")

    def test_regression_age_does_not_depend_on_requested_date(self):
        # BEACON smoke test 2026-11-09: same record came back fresh/0 min for latest but stale/168480 min
        # for an explicit date. Age is now data_timestamp vs the clock in both paths.
        for now, today in (("2026-11-09T21:00:00Z", "2026-11-09"), (MOCK_NOW, "2026-07-14")):
            for scenario in ("fresh", "stale", "expired"):
                with self.subTest(now=now, scenario=scenario):
                    c = _client(scenario, mock_now=now)
                    latest = c.get("/api/v1/ignition?date=latest").json()[0]
                    explicit = c.get(f"/api/v1/ignition?date={max(today, DAY)}").json()[0]
                    cell = c.get(f"/api/v1/ignition/{CELL}?date={max(today, DAY)}").json()
                    for k in ("age_minutes", "stale_level", "stale_flag", "date", "data_timestamp"):
                        self.assertEqual(latest[k], explicit[k], k)
                        self.assertEqual(latest[k], cell[k], k)
        # served 2026-11-09 against the real-like clock: age = now - data_timestamp, expired, flagged
        rec = _client("fresh", mock_now="2026-11-09T21:00:00Z").get("/api/v1/ignition?date=2026-11-09").json()[0]
        self.assertEqual((rec["age_minutes"], rec["stale_level"], rec["stale_flag"]), (169980.0, "expired", True))

    def test_age_is_from_data_timestamp_against_the_clock(self):
        for now, age, level in (("2026-07-14T20:10:00Z", 10.0, "fresh"), ("2026-07-15T11:00:00Z", 900.0, "fresh"),
                                ("2026-07-15T11:01:00Z", 901.0, "stale"), ("2026-07-16T08:01:00Z", 2161.0, "expired")):
            with self.subTest(now=now):
                rec = _client(mock_now=now).get("/api/v1/ignition?date=latest").json()[0]
                self.assertEqual((rec["age_minutes"], rec["stale_level"], rec["stale_flag"]), (age, level, level != "fresh"))

    def test_404_only_when_nothing_at_or_before(self):
        body = self.assert_error(self.client.get("/api/v1/ignition?date=2026-07-14"), 404, "not_found", "/api/v1/ignition")
        self.assertEqual(body["last_cache_time"], CACHE_TIME)
        self.assertEqual(self.client.get(f"/api/v1/ignition?date={DAY}").status_code, 200)

    def test_picks_newest_map_at_or_before_among_several(self):
        c = _client(provider=_mock_with_dates("fresh", ("2026-07-10", "2026-07-15", "2026-07-20")))
        self.assertEqual(c.get("/api/v1/ignition/dates").json()["dates"], ["2026-07-10", "2026-07-15", "2026-07-20"])
        self.assertEqual(c.get("/api/v1/ignition?date=2026-07-17").json()[0]["date"], "2026-07-15")
        self.assertEqual(c.get("/api/v1/ignition?date=2026-07-20").json()[0]["date"], "2026-07-20")
        self.assertEqual(c.get("/api/v1/ignition?date=latest").json()[0]["date"], "2026-07-20")
        self.assertEqual(c.get("/api/v1/ignition?date=2026-07-09").status_code, 404)

    def test_expired_scenario_stays_expired_on_a_stale_day(self):
        rec = _client("expired").get("/api/v1/ignition?date=2026-07-16").json()[0]
        self.assertEqual((rec["stale_level"], rec["stale_flag"]), ("expired", True))
        self.assertEqual(rec["age_minutes"], 720.0)


class IgnitionEnvelope(ApiBase):
    def test_envelope_shape_and_each_record_validates_alone(self):
        ign_schema = json.loads((CONTRACTS / "ignition_output.schema.json").read_text(encoding="utf-8"))
        for date, expect_level in ((DAY, "fresh"), ("2026-07-16", "stale"), ("latest", "fresh")):
            with self.subTest(date=date):
                r = self.client.get(f"/api/v1/ignition?date={date}&envelope=true")
                env = self.check("/api/v1/ignition", "get", r)
                self.assertEqual(set(env), ENVELOPE_KEYS)                 # exactly BEACON's shape
                self.assertEqual((env["requested_date"], env["stale_level"], env["mock"]), (date, expect_level, True))
                self.assertEqual(env["mode"], "retrospective")
                self.assertEqual(env["data_timestamp"], "2026-07-14T20:00:00Z")
                for rec in env["records"]:
                    self.assertEqual(list(Draft202012Validator(ign_schema).iter_errors(rec)), [])
                    self.assertEqual(rec["disclaimer"], DISCLAIMER)
                    self.assertIn("model_version", rec)

    def test_requested_date_echoes_request_and_record_date_is_the_served_day(self):
        c = _client(provider=_mock_with_dates("fresh", ("2026-07-10", "2026-07-15", "2026-07-20")))
        for requested, served in (("latest", "2026-07-20"), ("2026-07-17", "2026-07-15"), ("2026-07-20", "2026-07-20"),
                                  ("2026-08-01", "2026-07-20")):
            with self.subTest(requested=requested):
                env = c.get(f"/api/v1/ignition?date={requested}&envelope=true").json()
                self.assertEqual(env["requested_date"], requested)          # "latest" stays "latest"
                self.assertEqual({r["date"] for r in env["records"]}, {served})
                self.assertEqual(all(r["stale_flag"] for r in env["records"]),
                                 requested != "latest" and served < requested or env["stale_level"] != "fresh")

    def test_default_is_bare_array_and_bad_flag_is_400(self):
        self.assertIsInstance(self.client.get(f"/api/v1/ignition?date={DAY}&envelope=false").json(), list)
        self.assert_error(self.client.get(f"/api/v1/ignition?date={DAY}&envelope=yes"), 400, "invalid_request", "/api/v1/ignition")


class ErrorModel(ApiBase):
    def test_every_error_has_request_id_header_and_mock_headers(self):
        for r in (self.client.get("/nope"), self.client.get("/api/v1/ignition?date=bad"),
                  self.client.post("/api/v1/spread", json={})):
            with self.subTest(status=r.status_code):
                self.assertEqual(r.json()["request_id"], r.headers["X-Request-ID"])
                self.assertEqual((r.headers["X-Mock-Data"], r.headers["X-Data-Mode"]), ("true", "mock"))
                self.assertEqual(set(r.json()), {"status", "error", "reason", "last_cache_time", "request_id", "disclaimer"})

    def test_provider_failure_is_503_with_retry_after_and_cache_time(self):
        from backend.app.contracts import Contracts
        from backend.app.providers import MockProvider

        class P(MockProvider):
            def latest_weather(self, *a):
                raise RuntimeError("boom")
        r = _client(provider=P(Contracts(CONTRACTS), now_fn=lambda: _at(MOCK_NOW))).get("/api/v1/weather/latest?lat=50&lon=-90")
        body = self.assert_error(r, 503, "provider_unavailable", "/api/v1/weather/latest")
        self.assertEqual((body["last_cache_time"], r.headers["Retry-After"]), (CACHE_TIME, "60"))
        self.assertNotIn("retryable", body)


class GzipAndCors(ApiBase):
    N = 9673

    @classmethod
    def synthetic_provider(cls, n):
        """n SYNTHETIC mock records (example record, one per distinct cell): size test only, not data."""
        from backend.app.cache_policy import StaleLevel
        from backend.app.contracts import Contracts
        from backend.app.providers import MockProvider, Served

        class P(MockProvider):
            def ignition_for_date(self, date, top_k):
                base = self._ign
                recs = [dict(base, cell_id=f"ofr10km_{-950000 + 10000 * (i % 110)}_{100000 + 10000 * (i // 110)}",
                             probability=round(0.0002 + (i % 997) * 1e-6, 7), rank_in_day=i + 1) for i in range(n)]
                return Served(recs, StaleLevel.FRESH, base["data_timestamp"])
        return P(Contracts(CONTRACTS))

    def test_gzip_on_large_ignition_list_and_headers_survive(self):
        c = _client(provider=self.synthetic_provider(self.N), validate_responses=False)
        raw = c.get(f"/api/v1/ignition?date={DAY}", headers={"Accept-Encoding": "identity"})
        gz = c.get(f"/api/v1/ignition?date={DAY}", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("content-encoding", raw.headers)
        self.assertEqual(gz.headers["content-encoding"], "gzip")
        self.assertEqual(len(gz.json()), self.N)
        self.assertEqual((gz.headers["X-Mock-Data"], gz.headers["X-Stale-Level"]), ("true", "fresh"))
        self.assertIn("X-Request-ID", gz.headers)
        self.assertLess(int(gz.headers["content-length"]), len(raw.content) / 5)

    def test_small_responses_are_not_compressed(self):
        r = self.client.get("/healthz", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("content-encoding", r.headers)

    def test_cors_for_beacon_dev_origins(self):
        from backend.app.config import Settings
        origins = "http://localhost:5173,http://127.0.0.1:5173"
        s = Settings.from_env({"RELAY_CORS_ORIGINS": origins})
        self.assertEqual(s.cors_origins, ("http://localhost:5173", "http://127.0.0.1:5173"))
        c = _client(cors_origins=s.cors_origins)
        for o in s.cors_origins:
            r = c.get("/healthz", headers={"Origin": o})
            self.assertEqual(r.headers["access-control-allow-origin"], o)
            self.assertIn("Retry-After", r.headers["access-control-expose-headers"])
        self.assertNotIn("access-control-allow-origin", c.get("/healthz", headers={"Origin": "http://evil.test"}).headers)


class NoCacheNeverInResponses(unittest.TestCase):
    """Team Lead ruling: stale_level is fresh|stale|expired only; "no_cache" never appears as a value."""

    REQUESTS = [("GET", "/healthz", None), ("GET", "/api/v1/ingestion/status", None),
                ("GET", "/api/v1/ignition/dates", None), ("GET", f"/api/v1/ignition?date={DAY}", None),
                ("GET", "/api/v1/ignition?date=latest&envelope=true", None),
                ("GET", "/api/v1/ignition?date=2026-07-20&envelope=true", None),
                ("GET", f"/api/v1/ignition/{CELL}?date={DAY}", None),
                ("GET", "/api/v1/weather/latest?lat=50&lon=-90", None),
                ("POST", "/api/v1/spread", SPREAD_BODY),
                ("POST", "/api/v1/spread", dict(SPREAD_BODY, confirm_degraded=True)),
                ("POST", "/api/v1/spread", dict(SPREAD_BODY, weather_override=OVERRIDE)),
                ("GET", "/api/v1/ignition?date=2026-07-01", None), ("GET", "/nope", None)]

    @staticmethod
    def stale_values(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "stale_level":
                    yield v
                yield from NoCacheNeverInResponses.stale_values(v)
        elif isinstance(node, list):
            for v in node:
                yield from NoCacheNeverInResponses.stale_values(v)

    @unittest.skipIf(MISSING, "backend dependencies missing")
    def test_no_cache_never_appears_as_a_stale_level(self):
        for scenario in ("fresh", "stale", "expired", "no_cache"):
            for now in (MOCK_NOW, "2026-11-09T21:00:00Z"):
                c = _client(scenario, mock_now=now)
                for method, url, body in self.REQUESTS:
                    with self.subTest(scenario=scenario, now=now, url=url, body=body):
                        r = c.request(method, url, json=body)
                        values = set(self.stale_values(r.json()))
                        self.assertLessEqual(values, {"fresh", "stale", "expired"})
                        self.assertNotIn("no_cache", values)
                        self.assertNotEqual(r.headers.get("X-Stale-Level"), "no_cache")
                        if url == "/api/v1/ingestion/status":
                            for s in r.json()["sources"]:
                                self.assertEqual(s["stale_flag"], s["stale_level"] != "fresh")


def measure_gzip(n=GzipAndCors.N):
    """Raw vs gzip size of a synthetic n-record ignition list (used for the openapi x-gzip-measurement)."""
    c = _client(provider=GzipAndCors.synthetic_provider(n), validate_responses=False)
    raw = c.get(f"/api/v1/ignition?date={DAY}", headers={"Accept-Encoding": "identity"})
    gz = c.get(f"/api/v1/ignition?date={DAY}", headers={"Accept-Encoding": "gzip"})
    wire = int(gz.headers["content-length"])
    assert len(gzip.compress(raw.content)) > 0
    return {"records": n, "raw_bytes": len(raw.content), "gzip_bytes": wire,
            "ratio": round(wire / len(raw.content), 4)}


if __name__ == "__main__":
    unittest.main()
