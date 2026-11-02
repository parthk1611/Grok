# Synthetic mock fixtures (NOT real data)

Everything in this directory is **hand-made placeholder data** for building and
reviewing the dashboard shell (task T1.9). None of it is a model result, a real
weather observation, or a forecast.

- Every ignition and spread fixture has `model_version: "example-not-a-real-model"`.
- Probabilities in `ignition_extra_cells.synthetic.json` are arbitrary toy values
  on a symmetric pattern (peak at the block centre) so the colour ramp and
  polygons are visible. They carry no meaning.
- Weather `age_minutes` / `stale_level` values are made up to exercise each
  staleness state.
- Cell IDs are valid cells of the `ofr10km-v1` grid (they exist in
  `data/interim/ontario_fire_region_10km_grid.csv`), but the values attached to
  them are fictional.

| File | Purpose |
| --- | --- |
| `ignition_extra_cells.synthetic.json` | 30 more cells (5x5 block around the example cell + 6 scattered) so the risk layer has something to draw |
| `ignition_sparse.synthetic.json` | Minimal record: only required fields, `mode: forecast`, `stale_reason: null` |
| `weather_stale.synthetic.json` | `stale_level: stale`, from cache, data age 120 min, fetch age 45 min |
| `weather_expired.synthetic.json` | `stale_level: expired`, from cache, data age 480 min, fetch age 400 min |
| `weather_forecast.synthetic.json` | `kind: forecast` with `source_run_time`, lead 6 h, data age 330 min declared fresh, fetch age 5 min |
| `weather_sparse.synthetic.json` | Every measured value `null` |
| `spread_sparse.synthetic.json` | Contract v0.2.0 edge cases: `n_runs: 1`, `uncertainty_status: not_estimated`, empty `validated_horizons_h`, empty `perturbation`, null `head_direction_deg` and inputs, an empty p10 band at 1 h (empty MultiPolygon, `area_ha: 0`), only 1 h and 6 h horizons |

Scenarios (dev toggle): `fresh`, `stale`, `expired`, `invalid` (the `bad_*` examples), `sparse`
(the files above) and `rich` (optional fields filled in code: ignition `calibration`,
`feature_set_version`, `rank_in_day`, spread `weather_series`, `mode: forecast_like`; the
values are labelled `synthetic-*`).

The base fixtures are the unmodified `contracts/examples/*.example.json`
files, imported directly. At runtime `fixtures.ts` re-bases the **timestamps and `age_minutes` / `stale_level` / `stale_flag`**
(relative to the browser clock) so that "fresh", "stale" and "expired" are true
relative to now; the spread polygons are rigidly translated to the clicked
point (this is not a simulation). All outputs are re-validated against the
contract schemas by the client before they are shown.

All files here are checked against the schemas in `fixtures.test.ts`.
