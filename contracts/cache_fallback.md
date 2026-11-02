# Cache and stale-data fallback rule (contract v0.1.0)

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

## Age and levels

`age_minutes` = serving time minus `retrieved_at` for the cached record
(and, for observations, also check serving time minus `valid_time`).

| `stale_level` | Condition (proposed) | Behaviour |
| --- | --- | --- |
| `fresh` | age <= 30 min (two missed polls) | Normal. `stale_flag = false` |
| `stale` | 30 min < age <= 6 h | Serve the cached record. `stale_flag = true`. UI shows a visible staleness warning with the age and the data time |
| `expired` | age > 6 h | Serve the last cached record only with `stale_flag = true` and an "expired" banner. Spread runs are labelled degraded; the UI makes the user confirm, or enter wind and moisture by hand, before running. Never show it as current |
| (no cache at all) | feed never succeeded | Return an explicit error (HTTP 503 with reason). Do not invent or default weather values |

Forecast records use the same levels, measured against `retrieved_at`, plus:
a forecast whose `valid_time` is already in the past is treated as an
observation substitute and flagged `stale` at least.

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
