# COMP-385 dashboard shell (task T1.9)

> ## ALL DATA SHOWN IS MOCK
> This build only talks to a **mock adapter**. Every ignition probability, weather value, spread polygon,
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
```

`?scenario=fresh|stale|expired|invalid|sparse|rich` in the URL preselects a fixture.

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
  estimated* and draws one neutral single-run footprint (the p50 feature). An empty band (empty
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
  validated) the user must confirm before the run, and the result is marked DEGRADED.
- **Invalid payloads** never render silently: every payload is validated with Ajv (draft 2020-12 +
  ajv-formats) against the schemas imported read-only from `../contracts`, and a failure shows an explicit
  error state listing the schema errors.
- **Dev toggle** (header, labelled DEV): fixtures `fresh`, `stale`, `expired`, `invalid`
  (the `contracts/examples/bad_*` files), `sparse` (nulls / missing optional fields / not_estimated /
  empty band) and `rich` (all optional fields). See `src/mocks/README.md`.

## Basemap

Default: OpenStreetMap raster tiles with attribution (fine for a demo; see the
[OSM tile policy](https://operations.osmfoundation.org/policies/tiles/) before any real deployment).
Configure via env vars at dev/build time:

```sh
VITE_BASEMAP_URL='https://tiles.example/{z}/{x}/{y}.png'   # or "none" to disable
VITE_BASEMAP_ATTRIBUTION='&copy; your tile provider'
```

There is also a Basemap checkbox in the header. Every data layer is drawn over a plain background, so
the map still works offline / without tiles (a notice appears if tiles fail to load).

## Layout

```
src/api/        typed client + mock adapter (adapters return unknown; client validates)
src/mocks/      fixture builder + synthetic fixtures (README: they are synthetic)
src/lib/        Ajv validators, staleness, time (Toronto/DST), horizons, geojson, colours, spread gate
src/geo/        cell_id -> EPSG:4326 polygon (proj4, EPSG:3978)
src/components/ map, panels, banners, footer, dev toggle, confirm dialog
```

## Notes / limitations

- No real HTTP adapter yet: endpoints are defined by RELAY's `contracts/openapi.yaml` (pending approval).
- Contract schemas use `if/then/else` branches without `type: object` (and `required` for properties declared
  outside the branch), so Ajv `strictTypes` and `strictRequired` are switched off in `src/lib/contract.ts`
  (schemas are not modified).
- The maplibre-gl v6 worker is bundled explicitly (`?worker&url`) because the default relative worker
  path breaks under Vite's asset bundling.
