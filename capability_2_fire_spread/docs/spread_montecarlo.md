# Monte Carlo burn-probability bands (T1.7)

Code: `scripts/spread_montecarlo.py` (bands), `scripts/spread_direction_bias.py` (bias tables).
Tests: `tests/test_spread_montecarlo.py`. Output contract: `contracts/spread_output.schema.json`.

**All numbers on this page come from made-up grids (uniform C-2 spruce, flat, constant weather) and say nothing
about real fires. The perturbation sizes are provisional round numbers, not calibrated (calibration is T2.5), and
nothing is validated against observed growth (`validated_horizons_h` is `[]`).**

## Method

1. Draw, for each of `n_runs` runs, one random number per input from `numpy.random.default_rng(seed)`, all up front in
   a fixed order, so the same seed gives the same bytes (apart from `run_timestamp`).
2. Perturb the case's hourly weather series for that run: wind speed x exp(0.25 z); wind direction + 15 deg x z;
   FFMC + 2 x z clipped to [0, 101]; `ros_scale` = 0.8 x exp(0.25 z). The hourly shape of the series is kept.
3. Run `spread_api.run_point` (FBP engine, no final-perimeter repair) for 6 h.
4. Burn fraction per cell and horizon = share of runs that burned it by then. p10 / p50 / p90 = cells with fraction
   >= 0.1 / 0.5 / 0.9. All three come from one fraction grid, so p90 is inside p50 inside p10, and every band grows
   with horizon because each run's arrival times only move forward.
5. Bands are polygonised on the EPSG:3978 90 m grid, reprojected to EPSG:4326, and written as MultiPolygons.
   `area_ha` and `perimeter_km` are measured in EPSG:3978 (conformal, not equal-area, so there is a small scale error
   that varies across Ontario and is not quantified here).

The contract has no lognormal distribution, so the two multiplicative perturbations are recorded as
`ln_factor_wind_speed_kmh` and `ln_factor_ros_scale` (normal in log space), with `ros_scale` as a `fixed` 0.8.
The FFMC clip bounds use `low` / `high`, which the contract describes for `uniform`; consumers should not read them as a
uniform range. Both are suggestions for the contract, not changes made here.

### Empty bands

v0.2.0 says every (horizon, band) feature is present and an empty band is `MultiPolygon` with `coordinates: []` and
`area_ha: 0`; both the empty `MultiPolygon` and an empty `Polygon` validate (tested). The module emits that form, with
`perimeter_km` 0 and `head_direction_deg` null. In practice a band is empty only when the start cell is water or
non-fuel, because a burnable start cell is burned in every run, so even p90 at 1 h contains at least that cell.

### Grid edge

Runs that touch the edge of the grid are cut off. `MonteCarloResult.edge_runs` counts them; the payload does not carry
it (no field for it). Use a grid large enough: on the reference case below, 2 of 100 runs touched the edge of a 301 x 301
grid (27 km square), so the 6 h p10 band there is slightly truncated and its area slightly low.

## Reference case (measured)

Synthetic 301 x 301 grid, 90 m cells, uniform C-2 spruce, flat, constant weather: wind 20 km/h from 270 deg (west),
FFMC 90, BUI 64, day of year 191, start 2019-07-10 18:00 UTC; ignition at the middle cell (about 51.7 N, 95 W);
`n_runs` 100, seed 385, default (provisional) sigmas. Command: `python scripts/spread_montecarlo.py`.

| horizon | band | area (ha) | perimeter (km) | head direction (deg) |
|---|---|---|---|---|
| 1 h | p10 | 73.71 | 4.86 | 93.56 |
| 1 h | p50 | 15.39 | 2.16 | 91.85 |
| 1 h | p90 | 3.24 | 0.90 | 90.00 |
| 3 h | p10 | 656.10 | 15.66 | 93.02 |
| 3 h | p50 | 136.89 | 6.30 | 92.82 |
| 3 h | p90 | 27.54 | 2.70 | 94.69 |
| 6 h | p10 | 2613.06 | 33.12 | 93.11 |
| 6 h | p50 | 554.85 | 13.14 | 92.87 |
| 6 h | p90 | 106.92 | 5.40 | 94.57 |

The deterministic (all sigmas 0) run on this setup gives 28.35 / 234.9 / 935.55 ha at 1 / 3 / 6 h with `ros_scale` 1.0, and
the bands are far wider than that: at 6 h p10 / p90 = 24. The defaults (wind +-28%, `ros_scale` +-28%, both lognormal) are wide
and the fire's size is steeply sensitive to them; per-run 6 h area varies by a factor of about 230 between the smallest and
largest run. Treat the band widths as a statement about these placeholder sigmas, not about real uncertainty.

## Runtime (wall clock, this box, synthetic grid, 6 h horizon, after one warm-up)

| grid | one `run_point` p50 / p95 | full ensemble, n_runs 100, p50 / p95 | repeats |
|---|---|---|---|
| 201 x 201 | 0.015 s / 0.016 s | 1.71 s / 1.88 s | 20 |
| 301 x 301 | 0.019 s / 0.021 s | 2.08 s / 2.41 s | 20 |
| 501 x 501 | 0.031 s / 0.051 s | 3.26 s / 3.59 s | 20 |
| 201 x 201, n_runs 200 | 0.014 s / 0.015 s | 3.27 s / 3.61 s | 10 |

Almost all the time is the simulator; polygonising nine bands takes about 0.012 s. The synthetic grids have flat ground and
one fuel type; real grids with varied fuel may be slower, and these timings have not been taken on one.

## Wind-azimuth bias of the grid

On uniform fuel and flat ground, burned area cannot depend on the wind azimuth, but the 16-move search on the 90 m grid makes fire
bigger when the wind is diagonal. Measured, deterministic (301 x 301, `ros_scale` 0.8, grid-aligned so wind azimuth is relative
to the grid; `python scripts/spread_direction_bias.py`):

| wind | horizon | cardinal mean (ha) | diagonal mean (ha) | diagonal / cardinal |
|---|---|---|---|---|
| 10 km/h | 1 h | 11.3 | 14.6 | 1.286 |
| 10 km/h | 3 h | 112.6 | 118.3 | 1.050 |
| 10 km/h | 6 h | 460.1 | 466.6 | 1.014 |
| 20 km/h | 1 h | 15.4 | 20.2 | 1.316 |
| 20 km/h | 3 h | 147.4 | 166.9 | 1.132 |
| 20 km/h | 6 h | 596.2 | 659.3 | 1.106 |

Winds from 0, 90, 180 and 270 deg gave identical areas, and 45, 135, 225 and 315 deg identical areas. At 1 h the fire is only
about 12 to 22 cells, so cell-count quantisation adds to the effect there.

How much of the band width this explains, 6 h, 20 km/h, n_runs 100, seed 385 (direction perturbation only vs all inputs):

| perturbed | wind from | p10 (ha) | p50 (ha) | p90 (ha) | p10 / p90 | per-run area CV | per-run max / min |
|---|---|---|---|---|---|---|---|
| direction only (15 deg) | 270 | 1189.9 | 523.3 | 170.1 | 7.00 | 2.8% | 1.114 |
| direction only (15 deg) | 225 | 1232.0 | 575.1 | 174.2 | 7.07 | 3.3% | 1.122 |
| all (provisional defaults) | 270 | 2613.1 | 554.9 | 106.9 | 24.44 | 107.6% | 229.9 |
| all (provisional defaults) | 225 | 2791.3 | 601.0 | 114.2 | 24.44 | 104.9% | 229.3 |

Reading: with only direction perturbed, the true area of a run cannot change, so the 2.8% (270 deg) and 3.3% (225 deg) run-to-run
area variation is all grid artefact; the band width itself (p10 / p90 about 7) is mostly the real geometric smearing of a stretched
fire swung through +-15 deg, not the artefact. The artefact moves the band centre by about 10% (p50 575 vs 523 ha) when the mean wind
is diagonal instead of west. Next to the +-28% wind and `ros_scale` sigmas it is small, but it is a bias, not noise: a fire
forecast for a diagonal wind is overstated by up to about 10% in area at 6 h (up to about 30% at 1 h) until the simulator's move set
is fixed. Wind-direction perturbation is kept as asked.

## Contract gaps noticed (for Team Lead)

- No `mode` value for synthetic or test output. The tests and demo use `retrospective` with a `weather_source` that says
  "synthetic ... NOT real weather".
- No lognormal in `perturbation.distribution`, and no field for the number of runs cut off by the grid edge.
