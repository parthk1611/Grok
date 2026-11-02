import json
import tempfile
import unittest
from pathlib import Path

from backend.app.atomic_io import atomic_write_json
from backend.app.config import ConfigError, Settings


class AtomicWriteTests(unittest.TestCase):
    def test_writes_and_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "cache.json"
            atomic_write_json(p, {"a": 1})
            atomic_write_json(p, {"a": 2})
            self.assertEqual(json.loads(p.read_text()), {"a": 2})
            self.assertEqual([x.name for x in p.parent.iterdir()], ["cache.json"])   # no .partial left

    def test_failed_write_keeps_previous_good_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cache.json"
            atomic_write_json(p, {"good": True})
            with self.assertRaises(ValueError):
                atomic_write_json(p, {"bad": float("nan")})      # NaN is refused
            self.assertEqual(json.loads(p.read_text()), {"good": True})
            self.assertEqual([x.name for x in Path(d).iterdir()], ["cache.json"])


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        s = Settings.from_env({})
        self.assertEqual((s.data_mode, s.mock_scenario, s.host, s.port, s.cors_origins), ("mock", "fresh", "127.0.0.1", 8000, ()))
        self.assertTrue(s.validate_responses)

    def test_overrides(self):
        s = Settings.from_env({"RELAY_PORT": "9000", "RELAY_MOCK_SCENARIO": "STALE", "RELAY_CORS_ORIGINS": "http://a.test, http://b.test"})
        self.assertEqual((s.port, s.mock_scenario, s.cors_origins), (9000, "stale", ("http://a.test", "http://b.test")))

    def test_rejects_bad_values(self):
        for env in ({"RELAY_PORT": "abc"}, {"RELAY_PORT": "70000"}, {"RELAY_DATA_MODE": "prod"},
                    {"RELAY_MOCK_SCENARIO": "bogus"}, {"RELAY_LOG_LEVEL": "LOUD"},
                    {"RELAY_CORS_ORIGINS": "*"}, {"RELAY_VALIDATE_RESPONSES": "maybe"}):
            with self.subTest(env=env), self.assertRaises(ConfigError):
                Settings.from_env(env)


if __name__ == "__main__":
    unittest.main()
