# ATLAS alignment v1: branch notes

Branch `data/atlas-alignment-v1`, contracts content at v0.2.1 (commit `629600d` on the branch history; earlier alignment commit was based on v0.2.0 `8af8713`). Local only; not pushed
(GitHub auth for this repo is blocked until the owner sets it up), no PR opened.

## Scope

Only alignment helpers and their tests, plus two documents and one ledger file for Team Lead:

| File | Purpose |
| --- | --- |
| `scripts/data_sources/alignment.py` | UTC to `America/Toronto` local date, label window, t-1 cutoff assertions, ECCC LST/UTC handling, EPSG:3978 10 km `grid_id` join, two-age staleness helper |
| `tests/test_source_alignment.py` | DST spring-forward/fall-back, cutoff, grid join, no future rows, ECCC LST, two-age staleness |
| `tests/test_split_lock.py` | Locks the 2010-2019 splits in `docs/modeling/split_manifest.json` |
| `tests/test_no_secrets_no_large_files.py` | Secret-pattern scan and 50 MB file limit over tracked plus new files |
| `requirements-data.txt` | `pyproj==3.8.0`, `tzdata==2026.6` (alignment code is stdlib-only except `lonlat_to_grid_id`) |
| `docs/data/fwi_gap_and_wind_options.md` | CWFIS gap and ERA5 wind options (no option chosen) |
| `data/metadata/source_availability_ledger_v1.json` | Fuel-layer year-selection rule, status "pending Team Lead sign-off" |

No `contracts/` file was created or edited. No 2020-2025 labels were touched. No raster was downloaded.
No seed constant was added: the alignment code is deterministic; model seed stays `SEED = 385` in `scripts/ignition_modeling/contract.py`.

TODO markers in code reference `contracts/v0`: the grid constants (`GRID_VERSION = ofr10km-v1`, EPSG:3978, 10,000 m, ID pattern)
mirror `contracts/grid_spec.md`, and the fetch-health thresholds mirror `contracts/cache_fallback.md` v0.2.0. Data-age thresholds
are per-source and still TBD in the contract, so `staleness()` has no default for them.

## Time rules implemented (Team Lead, 2026-11-02)

- `America/Toronto` civil time with DST for every cell; label windows start at local midnight and are half-open (23, 24 or 25 h).
- Raw timestamps are UTC; the local date is derived. Naive timestamps are rejected, never assumed to be UTC.
- No fixed offsets. ECCC "LST" is local *standard* time; its offset is read from the tz database (`utcoffset - dst`), not typed in.
  Verified against a real record: Armstrong AUT, 2019-07-01, `LOCAL_DATE` 00:00 with `UTC_DATE` 05:00Z.
- Cutoff: `assert_t_minus_1_cutoff` (dates) and `assert_available_before_issue` (instants, interval end must be <= issue time).
- Finding: an ECCC daily summary for LST day t-1 ends at 01:00 civil time on day t in summer, after the 00:00 issue time. It is not
  complete at issue in summer (it is in winter). Daily totals must be rebuilt from hourly values; the test `test_summer_daily_record...` pins this.

## Fixed UTC-5 bug on `parth/iteration-1-prototype` (flag, not fixed here)

Read from the branch source (read-only, code not executed by ATLAS):

- `prototype_iteration_1/scripts/prepare_selected_weather.py`: `daily_weather(hourly, standard_offset_hours, ...)` builds days with
  `offset = pd.Timedelta(hours=standard_offset_hours)` (line 56), groups hours into days with `frame.index.min() + offset` (line 57) and
  places each "noon" at `date + 12 h - offset` (line 62). The CLI default is `--standard-offset-hours` with choices `(-5, -6)` and
  default `-5` (line 53). That is a constant offset with no daylight saving.
- `prototype_iteration_1/reports/week_2_development_2026-10-15/selected_weather_contract.json` line 17 states the choice:
  "Fixed UTC-5 standard noon for this Thunder Bay input; western Ontario would require a declared UTC-6 or verified local-standard-time mapping".
- Why it conflicts with the rule: the project label day starts at local civil midnight (04:00Z in summer, 05:00Z in winter). A fixed -5
  day starts at 05:00Z all year, so for one hour of every summer day (04:00-05:00Z) the prototype assigns the hour to the previous
  date. Its noon is local standard noon (17:00Z), i.e. 13:00 civil time in summer. It is still before the 04:00Z issue time of the next day,
  so the t-1 rule is not broken by this alone, but date keys and rain windows built this way will not join cleanly to the panel's local dates.
- Related, not the same bug: `prepare_provincial_weather.standard_offsets` assigns per-cell standard offsets (-5 or -6, from the cell's
  time zone) and also never applies DST. That is a different rule from "America/Toronto with DST for every cell" and should be reconciled
  if that code is reused.
- Suggested fix when the prototype lineage is merged: derive the local date with `alignment.utc_to_local_date` and the window with
  `alignment.label_window_utc`, and drop `--standard-offset-hours`.

## T0.4 review of contracts v0.2.0 (ATLAS produces or consumes these)

One-line verdict: `grid_spec.md` OK; `live_weather_record.schema.json` and `cache_fallback.md` need the specific changes below before
sign-off (the contract files were not edited from this branch).

- `grid_spec.md`: OK. Checked against `data/interim/ontario_fire_region_10km_grid.csv`: 9,673 unique IDs, every ID equals
  `ofr10km_{x}_{y}` with x, y multiples of 10,000, extents -20,000..1,480,000 and -450,000..870,000 match, centroid lon/lat ranges match,
  and every centroid projects (pyproj 3.8.0, EPSG:4326 to EPSG:3978) into its own cell (tested for all 9,673). Spread fires CSV does carry
  `utc_offset_hours`. Optional wording: the time table could note that ECCC LST is one hour behind civil time in summer.
- `live_weather_record.schema.json` changes:
  1. Require `source_run_time` when `kind` is `forecast` (JSON Schema `if/then`); the two-age rule cannot be computed for HRDPS without it.
  2. The `age_minutes` description still says "now - retrieved_at ... or now - valid_time"; under v0.2.0 it should say which age it reports
     (the age that set `stale_level`) so RELAY and BEACON agree. My helper returns both ages plus the one that set the level.
  3. State units for conversion: GeoMet HRDPS wind speed is m/s (layer title `[m/s]`, measured 2026-11-02) while the record is km/h.
     ECCC bulk wind is not in the record's units either; the ingest step must convert, and `wind_direction_deg` must keep "blowing from".
  4. State what `valid_time` means for accumulated fields (end of the `precip_period_h` interval).
- `cache_fallback.md` changes:
  1. The older sections ("Age and levels", "Forecast records use the same levels, measured against `retrieved_at`") still define
     `age_minutes` from `retrieved_at` only and now contradict the v0.2.0 two-age section; fold them together.
  2. HRDPS cadence, measured on 2026-11-02: GeoMet `reference_time` is every 6 h (PT6H), and at 23:31Z the newest run was 18:00Z, already
     5.5 h old when fetched. A fresh fetch of that run is `fresh` by `retrieved_at` but 5.5 h old by model run, so the HRDPS data-age
     "fresh" limit has to exceed about 6 h or HRDPS will always read stale. This is one sample; T1.1 should measure the publication lag over 24 h.
  3. Polling a 6-hourly model every 15 minutes is mostly redundant; poll cadence per source belongs next to the data-age thresholds.
  4. The file points to `contracts/openapi.yaml`, which is not on this branch (RELAY draft).
- `alignment.staleness()` implements the v0.2.0 two-age model (fetch from `retrieved_at`, data age from `valid_time`/`source_run_time`,
  worse level wins, forecast with past `valid_time` at least stale, `stale_flag` true exactly when level is not fresh). Fetch thresholds are the
  contract's proposed 30 min / 6 h; data-age thresholds must be passed in until Team Lead fixes them.

## Fuel layers by fire year (ledger, pending Team Lead sign-off)

Rule recorded in `data/metadata/source_availability_ledger_v1.json`: fire year <= 2014 uses `National_FBP_Fueltypes_version2014b.zip`
(250 m, built from the 2011 land cover; its metadata cites NFDB 2016 for burn scars); fire year >= 2015 uses the SCANFI v2 5-year epoch
strictly before the fire year, `5 * floor((year - 1) / 5)` (2015 -> 2010, 2016-2020 -> 2015, 2021-2025 -> 2020; superseded the earlier same-year rule, see the ledger). HEAD requests confirmed SCANFI v2 closure files for 2015, 2020 and
2025 exist and 2016 does not. Open points listed in the ledger: SCANFI is not an FBP fuel-type raster (crosswalk needed), and 250 m / 30 m / 90 m resolutions differ. Both entries are flagged hindcast, not operational.

## Test results

Run with `/workspace/venv-model` (requirements-modeling.txt pins plus `requirements-data.txt` and `jsonschema==4.26.0`):
`python -m unittest discover -s tests -v` ran 89 tests at `f5a0d4f`, all OK (98 at `9d065cf` after T1.1, 100 after the #2 review fixes) (10 contract tests, 3 ignition feature panel, 26 ignition modeling, and the
50 new tests). `capability_2_fire_spread/tests` was not runnable: it needs `shapely` (not installed; unpinned in its requirements) and data that is not in the clone.

## T1.1 (2026-11-09)

Delivered on this branch (local commit; not pushed):

- `docs/data/t1_1_live_endpoints.md` — GeoMet HRDPS GFI layers + Datamart GRIB2, ~2-day GeoMet window, 6-hourly runs, field mapping, wind ×3.6
- `tests/fixtures/live/` + `manifest.json` — real captured responses (2026-11-02)
- `scripts/data_sources/measure_live_cadence.py` — Datamart GRIB2 publication lag only; reads RELAY `feed_cadence.jsonl` if present
- Ledger `1.1.0`: project-wide fuel rule (FBP 2014b for year <=2014; else latest SCANFI 5-yr epoch **strictly before** fire year), SCANFI→FBP crosswalk TODO, `contracts_base_commit` `629600d`

Datamart WIND lead-001 publication lag measured 2026-11-09: 177–183 min (mean 179.5). Fixture 18Z on 2026-11-02: 178 min.

## Not done / follow-ups

- Not pushed, no PR. Needs GitHub write auth from the repository owner; then push the branch once and open a draft PR to `contracts/v0` or `main` per Team Lead.
- Measurement scripts for the options doc are in scratch space, not committed.
- T1.1 done (see above). T1.2–T1.5 not started.
