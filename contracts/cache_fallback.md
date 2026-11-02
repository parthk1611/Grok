# Cache and stale-data fallback rule (contract v0.2.1)

Owner: RELAY. Consumers: EMBER, BLAZE, BEACON. **Thresholds below are proposed
defaults, pending Team Lead approval and ATLAS confirmation of how often the
real MSC sources update.** Nothing here has been measured against a live feed yet.

## Ingestion

- RELAY polls each live weather source every 15 minutes (target).
- Each successful fetch is written to the cache with `retrieved_at` and the
  source's own `valid_time` / `source_run_time`.
- A fetch fails if it times out (proposed: 20 s), returns a non-2xx status,
  returns malformed data, or fails schema validation against
  `live_weather_record.schema.json`. A failed fetch never overwrites a good cache entry.

## Age and levels (two ages)

Staleness is judged on two separate ages. `stale_level` reports the **worse** of the two.

1. **Fetch health** = serving time minus `retrieved_at`. Tells us whether our polling works.
2. **Data age** = serving time minus `valid_time` (observations) or minus `source_run_time` (forecasts such as HRDPS). Tells us how old the information itself is. This is what `age_minutes` reports; fetch health goes in `fetch_age_minutes`.

| `stale_level` | Fetch health (proposed) | Data age (per source) | Behaviour |
| --- | --- | --- | --- |
| `fresh` | <= 30 min (two missed polls) | <= source fresh limit | Normal. `stale_flag = false` |
| `stale` | 30 min to 6 h | <= source stale limit | Serve the cached record. `stale_flag = true`. UI shows a visible staleness warning with the age and the data time |
| `expired` | > 6 h | > source stale limit | Serve the last cached record only with `stale_flag = true` and an "expired" banner. Spread runs are labelled degraded; the UI makes the user confirm, or enter wind and moisture by hand, before running. Never show it as current |
| (no cache at all) | feed never succeeded | n/a | Return an explicit error (HTTP 503 with reason). Do not invent or default weather values |

Per-source data-age limits are **TBD** until RELAY's and ATLAS's measured update cadence and publication lag are reviewed by Team Lead. Constraint already fixed: a model source's fresh limit must be at least its run cycle plus its measured publication lag (for HRDPS, more than 6 h; a sample showed the newest run already 5.5 h old at 23:31Z), so a healthy feed never reads as permanently stale.

A forecast hour whose `valid_time` is already in the past may be used as an observation substitute but is flagged at least `stale`.

## Mapping to output contracts

- `ignition_output.stale_flag`: true if the weather/feature inputs behind the
  record are `stale` or `expired`. `stale_reason` should name the feed and age.
  The historical-panel model uses the prior-day observation, so for the daily
  map the relevant check is whether the latest complete prior-day input exists;
  if not, serve the previous day's map with `stale_flag = true` and the original `date`.
- `spread_output.inputs.stale_flag`: same rule for the wind and moisture inputs.
- `data_timestamp` (ignition) and `inputs.weather_valid_time` (spread) always
  carry the real age of what was used, never the serving time.

## Logging and alerts (RELAY)

Log every fallback (source, age, reason) without secrets. Raise an operator
alert when any feed is `stale` for more than 3 consecutive polls and when any
feed becomes `expired`. Alert thresholds are subject to Team Lead approval.

## Out of scope for this contract

Which MSC endpoint/layers to use (ATLAS), cache storage technology, and
authentication. Keys and tokens live in environment variables or a secrets
store, never in the repo or in a payload.

## Output fields

`ignition_output` and `spread_output` carry `stale_level` (`fresh | stale | expired`) and `age_minutes` (filled by the API cache layer). `stale_flag` is kept for compatibility and must be true exactly when `stale_level` is not `fresh`. An expired spread input requires a UI confirmation step before a run. The 503 error body will be defined in the API spec once RELAY's draft is approved by Team Lead.
