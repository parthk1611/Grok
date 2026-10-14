# Fire-spread simulator v1

A wind-driven simulator that grows a fire across a 90 m grid, replayed against
real Ontario fires from the spread dataset. This note says what it does, how
it was tuned and scored, what the scores are, and where it falls short.

## What it does

`scripts/simulate_spread.py` spreads fire from burning cells to their
neighbours. The time to cross to a neighbour comes from:

- the Canadian FBP rate-of-spread equations for the two cells' fuel types
  (`scripts/fbp_spread.py`; constants checked against the `cffdrs` R package);
- that hour's 10 m wind, the day's Fine Fuel Moisture Code and Buildup Index;
- an ellipse that stretches the fire downwind (FBP length-to-breadth ratio);
- the slope between the two cells (faster uphill).

Each cell's arrival time is the earliest time fire can reach it by any path
(shortest travel time over sixteen neighbours), so fire goes around lakes and
runs ahead through faster fuels. Water and non-fuel never burn.

Inputs per fire are the dataset's 90 m fuel and elevation windows and its
hourly weather. The fuel map is the 2024 edition, which shows regrowth or
non-fuel where older fires burned; inside each fire's perimeter the land is
given the fuel of the nearest burnable cell outside it (lakes are kept).

## Departure from the proposal

The proposal calls the simulator "Rothermel-based". This version uses the
Canadian FBP System instead: Rothermel needs the US fuel models, and the
national fuel map for Ontario is in FBP fuel types. The rest of the design
(physics-based, wind u/v, fuel moisture, fuel type, slope, replay against
time-stamped fire growth) follows the proposal.

## Tuning and scoring

`scripts/run_spread_replay.py` replays fires between consecutive satellite
observations 6 to 30 hours apart with at least 5 newly burning 375 m cells.
Each step starts from the area burned so far, spreads only from the stretch of
perimeter where flames were seen at the latest pass, and is compared with the
cells first seen burning at the next pass.

Two settings are tuned, on 24 fires from 2012–2020 only:

| Setting | Meaning | Chosen |
| --- | --- | --- |
| `ros_scale` | multiplies every rate of spread | 0.8 |
| `flank_scale` | further multiplies the flank and back rates | 0.25 |

They are chosen so that the simulated head runs as far as the observed one
(distance ratio 1) and the simulated area is 1.3 times the cells seen
(satellites record a median 77% of a fire's final area). No single pair meets
both: the chosen pair gives 0.65 and 1.25 on the calibration fires.

Scores are then taken on 24 fires from 2021–2024 that played no part in the
tuning (22 of them gave scoreable steps). The baseline, "even growth", burns
the same area as the simulator but spreads it evenly outward from the same
stretch of perimeter, with no wind, fuel or terrain.

| Holdout score | All 150 steps | 44 steps where the fire ran 1,500 m or more |
| --- | --- | --- |
| Observed new cells the simulator reached (recall) | 30.2% | 26.5% |
| Same, even-growth baseline | 33.1% | 19.2% |
| Overlap (IoU) | 0.109 | 0.122 |
| Overlap, even-growth baseline | 0.110 | 0.092 |
| Head direction within 45° of observed (chance 25%) | 56.0% | 70.5% |
| Median head direction error | 41° | 23° |
| Simulated / observed head distance (median) | 0.81 | 0.36 |
| Simulated / observed new area (median) | 1.44 | 0.87 |

## What the scores say

- The simulator gets the direction of spread right far more often than chance,
  and more so when the fire makes a real run.
- On steps where the fire ran 1,500 m or more it beats even growth. Over all
  steps it does not: most steps are small, patchy growth around the edge that
  wind does not explain.
- It under-predicts big runs (0.36 of the observed distance). Step-to-step,
  the observed run length is only weakly related to wind (correlation 0.32)
  and the Initial Spread Index (0.13) in this dataset.

## Limits

- Satellites pass about twice a day, so the 1- and 3-hour horizons in the
  proposal cannot be scored; only steps of 6 hours or more are.
- Satellites see only where fire is active at a pass. Observed cells are a
  lower bound on what burned, which is why recall is reported ahead of overlap.
- Weather is reanalysis on a coarse grid (one series per fire), not the wind
  at the fire front.
- Fuel is the 2024 map with a repair inside the perimeter, not the fuel on the
  day of the fire.
- No crown-fire transition, spotting or suppression. Repeated runs for
  confidence bands are not built yet.

## Interface

`viewer/fire_spread_viewer.html` is a self-contained page: pick a fire, step
through satellite passes, play the simulated growth hour by hour, and see the
next pass's cells marked as reached or missed, with that step's wind, moisture
and scores. It also shows the first 6 hours from the reported ignition point.

The fire is drawn on a map you can drag and zoom. The ground is the Canada
Base Map – Transportation from Natural Resources Canada (rivers, lakes,
wetlands, height contours, roads and place names), used under the Open
Government Licence – Canada and credited on the map. The service draws in
EPSG:3978, the simulator's own grid, so the map lines up with the cells
without warping. `scripts/spread_basemap.py` fetches one image per fire and a
closer one around each ignition run, the first time the viewer is exported;
`data/raw/basemap/manifest.json` records each request and checksum. The fuel
types the simulator actually uses can be switched on over the map.
Three holdout and three calibration fires are included, chosen by rule (most
scored steps, grid of 130,000 cells or fewer), not by how well they turned out.

## Run it

Needs the dataset build's local fuel and terrain windows (`data/raw/fuel_terrain/windows/`).

```sh
python scripts/run_spread_replay.py          # tune, score, write data/processed/simulation/
python scripts/export_spread_viewer_data.py  # rebuild viewer/fire_spread_viewer.html
python -m unittest tests.test_fire_spread_simulator -v
```
