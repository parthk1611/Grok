"""Environment-variable configuration. No secrets are needed or read today.

See .env.example. Real secrets (if any ever exist) belong in the environment or a
secrets store, never in the repo or in a payload.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_SCENARIOS = ("fresh", "stale", "expired", "no_cache")


class ConfigError(ValueError):
    pass


def _bool(value: str, name: str) -> bool:
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean, got {value!r}")


@dataclass(frozen=True)
class Settings:
    data_mode: str = "mock"                 # RELAY_DATA_MODE: 'mock' now; 'live' = LiveProvider (not built yet)
    mock_scenario: str = "fresh"            # RELAY_MOCK_SCENARIO: fresh|stale|expired|no_cache (demo of cache_fallback.md levels)
    contracts_dir: Path = REPO_ROOT / "contracts"   # RELAY_CONTRACTS_DIR
    host: str = "127.0.0.1"                 # RELAY_HOST (loopback by default)
    port: int = 8000                        # RELAY_PORT
    log_level: str = "INFO"                 # RELAY_LOG_LEVEL
    validate_responses: bool = True         # RELAY_VALIDATE_RESPONSES: fail closed on schema-invalid payloads
    cors_origins: tuple[str, ...] = field(default_factory=tuple)  # RELAY_CORS_ORIGINS, comma separated; empty = none
    # Coarse sanity box for point queries (not an Ontario boundary test): lon/lat degrees.
    bbox_lon: tuple[float, float] = (-95.5, -74.0)
    bbox_lat: tuple[float, float] = (41.5, 57.0)
    spread_start_max_future_hours: float = 48.0
    spread_max_runs: int = 1000
    spread_timeout_seconds: float = 60.0    # RELAY_SPREAD_TIMEOUT_S: synchronous spread run limit (Team Lead decision)

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        e = os.environ if env is None else env
        mode = e.get("RELAY_DATA_MODE", "mock").strip().lower()
        if mode not in ("mock", "live"):
            raise ConfigError("RELAY_DATA_MODE must be 'mock' or 'live'")
        scenario = e.get("RELAY_MOCK_SCENARIO", "fresh").strip().lower()
        if scenario not in MOCK_SCENARIOS:
            raise ConfigError(f"RELAY_MOCK_SCENARIO must be one of {MOCK_SCENARIOS}")
        try:
            port = int(e.get("RELAY_PORT", "8000"))
        except ValueError as exc:
            raise ConfigError("RELAY_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ConfigError("RELAY_PORT out of range")
        level = e.get("RELAY_LOG_LEVEL", "INFO").strip().upper()
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ConfigError("RELAY_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")
        origins = tuple(o.strip() for o in e.get("RELAY_CORS_ORIGINS", "").split(",") if o.strip())
        if "*" in origins:
            raise ConfigError("RELAY_CORS_ORIGINS must list explicit origins, not '*'")
        try:
            spread_timeout = float(e.get("RELAY_SPREAD_TIMEOUT_S", "60"))
        except ValueError as exc:
            raise ConfigError("RELAY_SPREAD_TIMEOUT_S must be a number") from exc
        if not 0 < spread_timeout <= 600:
            raise ConfigError("RELAY_SPREAD_TIMEOUT_S must be in (0, 600]")
        return cls(
            spread_timeout_seconds=spread_timeout,
            data_mode=mode,
            mock_scenario=scenario,
            contracts_dir=Path(e.get("RELAY_CONTRACTS_DIR", str(REPO_ROOT / "contracts"))),
            host=e.get("RELAY_HOST", "127.0.0.1"),
            port=port,
            log_level=level,
            validate_responses=_bool(e.get("RELAY_VALIDATE_RESPONSES", "true"), "RELAY_VALIDATE_RESPONSES"),
            cors_origins=origins,
        )
