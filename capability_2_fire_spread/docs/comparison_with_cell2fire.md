# Our spread simulator compared with Cell2Fire

A side-by-side of the simulator in this folder (`scripts/simulate_spread.py`,
`scripts/fbp_spread.py`) and Cell2Fire, the open-source simulator the team
notes named as a candidate. It is a comparison of how the two work, from
Cell2Fire's paper, README and source code. Cell2Fire has not been run on our
fires, so this note does not say which one predicts them better.

## In short

The two are the same kind of model: a grid of cells, the Canadian FBP
rate-of-spread equations, an ellipse stretched downwind, and fire passed from
cell to cell. Cell2Fire is the fuller and faster one: it carries the whole FBP
system including crown fire, runs thousands of repetitions for burn-probability
maps, and is built for planning studies. Ours is smaller and leaves parts of
FBP out, but it comes with something Cell2Fire's release does not: a replay of
real Ontario fires against satellite observations, scored on fires it was not
tuned on.

## Side by side

| | Cell2Fire | Ours |
| --- | --- | --- |
| Built for | Strategic planning: burn-probability maps, comparing harvest plans | Replaying real Ontario fires and scoring the result |
| Grid | Equal square cells; 100 m in the paper's tests | 90 m cells in EPSG:3978 |
| Neighbours per cell | 8 | 16 (the 8 adjacent cells and 8 "knight moves"), which gives rounder fire shapes |
| How fire moves | Each burning cell advances fire along the lines to its neighbours' centres, in fixed time steps; a neighbour ignites when fire reaches its centre | Earliest arrival time at every cell by any path (shortest travel time), with no time step; each move takes half its time from each cell's fuel |
| Spread rates | Canadian FBP: head, flank and back rates shaped into an ellipse | Canadian FBP: head, flank and back rates shaped into an ellipse |
| How much of FBP | The full system: buildup effect, slope effect, foliar moisture, crown fraction burned, fire intensity, acceleration | Rate of spread and buildup effect only; no crown fire, foliar moisture, intensity or acceleration |
| Slope | FBP's slope effect, combined with the wind | The same FBP slope factor, applied as a speed-up on each uphill move between cells |
| Fuel moisture and codes | Read from the weather file; the user supplies FFMC, ISI, BUI and the rest | Calculated here from hourly temperature, humidity, wind and rain |
| Weather over time | Hourly rows; can hold several weather scenarios | Hourly wind; one fuel-moisture value per day |
| When spread stops | Below a rate-of-spread or fire-intensity threshold the user sets | Only in water and non-fuel, or when rates fall to zero |
| Randomness | Built in: random variation in spread rate, random ignitions, weather scenarios, many repetitions | None yet: one run per case |
| Adjustment settings | Optional multipliers on the head, flank and back rates and on the ellipse, all 1 unless set | Two multipliers, on all rates (0.8) and on the flank and back rates (0.25), fitted to past Ontario fires |
| Seasonal fuel changes | FBP's grass and green-aspen equations are in its code; how they are switched was not examined | Leaf-out and grass curing switched by date |
| Old burn scars in the fuel map | Not addressed; takes the fuel grid as given | Replaces mapped fuel inside each fire's perimeter with the fuel around it |
| Suppression | Not modelled | Not modelled |
| Spotting (embers) | Described in the paper as planned; the repository has a spotting source file, which was not examined | Not modelled |
| Language and speed | C++ core with a Python wrapper; parallel; tested up to 100 million cells | Python, one fire's window and one run at a time |
| Licence | GPL-3.0, marked "for research use only" | Part of this project |

## How each has been checked

These are different kinds of test, so the numbers cannot be set against each
other.

**Cell2Fire** was checked against another simulator, Prometheus, not against
observed fire growth. The paper explains that real perimeters were avoided
because suppression is hard to account for. On the Dogrib fire (Alberta, 2001,
10,216 ha) it agreed with Prometheus's burned area at about 92% by one measure
and 88% by a stricter one that compares shape, over 22 hours. On ten
hypothetical British Columbia fires the stricter measure averaged 68%. The
real Dogrib scar was compared by eye only.

**Ours** was checked against what satellites saw of real fires: 150 steps
from 22 Ontario fires from 2021–2024 that played no part in tuning. It put 56%
of fire runs within 45° of the observed direction (25% by chance), reached 30%
of newly burning cells against 33% for a simple even-growth baseline, and
beat that baseline only on the larger runs (27% against 19%). Details are in
`fire_spread_simulator.md`.

So Cell2Fire is shown to reproduce an established simulator closely, and ours
is shown to have modest skill against real observations. Neither result says
how Cell2Fire would score on our test.

## Where ours falls short of Cell2Fire

- **Crown fire and fire intensity.** Large boreal runs are usually crown
  fires. Leaving them out is a likely contributor to our under-prediction of
  big runs (0.36 of the observed distance on long runs).
- **Repeated runs.** The proposal asks for confidence bands from repeated
  runs. Cell2Fire has this built in; ours does not have it yet.
- **Speed and scale.** Cell2Fire is far faster and handles whole landscapes.
- **Track record.** Cell2Fire is published and has been used by others.

## Where ours adds something

- **A test against real fire growth**, on unseen fires, with a baseline to
  beat. Cell2Fire's published validation has no equivalent.
- **Fire-weather codes calculated from raw weather**, so it runs from an
  hourly forecast or reanalysis without a separate step.
- **Handling of the 2024 fuel map's burn scars**, without which any simulator
  replaying older fires would be fed regrowth where forest stood.
- **Rounder fire shapes** from sixteen directions of travel.
- **Small and readable**: about 400 lines of Python, with tests.

## If the team wants a head-to-head

The spread dataset already holds what Cell2Fire needs: a fuel grid and an
elevation grid per fire, hourly weather with the fire-weather codes, and
ignition points. The work would be to write those out in Cell2Fire's file
formats, build it (it compiles under Linux with g++, Boost and Eigen; this
machine would use its Ubuntu subsystem), run it over the same steps, and pass
its burned cells through the scoring in `run_spread_replay.py`. That would
answer the open question directly. The repository also points to C2FK as a
more actively maintained fork, which would be worth considering.

## Sources

- Pais, C., Carrasco, J., Martell, D. L., Weintraub, A. and Woodruff, D. L.
  (2021). Cell2Fire: A Cell-Based Forest Fire Growth Model to Support
  Strategic Landscape Management Planning. *Frontiers in Forests and Global
  Change*, 4. https://doi.org/10.3389/ffgc.2021.692706
- Cell2Fire repository, README and C++ source
  (`cell2fire/Cell2FireC/`: `CellsFBP.cpp`, `FBPfunc5_NoDebug.c`,
  `ReadArgs.cpp`), read 9 October 2026. https://github.com/cell2fire/Cell2Fire
