"""API tests. Every response is validated against contracts/openapi.yaml (which $refs the contract JSON schemas)."""
import io
import json
import logging
import time
import unittest
from dataclasses import replace
from pathlib import Path

try:
    import yaml
    from fastapi.testclient import TestClient
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    from jsonschema import Draft202012Validator
    MISSING = None
except ImportError as exc:  # keep `unittest discover` usable without backend deps
    MISSING = str(exc)

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SPEC_URI = (CONTRACTS / "openapi.yaml").resolve().as_uri()
CELL = "ofr10km_-10000_100000"
DAY = "2026-07-15"
DISCLAIMER = "Research estimate for decision support only. It does not replace official fire alerts, warnings or evacuation orders."
MOCK_NOW = "2026-07-14T20:10:00Z"        # pinned mock clock: example ignition data_timestamp + 10 min
CACHE_TIME = "2026-07-15T16:05:00Z"      # newest cached time in contracts/examples (weather retrieved_at)
MANUAL_REASON = "manual weather override; not live data"
SPREAD_BODY = {"longitude": -85.0, "latitude": 50.0, "start_time": "2026-11-02T12:00:00Z", "n_runs": 100, "seed": 385}


def _build():
    from backend.app.config import Settings
    from backend.app.main import create_app
    return Settings, create_app


if MISSING is None:
    SPEC = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))

    def _retrieve(uri):
        path = Path(uri.removeprefix("file://"))
        return Resource.from_contents(json.loads(path.read_text(encoding="utf-8")), default_specification=DRAFT202012)

    REGISTRY = Registry(retrieve=_retrieve).with_resource(
        SPEC_URI, Resource.from_contents(SPEC, default_specification=DRAFT202012))

    def spec_validator(path, method, status):
        esc = path.replace("~", "~0").replace("/", "~1")
        base = f"#/paths/{esc}/{method}/responses/{status}"
        node = SPEC["paths"][path][method]["responses"][str(status)]
        if "$ref" in node:
            base = node["$ref"]
        # the resolver starts at the openapi.yaml resource, so internal and external $refs both resolve
        return Draft202012Validator({"$ref": SPEC_URI + base + "/content/application~1json/schema"},
                                    registry=REGISTRY)

    def spec_headers(path, method, status):
        node = SPEC["paths"][path][method]["responses"][str(status)]
        return {k: v for k, v in node.get("headers", {}).items()}


@unittest.skipIf(MISSING, f"backend dependencies missing: {MISSING}; pip install -r backend/requirements-dev.txt")
class ApiBase(unittest.TestCase):
    scenario = "fresh"
    overrides = {}

    @classmethod
    def setUpClass(cls):
        Settings, create_app = _build()
        s = replace(Settings.from_env({"RELAY_MOCK_SCENARIO": cls.scenario, "RELAY_MOCK_NOW": MOCK_NOW}),
                    log_level="ERROR", **cls.overrides)
        cls.app = create_app(s)
        cls.client = TestClient(cls.app)

    def check(self, path_tpl, method, resp, status=None):
        status = status or resp.status_code
        errs = list(spec_validator(path_tpl, method, status).iter_errors(resp.json()))
        self.assertEqual(errs, [], f"{path_tpl} {status} violates openapi.yaml: {[e.message for e in errs[:2]]}")
        return resp.json()

    def assert_error(self, resp, status, reason, path_tpl, method="get"):
        self.assertEqual(resp.status_code, status, resp.text)
        body = resp.json()
        for k in ("error", "reason", "last_cache_time", "request_id", "disclaimer"):
            self.assertIn(k, body)
        self.assertEqual(body["reason"], reason)
        self.assertEqual(body["disclaimer"], DISCLAIMER)
        self.assertEqual(body["request_id"], resp.headers["X-Request-ID"])
        if path_tpl and str(status) in SPEC["paths"][path_tpl][method]["responses"]:
            self.check(path_tpl, method, resp)
        return body


class SpecTests(ApiBase):
    def test_openapi_is_a_valid_31_document(self):
        from openapi_spec_validator import validate
        validate(SPEC, base_uri=SPEC_URI)
        self.assertEqual(SPEC["openapi"], "3.1.0")
        self.assertIn("draft", SPEC["info"]["version"])
        self.assertEqual(SPEC["info"]["version"], "0.2.1-draft")
        from backend.app import __version__
        self.assertEqual(__version__, SPEC["info"]["version"])

    def test_error_reason_enum_is_closed_and_matches_the_server(self):
        from backend.app.errors import REASONS, STATUSES
        err = SPEC["components"]["schemas"]["ErrorResponse"]
        self.assertEqual(set(err["properties"]["reason"]["enum"]), set(REASONS))
        self.assertEqual(set(err["properties"]["status"]["enum"]), set(STATUSES))
        self.assertNotIn("retryable", json.dumps(SPEC))           # Team Lead: dropped, error shape closed
        self.assertFalse(err["additionalProperties"])
        self.assertNotIn("400/404", json.dumps(SPEC))
        self.assertIn("confirmation_required", REASONS)
        self.assertEqual(REASONS["confirmation_required"][0], 409)
        self.assertEqual(REASONS["spread_timeout"][0], 504)
        self.assertEqual({r for r, v in REASONS.items() if v[0] == 503},
                         {"no_cached_data", "provider_unavailable", "invalid_provider_payload"})

    def test_cr5_dropped_and_proposals_documented(self):
        ids = [c["id"] for c in SPEC["x-change-requests"]]
        self.assertNotIn("CR-5", ids)
        self.assertIn("weather-latest-selection", SPEC["x-proposals"])
        desc = SPEC["info"]["description"]
        for needle in ("RELAY_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173", "get_app --factory", "gzip"):
            self.assertIn(needle, desc)

    def test_source_enum_and_required_source_status_fields(self):
        schemas = SPEC["components"]["schemas"]
        self.assertEqual(schemas["SourceName"]["enum"], ["hrdps", "cwfis_firewx_stns", "swob_realtime"])
        req = set(schemas["SourceStatus"]["required"])
        for f in ("mock", "last_attempt_at", "last_error_at", "age_minutes", "fetch_age_minutes", "next_poll_at"):
            self.assertIn(f, req)
        self.assertIn("mock", schemas["IngestionStatus"]["required"])

    def test_every_documented_route_is_implemented_and_vice_versa(self):
        implemented = {(r.path, m.lower()) for r in self.app.routes if hasattr(r, "methods")
                       for m in r.methods if m not in ("HEAD", "OPTIONS")}
        documented = {(p, m) for p, ops in SPEC["paths"].items() for m in ops}
        self.assertEqual(implemented, documented)

    def test_every_error_response_documents_the_required_503_fields(self):
        err = SPEC["components"]["schemas"]["ErrorResponse"]
        for f in ("error", "reason", "last_cache_time", "disclaimer"):
            self.assertIn(f, err["required"])
        self.assertEqual(err["properties"]["disclaimer"]["const"], DISCLAIMER)
        self.assertIn("null", err["properties"]["last_cache_time"]["type"])


class FreshMockTests(ApiBase):
    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        body = self.check("/healthz", "get", r)
        self.assertEqual((body["status"], body["mode"], body["mock"], body["contract_version"]), ("ok", "mock", True, "0.2.1"))
        self.assertEqual(body["api_version"], "0.2.1-draft")

    def test_ignition_list_validates_and_is_labelled_mock(self):
        r = self.client.get(f"/api/v1/ignition?date={DAY}")
        recs = self.check("/api/v1/ignition", "get", r)
        self.assertIsInstance(recs, list)
        self.assertEqual(recs[0]["model_version"], "example-not-a-real-model")
        self.assertEqual((r.headers["X-Mock-Data"], r.headers["X-Data-Mode"], r.headers["X-Stale-Level"]), ("true", "mock", "fresh"))
        self.assertEqual(r.headers["X-Data-Timestamp"], recs[0]["data_timestamp"])
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        for h in spec_headers("/api/v1/ignition", "get", 200):
            self.assertIn(h, r.headers)

    def test_ignition_top_k(self):
        self.assertEqual(len(self.client.get(f"/api/v1/ignition?date={DAY}&top_k=1").json()), 1)

    def test_ignition_cell(self):
        r = self.client.get(f"/api/v1/ignition/{CELL}?date={DAY}")
        rec = self.check("/api/v1/ignition/{cell_id}", "get", r)
        self.assertEqual((rec["cell_id"], rec["stale_flag"], rec["stale_level"]), (CELL, False, "fresh"))

    def test_spread_moves_example_to_requested_point_and_validates(self):
        r = self.client.post("/api/v1/spread", json=SPREAD_BODY)
        out = self.check("/api/v1/spread", "post", r)
        self.assertEqual(out["ignition_point"], {"longitude": -85.0, "latitude": 50.0})
        self.assertEqual((out["start_time"], out["n_runs"], out["seed"]), ("2026-11-02T12:00:00Z", 100, 385))
        self.assertEqual(r.headers["X-Mock-Data"], "true")
        first = out["features"][0]["geometry"]["coordinates"][0][0]
        self.assertAlmostEqual(first[0], -85.0)
        self.assertAlmostEqual(first[1], 50.0)
        for k in ("mode", "stale_level", "age_minutes", "validated_horizons_h", "uncertainty_status", "perturbation"):
            self.assertIn(k, out)

    def test_spread_optional_fields_may_be_omitted(self):
        body = {k: v for k, v in SPREAD_BODY.items() if k not in ("n_runs", "seed")}
        self.check("/api/v1/spread", "post", self.client.post("/api/v1/spread", json=body))

    def test_weather_by_point_and_by_cell(self):
        r = self.client.get("/api/v1/weather/latest?lat=49.5&lon=-86.25")
        rec = self.check("/api/v1/weather/latest", "get", r)
        self.assertEqual((rec["latitude"], rec["longitude"]), (49.5, -86.25))
        self.assertTrue(rec["quality_flags"][0].startswith("MOCK"))
        rec = self.check("/api/v1/weather/latest", "get", self.client.get(f"/api/v1/weather/latest?cell_id={CELL}"))
        self.assertEqual(rec["cell_id"], CELL)

    def test_ingestion_status(self):
        r = self.client.get("/api/v1/ingestion/status")
        body = self.check("/api/v1/ingestion/status", "get", r)
        self.assertTrue(body["mock"])
        self.assertTrue(body["cache_policy"]["proposal"])
        self.assertTrue(body["cache_policy"]["data_age_thresholds_tbd"])
        self.assertEqual(body["cache_policy"]["data_age_thresholds_provisional"], ["hrdps"])
        self.assertEqual(body["cache_policy"]["data_age"]["hrdps"], {"fresh_max_minutes": 600, "stale_max_minutes": 720})
        self.assertEqual([s["source"] for s in body["sources"]], ["hrdps", "cwfis_firewx_stns", "swob_realtime"])
        for s in body["sources"]:
            self.assertEqual((s["stale_level"], s["consecutive_failures"], s["last_error"], s["last_error_at"]),
                             ("fresh", 0, None, None))
            self.assertTrue(s["mock"])
            self.assertGreater(s["next_poll_at"], s["last_attempt_at"])
            self.assertEqual((s["age_minutes"], s["fetch_age_minutes"]), (10.0, 10.0))

    def test_request_id_generated_echoed_and_sanitised(self):
        r = self.client.get("/healthz")
        self.assertRegex(r.headers["X-Request-ID"], r"^[0-9a-f]{32}$")
        r = self.client.get("/healthz", headers={"X-Request-ID": "abc-12345.trace"})
        self.assertEqual(r.headers["X-Request-ID"], "abc-12345.trace")
        r = self.client.get("/healthz", headers={"X-Request-ID": "bad id\twith spaces"})
        self.assertRegex(r.headers["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_no_cors_headers_by_default_and_no_generated_docs(self):
        r = self.client.get("/healthz", headers={"Origin": "http://evil.test"})
        self.assertNotIn("access-control-allow-origin", r.headers)
        for p in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(p).status_code, 404)


class BadRequestTests(ApiBase):
    def test_query_validation(self):
        cases = [
            ("/api/v1/ignition", "/api/v1/ignition"),
            (f"/api/v1/ignition?date=2026-02-30", None),
            ("/api/v1/ignition?date=15-07-2026", None),
            (f"/api/v1/ignition?date={DAY}&date={DAY}", None),
            (f"/api/v1/ignition?date={DAY}&extra=1", None),
            (f"/api/v1/ignition?date={DAY}&top_k=0", None),
            (f"/api/v1/ignition?date={DAY}&top_k=abc", None),
            (f"/api/v1/ignition?date={DAY}&top_k=10001", None),
            (f"/api/v1/ignition/not_a_cell?date={DAY}", None),
            (f"/api/v1/ignition/{CELL}", None),
            ("/api/v1/weather/latest", None),
            ("/api/v1/weather/latest?lat=50", None),
            (f"/api/v1/weather/latest?lat=50&lon=-90&cell_id={CELL}", None),
            ("/api/v1/weather/latest?lat=abc&lon=-90", None),
            ("/api/v1/weather/latest?lat=nan&lon=-90", None),
            ("/api/v1/weather/latest?cell_id=ofr10km_x_y", None),
        ]
        for url, _ in cases:
            with self.subTest(url=url):
                r = self.client.get(url)
                body = self.assert_error(r, 400, "invalid_request", None)
                self.assertEqual(body["last_cache_time"], CACHE_TIME)      # filled from the cache on every error
                self.assertNotIn("Retry-After", r.headers)
                self.check("/api/v1/ignition", "get", r, 400)

    def test_out_of_domain_point(self):
        for url in ("/api/v1/weather/latest?lat=10&lon=-90", "/api/v1/weather/latest?lat=50&lon=10"):
            with self.subTest(url=url):
                self.assert_error(self.client.get(url), 400, "out_of_domain", "/api/v1/weather/latest")
        body = dict(SPREAD_BODY, latitude=70.0)
        self.assert_error(self.client.post("/api/v1/spread", json=body), 400, "out_of_domain", "/api/v1/spread", "post")

    def test_spread_body_validation(self):
        bad = {
            "missing longitude": {k: v for k, v in SPREAD_BODY.items() if k != "longitude"},
            "missing start_time": {k: v for k, v in SPREAD_BODY.items() if k != "start_time"},
            "extra field": dict(SPREAD_BODY, wind=1),
            "string lon": dict(SPREAD_BODY, longitude="-85"),
            "bool lat": dict(SPREAD_BODY, latitude=True),
            "start in 2101": dict(SPREAD_BODY, start_time="2101-06-16T11:58:16Z"),
            "start before 2000": dict(SPREAD_BODY, start_time="1999-01-01T00:00:00Z"),
            "start no Z": dict(SPREAD_BODY, start_time="2026-11-02T12:00:00+00:00"),
            "n_runs 0": dict(SPREAD_BODY, n_runs=0),
            "n_runs huge": dict(SPREAD_BODY, n_runs=10**6),
            "n_runs float": dict(SPREAD_BODY, n_runs=5.5),
            "seed negative": dict(SPREAD_BODY, seed=-1),
            "seed bool": dict(SPREAD_BODY, seed=True),
        }
        for name, body in bad.items():
            with self.subTest(name):
                self.assert_error(self.client.post("/api/v1/spread", json=body), 400, "invalid_request", "/api/v1/spread", "post")
        for name, raw in {"not json": b"{nope", "array": b"[1]", "too big": b" " * 5000, "empty": b""}.items():
            with self.subTest(name):
                r = self.client.post("/api/v1/spread", content=raw, headers={"Content-Type": "application/json"})
                self.assert_error(r, 400, "invalid_request", "/api/v1/spread", "post")

    def test_not_found_and_method_errors_use_the_error_model(self):
        for r, st, reason, tpl, m in (
                (self.client.get("/nope"), 404, "route_not_found", None, "get"),
                (self.client.get("/api/v1/ignition?date=2026-07-14"), 404, "not_found", "/api/v1/ignition", "get"),
                (self.client.get(f"/api/v1/ignition/ofr10km_0_0?date={DAY}"), 404, "not_found", "/api/v1/ignition/{cell_id}", "get"),
                (self.client.post("/healthz"), 405, "method_not_allowed", None, "get")):
            with self.subTest(reason=reason, status=st):
                body = self.assert_error(r, st, reason, tpl, m)
                self.assertEqual(body["last_cache_time"], CACHE_TIME)


class ScenarioTests(unittest.TestCase):
    pass


def _scenario_case(level, age):
    class _Case(ApiBase):
        scenario = level

        def test_ignition_staleness_is_consistent(self):
            r = self.client.get(f"/api/v1/ignition?date={DAY}")
            rec = self.check("/api/v1/ignition", "get", r)[0]
            self.assertEqual((rec["stale_level"], rec["stale_flag"], rec["age_minutes"]), (level, True, age))
            self.assertIn("MOCK", rec["stale_reason"])
            self.assertEqual(r.headers["X-Stale-Level"], level)

        def test_spread_staleness_is_consistent(self):
            body = dict(SPREAD_BODY, confirm_degraded=True) if level == "expired" else SPREAD_BODY
            out = self.check("/api/v1/spread", "post", self.client.post("/api/v1/spread", json=body))
            self.assertEqual((out["stale_level"], out["inputs"]["stale_flag"], out["age_minutes"]), (level, True, age))

        def test_weather_and_ingestion_status(self):
            rec = self.check("/api/v1/weather/latest", "get", self.client.get("/api/v1/weather/latest?lat=50&lon=-90"))
            self.assertEqual((rec["stale_level"], rec["from_cache"], rec["age_minutes"]), (level, True, age))
            st = self.check("/api/v1/ingestion/status", "get", self.client.get("/api/v1/ingestion/status"))
            self.assertTrue(all(s["stale_level"] == level and s["consecutive_failures"] > 0 for s in st["sources"]))
    _Case.__name__ = f"Scenario_{level}"
    return _Case


Scenario_stale = _scenario_case("stale", 120.0)
Scenario_expired = _scenario_case("expired", 720.0)


class Scenario_no_cache(ApiBase):
    scenario = "no_cache"

    def test_503_only_body_has_error_reason_last_cache_time_and_disclaimer(self):
        cases = [("get", "/api/v1/ignition", f"/api/v1/ignition?date={DAY}", None),
                 ("get", "/api/v1/ignition/{cell_id}", f"/api/v1/ignition/{CELL}?date={DAY}", None),
                 ("get", "/api/v1/weather/latest", "/api/v1/weather/latest?lat=50&lon=-90", None),
                 ("post", "/api/v1/spread", "/api/v1/spread", SPREAD_BODY)]
        for method, tpl, url, body in cases:
            with self.subTest(url=url):
                r = self.client.request(method.upper(), url, json=body)
                err = self.assert_error(r, 503, "no_cached_data", tpl, method)
                self.assertIsNone(err["last_cache_time"])
                self.assertEqual(err["status"], 503)
                self.assertEqual(r.headers["Retry-After"], "900")
                self.assertNotIn("probability", json.dumps(err))        # nothing invented

    def test_status_endpoint_still_answers(self):
        st = self.check("/api/v1/ingestion/status", "get", self.client.get("/api/v1/ingestion/status"))
        for s in st["sources"]:
            self.assertEqual((s["stale_level"], s["stale_flag"], s["last_success_retrieved_at"], s["last_source_time"],
                              s["age_minutes"], s["fetch_age_minutes"]), ("expired", True, None, None, None, None))

    def test_healthz_unaffected(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)


@unittest.skipIf(MISSING, "backend dependencies missing")
class FailClosedTests(unittest.TestCase):
    def make(self, provider, **overrides):
        Settings, create_app = _build()
        s = replace(Settings.from_env({}), log_level="ERROR", **overrides)
        return TestClient(create_app(s, provider=provider))

    def mock(self, fn_name, fn, **kw):
        from backend.app.config import Settings
        from backend.app.contracts import Contracts
        from backend.app.providers import MockProvider

        class P(MockProvider):
            pass
        setattr(P, fn_name, fn)
        return P(Contracts(CONTRACTS))

    def test_schema_invalid_payload_is_never_served(self):
        def bad(self, date, top_k):
            from backend.app.providers import Served
            from backend.app.cache_policy import StaleLevel
            rec = dict(self._ign, probability=1.5)       # the prototype's "invalid_saved_probability" case
            return Served([rec], StaleLevel.FRESH, rec["data_timestamp"])
        r = self.make(self.mock("ignition_for_date", bad)).get(f"/api/v1/ignition?date={DAY}")
        self.assertEqual((r.status_code, r.json()["reason"]), (503, "invalid_provider_payload"))
        self.assertNotIn("1.5", r.text)
        self.assertEqual(r.json()["disclaimer"], DISCLAIMER)

    def test_stale_flag_disagreeing_with_stale_level_is_never_served(self):
        def bad(self, date, top_k):
            from backend.app.providers import Served
            from backend.app.cache_policy import StaleLevel
            rec = dict(self._ign, stale_level="stale", stale_flag=False)
            return Served([rec], StaleLevel.STALE, rec["data_timestamp"])
        r = self.make(self.mock("ignition_for_date", bad)).get(f"/api/v1/ignition?date={DAY}")
        self.assertEqual((r.status_code, r.json()["reason"]), (503, "invalid_provider_payload"))

    def test_provider_exception_becomes_503_without_leaking_internals(self):
        def boom(self, date, top_k):
            raise RuntimeError("secret-path /home/x/token=abc123")
        r = self.make(self.mock("ignition_for_date", boom)).get(f"/api/v1/ignition?date={DAY}")
        self.assertEqual((r.status_code, r.json()["reason"]), (503, "provider_unavailable"))
        self.assertNotIn("abc123", r.text)
        self.assertIn("last_cache_time", r.json())

    def test_unhandled_error_in_a_route_is_500_with_request_id(self):
        # an error outside the provider-failure path (response rendering) must still fail closed
        def bad_status(self, now):
            return object()          # not JSON-serialisable -> failure in response rendering
        c = self.make(self.mock("ingestion_status", bad_status))
        r = c.get("/api/v1/ingestion/status")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()["reason"], "internal_error")
        self.assertEqual(r.json()["request_id"], r.headers["X-Request-ID"])

    def test_runtime_validation_can_be_disabled_for_perf(self):
        def bad(self, date, top_k):
            from backend.app.providers import Served
            from backend.app.cache_policy import StaleLevel
            return Served([dict(self._ign, probability=1.5)], StaleLevel.FRESH, None)
        r = self.make(self.mock("ignition_for_date", bad), validate_responses=False).get(f"/api/v1/ignition?date={DAY}")
        self.assertEqual(r.status_code, 200)   # documented escape hatch; default is on

    def test_spread_timeout_returns_504(self):
        def slow(self, req):
            time.sleep(0.5)
            return MockProviderBase.run_spread(self, req)
        from backend.app.providers import MockProvider as MockProviderBase
        c = self.make(self.mock("run_spread", slow), spread_timeout_seconds=0.05)
        r = c.post("/api/v1/spread", json=SPREAD_BODY)
        self.assertEqual((r.status_code, r.json()["reason"]), (504, "spread_timeout"))
        self.assertEqual(r.json()["last_cache_time"], CACHE_TIME)
        self.assertEqual(r.headers["Retry-After"], "60")
        self.assertEqual(r.json()["disclaimer"], DISCLAIMER)
        time.sleep(0.5)    # let the worker thread finish

    def test_live_mode_refuses_to_start(self):
        Settings, create_app = _build()
        with self.assertRaises(RuntimeError):
            create_app(replace(Settings.from_env({}), data_mode="live", log_level="ERROR"))

    def test_spread_timeout_default_is_60s(self):
        from backend.app.config import Settings
        self.assertEqual(Settings.from_env({}).spread_timeout_seconds, 60.0)


@unittest.skipIf(MISSING, "backend dependencies missing")
class LoggingTests(unittest.TestCase):
    def test_structured_json_access_log_with_request_id_and_no_query_string(self):
        from backend.app.logging_setup import JsonFormatter
        Settings, create_app = _build()
        app = create_app(replace(Settings.from_env({}), log_level="INFO"))
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(JsonFormatter())
        logging.getLogger("relay").addHandler(h)
        try:
            r = TestClient(app).get("/api/v1/weather/latest?lat=50.123&lon=-90.456", headers={"X-Request-ID": "trace-0001-xyz"})
        finally:
            logging.getLogger("relay").removeHandler(h)
        lines = [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]
        access = [x for x in lines if x.get("event") == "access"]
        self.assertEqual(len(access), 1)
        a = access[0]
        self.assertEqual((a["request_id"], a["method"], a["path"], a["status"], a["data_mode"]),
                         ("trace-0001-xyz", "GET", "/api/v1/weather/latest", 200, "mock"))
        self.assertIn("duration_ms", a)
        self.assertNotIn("50.123", buf.getvalue())          # query values are not logged
        self.assertEqual(r.headers["X-Request-ID"], "trace-0001-xyz")


if __name__ == "__main__":
    unittest.main()
