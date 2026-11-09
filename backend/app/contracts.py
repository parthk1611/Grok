"""Load the contract JSON schemas and validate payloads against them (fail closed)."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_FILES = {
    "ignition": "ignition_output.schema.json",
    "spread": "spread_output.schema.json",
    "weather": "live_weather_record.schema.json",
}
EXAMPLE_FILES = {
    "ignition": "ignition_output.example.json",
    "spread": "spread_output.example.json",
    "weather": "live_weather_record.example.json",
}


class ContractViolation(ValueError):
    """A payload failed its contract schema. Message holds the first error path only."""


class Contracts:
    def __init__(self, contracts_dir: Path):
        self.dir = Path(contracts_dir)
        self.schemas = {k: json.loads((self.dir / f).read_text(encoding="utf-8")) for k, f in SCHEMA_FILES.items()}
        for k, s in self.schemas.items():
            Draft202012Validator.check_schema(s)
        self._validators = {k: Draft202012Validator(s) for k, s in self.schemas.items()}
        self.disclaimer: str = self.schemas["ignition"]["properties"]["disclaimer"]["const"]
        self.contract_version = "0.2.1"

    def example(self, kind: str) -> dict:
        return json.loads((self.dir / "examples" / EXAMPLE_FILES[kind]).read_text(encoding="utf-8"))

    def validate(self, kind: str, payload) -> None:
        err = next(iter(self._validators[kind].iter_errors(payload)), None)
        if err is not None:
            path = "/".join(str(p) for p in err.absolute_path) or "<root>"
            raise ContractViolation(f"{kind} payload violates contract at {path}")
