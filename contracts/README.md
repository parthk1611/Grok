# Handoff contracts (v0)

These files are the interfaces between the five lanes (ATLAS data, EMBER
ignition ML, BLAZE spread simulation, RELAY backend/pipeline, BEACON frontend/
docs). **A contract changes only with Team Lead approval.** Open a change
request in the team chat with the diff, the reason, and the affected lanes;
bump `contract_version` below when it is approved. Nobody edits these files in
a feature branch without that approval.

`contract_version`: **0.2.0** (draft until Team Lead sign-off)

| File | Purpose | Producer -> consumer |
| --- | --- | --- |
| `grid_spec.md` | CRS, cell size, cell-ID scheme, time zone and time conventions | ATLAS -> everyone |
| `ignition_output.schema.json` | One per-cell, per-day ignition-probability record | EMBER -> RELAY -> BEACON |
| `spread_output.schema.json` | Spread-run GeoJSON FeatureCollection (1 h / 3 h / 6 h, p10/p50/p90 bands) | BLAZE -> RELAY -> BEACON |
| `live_weather_record.schema.json` | One live weather record (observation or forecast) | ATLAS/RELAY -> BLAZE, EMBER, BEACON |
| `cache_fallback.md` | Staleness thresholds and the degrade-to-cache rule | RELAY (owner), all consumers |
| `examples/` | Illustrative payloads for mock-data development. **Not real model output.** | - |

## Rules every output must follow

1. Carry a timestamp, a `model_version`, and the exact `disclaimer` string
   (research estimate; does not replace official alerts or evacuation orders).
   Each schema enforces the disclaimer with `const`.
2. Timestamps are ISO 8601 UTC with a `Z` suffix. Calendar dates are local
   `America/Toronto` dates (`YYYY-MM-DD`). See `grid_spec.md`.
3. A stale or failed feed degrades to cached data and sets the stale flag; it
   is never silent. See `cache_fallback.md`.
4. No personal evacuee data, no secrets or API keys in any payload or in the repo.
5. Examples under `examples/` are hand-written placeholders for development.
   Their `model_version` is `example-not-a-real-model`; never present them as results.

## Validate

```sh
python -m pip install -r contracts/requirements-contracts.txt
python -m unittest tests.test_contracts -v      # repo convention is unittest
# or: python -m pytest tests/test_contracts.py
```

The test checks that each schema is a valid JSON Schema (draft 2020-12), that
every example validates, and that deliberately broken payloads are rejected.
If `jsonschema` is not installed the test is skipped with a visible reason
rather than breaking `python -m unittest discover -s tests`.

## Known open items (need a decision before v1.0.0)

- Ontario west of roughly 90 deg W observes Central time, while the ignition
  panel uses a single `America/Toronto` day. Contract keeps `America/Toronto`
  (consistent with the panel); a per-cell zone is not defined.
- The spread horizons of 1 h and 3 h cannot be scored against the replay
  dataset (satellite passes are >= 6 h apart); their outputs must be labelled
  unvalidated by BLAZE/BEACON.
- `p10/p50/p90` band semantics (burn-probability contours) are defined here;
  BLAZE must confirm the Monte Carlo can produce them.

## Changelog

- **0.2.0** (2026-11-02, Team Lead approved after T0.4 reviews from EMBER, RELAY, BEACON, BLAZE):
  - Both outputs: required `mode` (`retrospective | hindcast | forecast_like | forecast`), `stale_level`, `age_minutes`; `stale_flag` must agree with `stale_level`.
  - Ignition: optional `calibration`, `feature_set_version`.
  - Spread: required `validated_horizons_h`, `uncertainty_status`, `grid_resolution_m`, `perturbation`, `inputs.fuel_source`; optional hourly `inputs.weather_series`; scalar wind is the value at `start_time`; every (horizon, band) feature is always present and an empty band is an empty MultiPolygon with `area_ha: 0`.
  - cache_fallback.md: two-age staleness (fetch health and data age); per-source data-age thresholds TBD.
- **0.1.0**: initial handoff contracts.
