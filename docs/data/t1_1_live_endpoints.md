# T1.1 — MSC live wind/weather endpoints (GeoMet + Datamart)

Prepared by ATLAS (data lane), 2026-11-02 (fixtures) / 2026-11-09 (Datamart lag re-measure).
Branch `data/atlas-alignment-v1`. Contracts base: `629600d` (v0.2.1). **No `contracts/` files were edited.**

Every URL, status, byte count, value, and lag number below was either captured into
`tests/fixtures/live/` (unedited responses + `manifest.json`) or measured by
`scripts/data_sources/measure_live_cadence.py` / a live reachability check. Nothing is fabricated.
If an endpoint is unreachable, that is recorded as such.

## Decision summary

| Need | Source chosen for live ingest (T1.2) | Why |
| --- | --- | --- |
| Forecast wind / temp / RH / precip at a point | **MSC GeoMet WMS GetFeatureInfo** on `HRDPS.CONTINENTAL_*` | JSON point sample, carries `time` + `dim_reference_time`, no GRIB decoder required for a single cell |
| Full-grid / archive of the same model | **MSC Datamart HRDPS GRIB2** (`dd.weather.gc.ca`) | Prototype already uses it; Apache indexes give publication mtimes for lag |
| Station observation (optional, not FWI) | ECCC `swob-realtime` (OGC API) and/or CWFIS `firewx_stns_current` WFS | Measured reachable; CWFIS carries FWI-side station weather but is noon-rep_date, not a minute feed |

HRDPS runs every **6 hours** (`00/06/12/18` Z). GeoMet keeps about a **~2-day** forecast `time` window and about **~1.5 days** of `reference_time` runs (measured extents below).

## Contract mapping rules (v0.2.1, already in contracts; not re-edited here)

- Timestamps stored **UTC** (`…Z`). Local calendar dates use **`America/Toronto`** (DST-aware).
- `kind=forecast` requires **`source_run_time`** (= GeoMet `dim_reference_time` / Datamart run time in the filename).
- **`valid_time`** is the time the values describe; for precipitation it is the **end** of the `precip_period_h` interval.
- Wind: GeoMet HRDPS `WSPD` is **m/s** (layer title). Convert at ingest with **× 3.6** → `wind_speed_kmh` (`alignment.ms_to_kmh`). Direction stays "blowing from", degrees.
- Two-age staleness: `age_minutes` = data age from `source_run_time` (forecast) / `valid_time` (observation); `fetch_age_minutes` from `retrieved_at`.

## 1. GeoMet WMS — HRDPS continental (point forecast)

Base: `https://geo.weather.gc.ca/geomet`

### GetCapabilities (time dimensions)

- Fixture: `tests/fixtures/live/geomet_capabilities_HRDPS.CONTINENTAL_UU.xml`
- URL (captured 2026-11-02T23:47:27Z, HTTP 200):  
  `…/geomet?service=WMS&version=1.3.0&request=GetCapabilities&layer=HRDPS.CONTINENTAL_UU`
- Measured dimensions in that fixture:
  - `reference_time`: `2026-11-01T06:00:00Z/2026-11-02T18:00:00Z/PT6H` (6-hourly runs)
  - `time`: `2026-11-02T18:00:00Z/2026-11-04T18:00:00Z/PT1H` (**48 h** hourly window for the default run)
- Live re-check 2026-11-09 (~16:46 America/Toronto): still HTTP 200;  
  `reference_time` `2026-11-08T06:00:00Z/2026-11-09T18:00:00Z/PT6H`,  
  `time` `2026-11-09T18:00:00Z/2026-11-11T18:00:00Z/PT1H` (again ~2-day forecast window).

### GetFeatureInfo layers queried (Thunder Bay area 48.38N, 89.25W)

Common query shape (fixture URLs in `manifest.json`):

`service=WMS&version=1.3.0&request=GetFeatureInfo&layers=LAYER&query_layers=LAYER&crs=EPSG:4326&bbox=48.37,-89.26,48.39,-89.24&width=3&height=3&i=1&j=1&info_format=application/json`

| Fixture | Layer | Units in title | Sample `value` (2026-11-02) | Maps to `live_weather_record` |
| --- | --- | --- | --- | --- |
| `geomet_gfi_TT.json` | `HRDPS.CONTINENTAL_TT` | °C | 10.023005 | `temp_c` |
| `geomet_gfi_HR.json` | `HRDPS.CONTINENTAL_HR` | % | 99.57312 | `rh_pct` |
| `geomet_gfi_WSPD.json` | `HRDPS.CONTINENTAL_WSPD` | **m/s** | 5.1900001 → **18.684 km/h** after ×3.6 | `wind_speed_kmh` |
| `geomet_gfi_WD.json` | `HRDPS.CONTINENTAL_WD` | ° | 81.800003 | `wind_direction_deg` |
| `geomet_gfi_PR.json` | `HRDPS.CONTINENTAL_PR` | mm (run accumulation) | 9.9367199 | prefer DIAG for hourly; if used, set `precip_period_h` to hours since `source_run_time` |
| `geomet_gfi_DIAG_PR_PT1H.json` | `HRDPS.CONTINENTAL.DIAG_PR_PT1H` | mm (1-hour) | 3.2193375 | `precip_mm` with `precip_period_h=1`; `valid_time` = property `time` (interval end) |
| `geomet_gfi_WSPD_explicit_time.json` | same WSPD | m/s | 4.52 at `time=2026-11-03T00:00:00Z`, `dim_reference_time=2026-11-02T18:00:00Z` | shows explicit TIME + DIM_REFERENCE_TIME work |
| `geomet_gfi_WSPD_time_out_of_range.json` | WSPD, TIME beyond extent | — | XML ServiceException (HTTP 200) | failure fixture |
| `geomet_gfi_bad_layer.xml` | unknown layer | — | XML ServiceException (HTTP 200) | failure fixture |

Every successful GFI feature carries:

- `properties.time` → `valid_time`
- `properties.dim_reference_time` → `source_run_time` (required for forecast)
- `geometry.coordinates` → lon/lat
- `kind` = `forecast`; `forecast_lead_h` = hours from `source_run_time` to `valid_time`

Live re-check 2026-11-09: default WSPD GFI still HTTP 200 (671 bytes). Endpoints reachable.

### Rate / practical limits (measured, not a published SLA)

- Single GFI/Capabilities calls completed in **~0.3–1.8 s** on 2026-11-02 (see `manifest.json` `latency_s`).
- No API key. No documented per-IP quota found in the responses; treat RELAY's 15‑minute poll target as the project limit, and prefer one Capabilities poll for run detection rather than hammering GFI.
- GeoMet retains only the recent run window above; it is **not** a multi-week archive (use Datamart dated trees or other archives for history).

## 2. MSC Datamart — HRDPS GRIB2 (grid + publication lag)

Base (today): `https://dd.weather.gc.ca/today/model_hrdps/continental/2.5km/{HH}/{lead}/`

| Fixture | URL | Captured | Notes |
| --- | --- | --- | --- |
| `datamart_hrdps_run_listing.html` | `…/2.5km/` | 2026-11-02T23:48:22Z, 200 | Run folders `00/06/12/18`; dir mtimes UTC |
| `datamart_hrdps_18Z_lead001_listing.html` | `…/18/001/` | same, 200 | 401 `.grib2` rows; WIND lead 001 mtime **2026-11-02 20:58Z** |

Filename pattern: `{YYYYMMDD}T{HH}Z_MSC_HRDPS_{VAR}_…_PT{lead}H.grib2`.

Variables needed for the live weather record (lead hours as required):

| Variable token | Field | Native units |
| --- | --- | --- |
| `TMP_AGL-2m` | `temp_c` | °C |
| `RH_AGL-2m` | `rh_pct` | % |
| `WIND_AGL-10m` | `wind_speed_kmh` after ×3.6 | m/s in GRIB |
| `WDIR_AGL-10m` | `wind_direction_deg` | ° |
| `APCP-Accum1h_Sfc` (or DIAG equivalent) | `precip_mm`, `precip_period_h=1` | mm; `valid_time` = end of that hour |

`source_run_time` = run time in the filename; `valid_time` = run time + lead.

### Publication lag (Datamart only) — measured

Script: `scripts/data_sources/measure_live_cadence.py`  
Method: Apache `Last-modified` on lead **001** files minus run time in the filename (minute resolution). Does **not** start a second GeoMet/CWFIS/SWOB poller; if `/workspace/relay-evidence/feed_cadence.jsonl` exists it is read for context only.

**2026-11-02 fixture (18Z only):** WIND `…PT001H.grib2` mtime 20:58Z → lag **178 min**.

**2026-11-09 live measure** (all four runs present under `today/`), WIND lead 001:

| Run | WIND file mtime (UTC) | Publication lag |
| --- | --- | --- |
| 00Z | 02:58 | **178 min** |
| 06Z | 08:57 | **177 min** |
| 12Z | 15:00 | **180 min** |
| 18Z | 21:03 | **183 min** |

Summary: min **177**, max **183**, mean **179.5** minutes (~**3.0 h**).  
Earliest any GRIB2 in those lead-001 dirs: **173–180 min** after run time.

Implication for `cache_fallback.md` (already noted there): a healthy HRDPS data-age "fresh" limit must exceed **run cycle (6 h) + publication lag (~3 h)** — i.e. more than ~9 h if measured from `source_run_time` at the end of a cycle — so a just-published run is not stuck in `stale`. Exact per-source thresholds remain TBD for Team Lead.

RELAY's `feed_cadence.jsonl` (38 lines, 2026-11-02 → 2026-11-08) records GeoMet `latest_run_age_min` only. Its minimum ages when a new `reference_time` first appears in the sparse sample are often **~255–300+ min**; that is an **upper bound** on GeoMet appearance lag under 15‑minute polling gaps, not a Datamart publish time. ATLAS does not re-poll those feeds.

## 3. Ancillary live observation endpoints (reachable; not the HRDPS forecast path)

| Fixture | Endpoint | Role | Notes from capture |
| --- | --- | --- | --- |
| `cwfis_wfs_firewx_stns_current_ON.json` | CWFIS GeoServer WFS `public:firewx_stns_current` (`prov='ON'`) | Station weather / FWI-side | Geometry EPSG:3978; `rep_date` noon Z; `ws` already km/h-like station wind in the sample (`ws: 48.1` with name ARGENTIA in the count=1 fixture) |
| `cwfis_wfs_firewx_stns_current_count1.json` | same, `count=1` | schema sample | HTTP 200 |
| `swob_realtime_item_ON_bbox.json` | `api.weather.gc.ca/collections/swob-realtime/items` | Minute surface obs | Geraldton Airport sample; `date_tm-value` UTC; air temp / RH / wind fields present with unit suffixes |

Licence for ECCC API collections remains unresolved in `docs/data/fwi_gap_and_wind_options.md` (Team Lead: CWFIS archive is primary for historical FWI; ECCC gap-fill only after licence confirmation).

## 4. Fixtures inventory

All under `tests/fixtures/live/`. Checksums, URLs, timestamps, HTTP status, and latencies: **`tests/fixtures/live/manifest.json`**. Responses are raw bytes as received (JSON/XML/HTML), not cleaned.

## 5. Blockers / open items for T1.2

1. Per-source data-age fresh/stale **thresholds** still TBD in `cache_fallback.md` (needs Team Lead after this lag evidence).
2. GeoMet `HRDPS.CONTINENTAL_PR` is **run-total accumulation**, not hourly — prefer `DIAG_PR_PT1H` (or Datamart `APCP-Accum1h`) for `precip_period_h=1`.
3. ECCC `swob-realtime` / climate API **licence** not confirmed → not adopted as primary.
4. SCANFI→FBP fuel crosswalk is a separate ledger TODO (`TODO-crosswalk-v1`); not required for live weather.
5. Rate-limit SLA for GeoMet/Datamart is **not published** in the responses we fetched; operate politely (RELAY 15 min cadence).

## 6. How to re-measure Datamart lag

```sh
python scripts/data_sources/measure_live_cadence.py
# optional: --json-out /tmp/datamart_lag.json
# reads /workspace/relay-evidence/feed_cadence.jsonl when present
```
