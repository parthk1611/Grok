"""Validate the handoff contracts in /contracts (JSON Schema draft 2020-12).

Run: python -m unittest tests.test_contracts -v   (needs jsonschema, see
contracts/requirements-contracts.txt)
"""
import json
import unittest
from pathlib import Path

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except ImportError:  # keep `unittest discover` usable without the optional dependency
    jsonschema = None

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
EXAMPLES = CONTRACTS / "examples"

PAIRS = {
    "ignition_output": ("ignition_output.schema.json", "ignition_output.example.json",
                        ["bad_ignition_probability.json", "bad_ignition_no_disclaimer.json"]),
    "spread_output": ("spread_output.schema.json", "spread_output.example.json",
                      ["bad_spread_horizon.json"]),
    "live_weather_record": ("live_weather_record.schema.json", "live_weather_record.example.json",
                            ["bad_weather_stale_level.json", "bad_weather_forecast_no_run_time.json"]),
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@unittest.skipIf(jsonschema is None, "jsonschema not installed: pip install -r contracts/requirements-contracts.txt")
class ContractTests(unittest.TestCase):
    def test_schemas_are_valid(self):
        for name, (schema_file, _, _) in PAIRS.items():
            with self.subTest(name):
                Draft202012Validator.check_schema(load(CONTRACTS / schema_file))

    def test_good_examples_validate(self):
        for name, (schema_file, good, _) in PAIRS.items():
            with self.subTest(name):
                Draft202012Validator(load(CONTRACTS / schema_file)).validate(load(EXAMPLES / good))

    def test_bad_examples_are_rejected(self):
        for name, (schema_file, _, bad_files) in PAIRS.items():
            validator = Draft202012Validator(load(CONTRACTS / schema_file))
            for bad in bad_files:
                with self.subTest(bad):
                    self.assertFalse(validator.is_valid(load(EXAMPLES / bad)))

    def test_every_output_requires_disclaimer_and_version(self):
        for name in ("ignition_output", "spread_output"):
            schema = load(CONTRACTS / PAIRS[name][0])
            self.assertIn("disclaimer", schema["required"])
            self.assertIn("model_version", schema["required"])

    def test_ignition_required_fields_match_contract(self):
        schema = load(CONTRACTS / "ignition_output.schema.json")
        for field in ("cell_id", "date", "probability", "model_version", "data_timestamp", "stale_flag"):
            self.assertIn(field, schema["required"])

    def test_spread_example_has_all_horizons_and_bands(self):
        example = load(EXAMPLES / "spread_output.example.json")
        seen = {(f["properties"]["horizon_h"], f["properties"]["band"]) for f in example["features"]}
        self.assertEqual(seen, {(h, b) for h in (1, 3, 6) for b in ("p10", "p50", "p90")})

    def test_example_cell_id_exists_in_repo_grid(self):
        grid = (CONTRACTS.parent / "data" / "interim" / "ontario_fire_region_10km_grid.csv").read_text()
        cell = load(EXAMPLES / "ignition_output.example.json")["cell_id"]
        self.assertIn(cell + ",", grid)

    def test_v020_fields_required(self):
        ign = load(CONTRACTS / "ignition_output.schema.json")
        for f in ("mode", "stale_level", "age_minutes"):
            self.assertIn(f, ign["required"])
        spr = load(CONTRACTS / "spread_output.schema.json")
        for f in ("mode", "stale_level", "age_minutes", "validated_horizons_h", "uncertainty_status",
                  "grid_resolution_m", "perturbation"):
            self.assertIn(f, spr["required"])
        self.assertIn("fuel_source", spr["properties"]["inputs"]["required"])

    def test_stale_flag_must_match_stale_level(self):
        v = Draft202012Validator(load(CONTRACTS / "ignition_output.schema.json"))
        rec = load(EXAMPLES / "ignition_output.example.json")
        rec.update(stale_level="stale", stale_flag=False)
        self.assertFalse(v.is_valid(rec))
        rec.update(stale_flag=True)
        self.assertTrue(v.is_valid(rec))

    def test_empty_band_is_allowed(self):
        v = Draft202012Validator(load(CONTRACTS / "spread_output.schema.json"))
        out = load(EXAMPLES / "spread_output.example.json")
        for f in out["features"]:
            if f["properties"]["horizon_h"] == 1 and f["properties"]["band"] == "p90":
                f["geometry"] = {"type": "MultiPolygon", "coordinates": []}
                f["properties"]["area_ha"] = 0
        v.validate(out)


if __name__ == "__main__":
    unittest.main()
