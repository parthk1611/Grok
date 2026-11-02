# Grid and time specification (contract v0.2.0)

Everything below is taken from the existing ignition panel unless marked
**NEW**. Sources: `scripts/build_cpa_daily_ignition_labels.py`
(`CELL_SIZE_M`, `cell_id`), `data/interim/ontario_fire_region_10km_grid.csv`,
`AGENTS.md`, `docs/ignition_features.md`.

## Spatial grid

| Item | Value |
| --- | --- |
| CRS | EPSG:3978 (NAD83 / Canada Atlas Lambert), metres |
| Cell size | 10,000 m x 10,000 m (10 km) |
| Lattice | Cell origins are the south-west corner, snapped to multiples of 10,000 m in EPSG:3978 (`floor(value / 10000) * 10000`) |
| Cell ID | `ofr10km_{x0}_{y0}`, `x0`/`y0` the integer SW-corner coordinates in metres (may be negative). Example from the repo grid: `ofr10km_-10000_100000` |
| Cell centre | `(x0 + 5000, y0 + 5000)` in EPSG:3978. Geographic centroid (EPSG:4326, 6 decimals) is in the grid CSV columns `centroid_longitude`, `centroid_latitude` |
| Today's extent | 9,673 cells; `grid_x_m` -20,000..1,480,000, `grid_y_m` -450,000..870,000; centroid lon -95.219372..-76.134849, lat 44.05916..56.81245 (from `data/interim/ontario_fire_region_10km_grid.csv`) |
| Region definition | Ontario Fire Response Plan Area sectors where `RES_SECTOR != 'OFR'` (OFR = outside fire region); centroid inside the region, plus edge cells containing a selected ignition. The area outside the fire region (southern Ontario) is **not** covered today |
| Authoritative cell list | `data/interim/ontario_fire_region_10km_grid.csv` (`grid_id`, `grid_x_m`, `grid_y_m`, `centroid_longitude`, `centroid_latitude`) |

**Extension rule (NEW).** Cells added for wider coverage keep the same
lattice and ID formula, so IDs stay globally unique and existing IDs never
change. The `ofr10km_` prefix is a legacy name and is kept for compatibility.
ATLAS appends new rows to the grid CSV and records the grid version below.
Existing cells are never renumbered, resized or dropped without Team Lead
approval, because the trained model and the locked 2019 test are defined on them.

`grid_version`: `ofr10km-v1` (the 9,673-cell grid). ATLAS bumps this when cells are added.

## Spread-simulation grid (separate from the 10 km ignition grid)

The spread simulator (`capability_2_fire_spread/scripts/simulate_spread.py`)
runs on a 90 m fuel/elevation grid, also EPSG:3978, and the replay scores on
375 m cells (`capability_2_fire_spread/docs/fire_spread_simulator.md`).
Spread outputs are published as GeoJSON in EPSG:4326 (see
`spread_output.schema.json`); the 10 km cell ID is not used inside spread output.

## Time

| Item | Rule |
| --- | --- |
| Local day | `America/Toronto` calendar date (`YYYY-MM-DD`), as in the panel (`ignition_date`) |
| Issue time | 00:00 local time on that date (daylight-saving aware); `issue_time_utc` in the panel is that instant |
| Ignition target | At least one ignition in the cell during that local day |
| Timestamps in all payloads | ISO 8601, UTC, `Z` suffix |
| Information cut-off | Only data available before the prediction time. In the historical panel the dynamic inputs are the prior-day CWFIS observation (`t - 1`), seven completed preceding days of rain, and ignitions strictly before the date |
| Known limitation | Parts of north-western Ontario use Central time. The panel and this contract use `America/Toronto` everywhere. Spread dataset rows carry a per-fire `utc_offset_hours`. A per-cell zone is not defined (open item) |
| Fire season | Panel covers May 1 - October 31 only |

## Units (match existing datasets)

Temperature deg C (`temp_c`), relative humidity % (`rh_pct`), precipitation mm
(`precip_mm`), wind speed km/h (`wind_speed_kmh`), wind direction in degrees the
wind blows **from** (`wind_direction_deg`), wind components `wind_u_kmh`
(east) and `wind_v_kmh` (north). These are the column names in
`capability_2_fire_spread/data/processed/ontario_fire_spread_weather_hourly_v1.csv.gz`.
