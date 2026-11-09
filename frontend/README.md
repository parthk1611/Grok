# COMP-385 dashboard shell (task T1.9)

> ## ALL DATA SHOWN IS MOCK
> By default this build only talks to an in-browser **mock adapter** (an optional RELAY HTTP adapter exists; RELAY's server is itself mock-only today). Every ignition probability, weather value, spread polygon,
> timestamp age and staleness state comes from `contracts/examples/*` or from the synthetic fixtures in
> `src/mocks/` (`model_version: example-not-a-real-model`). Nothing here is a model result, a real
> observation, or a forecast. The UI says so with a persistent MOCK banner and a MOCK badge on every panel.
> This is a research-estimate UI; it does not replace official fire alerts or evacuation orders.

Vite + React + TypeScript + MapLibre GL JS. Built against **contract v0.2.1** (`/contracts`, read-only
for this lane).

## Run

Needs Node >= 20.19.

```sh
cd frontend
npm ci            # installs the exact versions pinned in package-lock.json
npm run dev       # http://localhost:5173
npm run build     # type-check + production build into dist/
npm run preview   # serve dist/ at http://localhost:4173
npm test          # vitest (unit + component tests)
npm run lint      # tsc --noEmit
npm run smoke:relay  # optional, needs a running RELAY server (see below)
```

`?scenario=fresh|stale|expired|invalid|sparse|rich|stale_day|no_cache` in the URL preselects a fixture;
`?date=YYYY-MM-DD|latest` preselects the ignition map date.

## What it shows

- **Ignition risk layer**: one polygon per `cell_id` (`ofr10km_{x0}_{y0}` = EPSG:3978 SW corner, 10 km),
  reprojected to EPSG:4326 with proj4. Raw probability on a neutral viridis ramp; the legend states
  *probability, no approved danger thresholds*. The scale is linear from 0 to the maximum in the loaded
  payload (relative only, not a danger level). Click a cell for details (probability, rank, calibration,
  feature_set_version, grid_version, timestamps, staleness).
- **Spread mode** (header toggle): click the map; the mock adapter returns the example spread output
  translated to the clicked point (not a simulation). p10/p50/p90 polygons for the 1 / 3 / 6 h horizon
  toggle. Horizons not in `validated_horizons_h` are labelled *unvalidated*. If
  `uncertainty_status` is `not_estimated`, no bands are drawn or tabulated: the UI says *uncertainty not
  estimated* and draws the p50 feature once as a neutral *single-run footprint* (p10/p90 ignored). An empty band (empty
  MultiPolygon, `area_ha: 0`) is shown as *no area reached this level*. Run details list model version,
  n_runs/seed, grid resolution, perturbation parameters, inputs (wind is the value at `start_time`),
  fuel source and the optional hourly `weather_series`.
- **Mode badge**: `mode` is shown prominently; anything but `forecast` reads
  *RETROSPECTIVE / HINDCAST / FORECAST-LIKE REPLAY - not a forecast*.
- **Timestamps** in UTC and America/Toronto (DST-aware via `Intl`), `model_version`, and the payload's
  exact disclaimer string (falling back to the contract constant) on every output; the disclaimer is also
  in a persistent footer.
- **Staleness** (two ages, contract v0.2.1): `stale_level` is the worse of *fetch health* (now - `retrieved_at`;
  weather `fetch_age_minutes`, optional) and *data age* (`age_minutes`: now - `valid_time` for observations, now -
  `source_run_time` for forecasts). The weather panel and the stale/expired banners show **both ages**
  (fetch age says "not provided" when absent), plus `source_run_time` and lead for forecasts. `stale_level` /
  `stale_flag` / `stale_reason` drive a stale (amber) or expired (red, distinct) banner. The declared
  `stale_level` is the baseline; because per-source data-age limits are still TBD in the contract, generic limits
  are **not** applied to the data age (a note is shown if it exceeds them). Fetch age, when present, is
  cross-checked against the proposed 30 min / 6 h fetch-health limits in `src/config.ts` (**proposed**) and the
  worse level is shown if it disagrees. When the weather for a spread run is expired (or could not be
  validated) the user must confirm or enter inputs by hand before the run (see rulings below); a confirmed run is marked DEGRADED.
- **Invalid payloads** never render silently: every payload is validated with Ajv (draft 2020-12 +
  ajv-formats) against the schemas imported read-only from `../contracts`, and a failure shows an explicit
  error state listing the schema errors.
- **Dev toggle** (header, labelled DEV): fixtures `fresh`, `stale`, `expired`, `invalid`
  (the `contracts/examples/bad_*` files), `sparse` (nulls / missing optional fields / not_estimated /
  empty band), `rich` (all optional fields), `stale_day` (yesterday's ignition map served for today) and
  `no_cache` (503 `no_cached_data` everywhere). See `src/mocks/README.md`.

## API adapters

| `VITE_API_ADAPTER` | Data source |
|---|---|
| `mock` (default) | In-browser MOCK adapter (`src/mocks/mockServer.ts`). Emulates RELAY's status codes and error bodies from the fixtures. |
| `relay` | `src/api/relayAdapter.ts`, HTTP to `VITE_API_BASE_URL` (e.g. `http://127.0.0.1:8765`). Without a usable base URL the app falls back to MOCK and says so. |

`VITE_API_ENVELOPE=true` asks for the opt-in ignition envelope (`?envelope=true`). The client accepts the plain
array or the envelope either way. With the HTTP adapter the MOCK banner stays up until responses explicitly say
`X-Mock-Data: false` (or envelope `mock: false`); unknown means MOCK. The DEV fixture toggle only shows on the mock
adapter (RELAY's scenario is server-side: `RELAY_MOCK_SCENARIO`).

```sh
VITE_API_ADAPTER=relay VITE_API_BASE_URL=http://127.0.0.1:8765 npm run dev
# RELAY must list the dashboard origin in RELAY_CORS_ORIGINS. Vite may be opened as either host, so list both:
#   RELAY_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173   (add :4173 for `npm run preview`)
```

## Team Lead rulings implemented (2026-11-09)

- **Spread on expired weather.** Before a run the client checks the weather record; if it is expired or unusable, or
  the API answers **409 `confirmation_required`**, a dialog offers *Run anyway (degraded)* (`confirm_degraded: true`)
  or *Enter wind/moisture by hand* (validated form with units: wind km/h and direction FROM in degrees required;
  FFMC, BUI, temp, RH and precip optional; empty optional fields are not sent). The ranges are **sanity limits, not
  contract limits** (approved by Team Lead 2026-11-09): wind 0-200 km/h, direction 0-360° (from), FFMC 0-101 (the
  real FWI scale), BUI 0-500, temperature -50 to 50 °C, RH 0-100 %, precip 0-500 mm. The form says so too. Cancel sends nothing. A 409 after the user already confirmed is shown as an error, never looped. Expired weather
  with no cache at all: `confirm_degraded` gets 503 `no_cached_data`; a manual override still runs (200, stale, manual).
- **Manual inputs.** A response with `inputs.weather_source: "manual"` gets a **Manual inputs** badge above the stale
  banner and is never shown as fresh. If the server's manual response departs from the ruling (`stale_level` not
  `stale`, `stale_flag` not true, a different `stale_reason`, wind not echoed), the UI lists that as a deviation.
- **Ignition stale day.** A date picker (Toronto day, or *latest map*) drives `GET /ignition?date=D|latest`. Records
  always show their own `date`; when it differs from D a banner reads *Showing &lt;date&gt;; requested &lt;D&gt;
  unavailable*, only when an explicit date was requested (`requested_date` echoes the request, so `latest` stays
  `latest`; the records' `date` is the served day). Earlier records without `stale_flag: true`, or records dated after D, are listed as deviations.
- **Error bodies** `{status, error, reason, last_cache_time, request_id, disclaimer}` (closed shape; any other
  field, e.g. `retryable`, is listed as unexpected): each `reason` in the closed
  enum (`src/lib/apiError.ts`) has a plain-language message, shown with the HTTP status, the server's text,
  `last_cache_time` (UTC + Toronto, or *never cached* when null) and `request_id`. Off-model bodies (unknown reason,
  extra or missing fields, wrong disclaimer, status mismatch) are listed field by field, never fixed up.
- **Envelope**: `{mode, stale_level, age_minutes, data_timestamp, requested_date, mock?, stale_sources?, records}` is
  checked structurally (no schema in `/contracts` yet), and each record is validated against the ignition schema on
  its own. Any other envelope key is rejected.
- **PROVISIONAL, until Team Lead approves contract v0.3.0:** the envelope's optional `mock` (CR-4, boolean) and
  `stale_sources` (CR-6, `[{source: hrdps|cwfis_firewx_stns|swob_realtime, stale_level, age_minutes,
  fetch_age_minutes?}]`) follow RELAY's proposal `contracts/proposals/v0.3.0-cr4-cr6.patch` (RELAY 71583ce).
  Envelope `mock: true` always means MOCK, even if `X-Mock-Data` says false (the disagreement is listed).
  `stale_sources` is listed per feed in the stale banner, and an entry worse than the envelope `stale_level` is
  flagged. **When v0.3.0 is approved, the client needs a matching change:** records carrying `mock` /
  `stale_sources` are rejected by the v0.2.1 schemas in `/contracts` until those schemas are updated (we import
  them read-only), and the hand-written envelope check should then follow whatever shape v0.3.0 settles on.
- **`not_estimated` spread** is decided only from `uncertainty_status`: p50 is drawn once, labelled *single-run
  footprint*, and p10/p90 are ignored whether they repeat the footprint or are empty. There is no fallback to p10/p90.
- **Bad examples**: `src/lib/contract.bad-examples.test.ts` loads every `contracts/examples/bad_*.json` from disk and
  asserts the app's validators (strictTypes/strictRequired relaxed) reject each one.

## Timeouts and errors

The HTTP adapter aborts a request after 70 s (the server's synchronous spread limit is 60 s). That shows as
*request timed out after 70 s*; a server-side 504 `spread_timeout` shows the timeout reason with its
`request_id`. Other network failures show *request failed*.

## MOCK labelling

One decision (`src/lib/mockDecision.ts`) drives the MOCK banner, panel badges and the footer: MOCK if the adapter is
the mock, before any response arrives, or unless every response explicitly says `X-Mock-Data: false` (and no
envelope says `mock: true`). Any disagreement means MOCK.

## RELAY smoke test

```sh
# terminal 1, in RELAY's worktree with its venv
RELAY_MOCK_SCENARIO=expired RELAY_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173 uvicorn backend.app.main:get_app --factory --port 8765
# terminal 2
RELAY_SMOKE_URL=http://127.0.0.1:8765 RELAY_SMOKE_SCENARIO=expired npm run smoke:relay
```

This runs every dashboard call through the real client and HTTP adapter and prints a JSON report with a `findings`
list (deviations from the contract and rulings). It only fails if the server is unreachable.

## Basemap

Default: OpenStreetMap raster tiles with attribution. The map's attribution control is always expanded (not the
compact "i" button), so "© OpenStreetMap contributors" is visible whenever the basemap is on.

**OSM tile usage policy.** `tile.openstreetmap.org` is a donated community service with a
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/): light, occasional use such as a
classroom demo is fine, with visible attribution. Heavy or public demos (a projected live demo with many viewers, a
publicly hosted build, load tests, offline pre-caching) should use another tile provider or self-host tiles, and
set `VITE_BASEMAP_URL` plus a proper `VITE_BASEMAP_ATTRIBUTION` for that provider (it must still credit OSM
contributors if the tiles are OSM-derived). Never bulk-download or scrape OSM tiles.
Configure via env vars at dev/build time:

```sh
VITE_BASEMAP_URL='https://tiles.example/{z}/{x}/{y}.png'   # or "none" to disable
VITE_BASEMAP_ATTRIBUTION='&copy; your tile provider'
```

There is also a Basemap checkbox in the header. Every data layer is drawn over a plain background, so
the map still works offline / without tiles (a notice appears if tiles fail to load).

## Layout

```
src/api/        typed client (validates everything), RELAY HTTP adapter, adapter selection from env
src/mocks/      fixture builder, synthetic fixtures, MOCK API emulation (statuses, 409, 404, 503)
smoke/          optional smoke test against a running RELAY server
src/lib/        Ajv validators, staleness, time (Toronto/DST), horizons, geojson, colours, spread gate
src/geo/        cell_id -> EPSG:4326 polygon (proj4, EPSG:3978)
src/components/ map, panels, banners, footer, dev toggle, confirm dialog
```

## Notes / limitations

- The HTTP adapter follows RELAY's draft `openapi.yaml` and the 2026-11-09 rulings. The error model and envelope are
  checked by hand-written code until they are in `/contracts` (v0.3.0).
- Contract schemas use `if/then/else` branches without `type: object` (and `required` for properties declared
  outside the branch), so Ajv `strictTypes` and `strictRequired` are switched off in `src/lib/contract.ts`
  (schemas are not modified).
- The maplibre-gl v6 worker is bundled explicitly (`?worker&url`) because the default relative worker
  path breaks under Vite's asset bundling.
