# RELAY backend (skeleton v1, mock mode)

FastAPI service for the COMP-385 wildfire decision-support system. **Everything it serves today is
mock data from `contracts/examples/`** (`model_version: example-not-a-real-model`), marked by the
`X-Mock-Data: true` / `X-Data-Mode: mock` headers. Research estimate for decision support only. It does
not replace official fire alerts, warnings or evacuation orders. The API contract is the draft
`contracts/openapi.yaml` (0.2.1-draft).

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt            # runtime-only: backend/requirements.txt
python -m unittest discover -s backend/tests -t . -v   # backend tests (responses validated against openapi.yaml)
python -m unittest discover -s tests -v                      # existing + contract tests (needs requirements-modeling.txt)
# run from the REPO ROOT; the app is built by a factory (there is no module-level `app`):
uvicorn backend.app.main:get_app --factory --host 127.0.0.1 --port 8000 --no-access-log
./scripts/secret_scan.sh                                # offline secret scan (add --history for git history)
docker compose up --build                               # same API in a container, published on 127.0.0.1 only
```

Config is by environment variable; see `.env.example` (no secrets needed).
`RELAY_MOCK_SCENARIO=stale|expired|no_cache` demos the staleness banners and the 503 path.
`RELAY_MOCK_NOW=2026-07-14T20:10:00Z` pins the mock clock: ignition data ages are measured from each record's
`data_timestamp` against the clock, and the examples are dated July 2026, so on the real clock they read `expired`.
`RELAY_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173` lets BEACON's Vite dev server call the API
(off by default; `*` is refused). `RELAY_SPREAD_DEFAULT_RUNS` (default 200) is `n_runs` when a request omits it.
Responses of 1 KiB or more are gzip-compressed for clients that send `Accept-Encoding: gzip`.

| Module | Role |
| --- | --- |
| `app/main.py` | routes, request IDs, error model, fail-closed schema check, spread timeout (60 s) |
| `app/providers/` | `DataProvider` interface, `MockProvider` (a `LiveProvider` comes with T2.6/T2.7) |
| `app/cache_policy.py` | two-age staleness levels, thresholds in one object (proposals; per-source data-age = TBD placeholders) |
| `app/timestamps.py` | source-time sanity check (rejects > 2 h in the future, before 2000) |
| `app/atomic_io.py` | atomic cache writes |
| `scripts/feed_cadence_probe.py` | 24 h feed update-cadence evidence for the thresholds |

Ideas ported (re-implemented, with attribution comments) from the unmerged branch
`parth/iteration-1-prototype`: fail-closed responses, strict query validation, atomic writes, age-based freshness.
