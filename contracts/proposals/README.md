# Contract proposals (NOT applied)

Files here are proposed changes to `contracts/`. They are **not** part of the contract until
Team Lead approves and commits them; nothing in `contracts/*.schema.json` is edited on a feature branch.

## v0.3.0-cr4-cr6.patch (RELAY, 2026-11-09)

For Team Lead to commit as contracts **v0.3.0**. Apply from the repo root:

```sh
git apply --check contracts/proposals/v0.3.0-cr4-cr6.patch && git apply contracts/proposals/v0.3.0-cr4-cr6.patch
```

| CR | Change | Files |
| --- | --- | --- |
| CR-4 | Optional `mock` boolean on all three payloads, so mock-ness is machine-checkable in the body (today only the `X-Mock-Data` / `X-Data-Mode` headers say it, because the schemas are `additionalProperties: false`). | `ignition_output`, `spread_output`, `live_weather_record` schemas |
| CR-6 | `stale_level` is the **worst** across all feeds an output used; optional `stale_sources: [{source, stale_level, age_minutes, fetch_age_minutes?}]` with `source` in `hrdps \| cwfis_firewx_stns \| swob_realtime` (same enum as `/api/v1/ingestion/status`). | `ignition_output`, `spread_output` schemas, `cache_fallback.md` |
| (version) | Titles, `contracts/README.md` and `cache_fallback.md` say v0.3.0. | |

Both fields are optional, so every v0.2.1 payload and example stays valid (checked: `tests/` 39/39 and
`backend/tests` 117/117 pass with the patch applied; the three examples validate with `mock: true` and a
`stale_sources` entry added, and `mock: "yes"` is rejected).

Not included: CR-2 (standalone error/ingestion-status schemas); see
`x-change-requests` in `contracts/openapi.yaml`.
