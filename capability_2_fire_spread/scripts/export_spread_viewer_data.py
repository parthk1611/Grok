#!/usr/bin/env python3
"""Export simulator runs for the browser viewer.

Replays a few fires with the tuned settings and writes `viewer/fire_spread_viewer.html`:
the page in `viewer/viewer_template.html` with the data written into it, so the
one file opens anywhere.  For each fire it keeps the fuel
grid, where satellites saw the fire at each pass, the simulated arrival time
of every cell for each step between passes, the hourly wind, and the step's
scores.  It also runs the first six hours from the reported ignition point.

The ground under the fire is the Canada Base Map (see `spread_basemap.py`):
one image for each fire's window and a closer one around its ignition run,
downloaded the first time they are needed and kept in `data/raw/basemap/`.

Fires are chosen by rule, not by how well they turned out: in each split, the
scored fires with the most usable steps whose grid is small enough to ship.
"""

from __future__ import annotations

import argparse
import base64
import json

import numpy as np
import pandas as pd
from scipy import ndimage

import run_spread_replay as replay
import spread_basemap as basemap
from simulate_spread import CELL_375_M, FireCase, Settings, load_case, simulate, weather_at
from spread_common import PROCESSED, PROJECT_ROOT, action_log


OUTPUT = PROJECT_ROOT / "viewer/spread_viewer_data.js"
TEMPLATE = PROJECT_ROOT / "viewer/viewer_template.html"
PAGE = PROJECT_ROOT / "viewer/fire_spread_viewer.html"
DATA_TAG = '<script src="spread_viewer_data.js"></script>'
FUEL_CLASS = {1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 7: 2, 415: 3, 625: 3, 650: 3, 675: 3, 11: 4, 13: 4, 31: 5, 101: 1, 105: 1, 102: 0}
MAX_GRID_CELLS = 130_000
IGNITION_HOURS = 6.0
IGNITION_LOCAL_HOUR = 13
IGNITION_VIEW_MARGIN_CELLS, IGNITION_VIEW_MIN_CELLS = 14, 22
WINDOW_MAP_PX, IGNITION_MAP_PX = 1800, 1400  # longest side of the base-map images


def b64(array: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(array).tobytes()).decode("ascii")


def view_box(shape: tuple[int, int], rows: np.ndarray, columns: np.ndarray) -> dict[str, int]:
    """A 3:2 box of cells around the given cells, kept inside the grid: what the viewer shows of an ignition run."""
    span = max(int(columns.max() - columns.min()), int(rows.max() - rows.min()), IGNITION_VIEW_MIN_CELLS) + 2 * IGNITION_VIEW_MARGIN_CELLS
    height, width = min(shape[0], span), min(shape[1], round(span * 1.5))
    r0 = int(np.clip(round((rows.min() + rows.max()) / 2 - height / 2), 0, shape[0] - height))
    c0 = int(np.clip(round((columns.min() + columns.max()) / 2 - width / 2), 0, shape[1] - width))
    return {"r0": r0, "c0": c0, "rows": int(height), "columns": int(width)}


def window_map_box(shape: tuple[int, int]) -> dict[str, int]:
    """A 3:2 box of cells around the fire's window, reaching past it, so the map fills the viewer in wide and square views alike."""
    rows = round(max(shape) * 1.1)
    columns = round(rows * 1.5)
    return {"r0": round((shape[0] - rows) / 2), "c0": round((shape[1] - columns) / 2), "rows": rows, "columns": columns}


def map_image(name: str, case: FireCase, box: dict[str, int], longest_px: int, log) -> dict[str, object] | None:
    """The base map for a box of cells, ready to write into the page; None if the map service cannot be reached."""
    west, north = case.west + box["c0"] * case.pixel, case.north - box["r0"] * case.pixel
    try:
        path, fresh = basemap.fetch(name, west, north - box["rows"] * case.pixel, west + box["columns"] * case.pixel, north, longest_px)
    except (OSError, RuntimeError) as error:
        log.info(f"WARNING: no base map for {name} ({error}); the viewer draws the fuel map there instead")
        return None
    if fresh:
        log.info(f"downloaded base map {path.name} from {basemap.SERVICE}, {path.stat().st_size / 1024:.0f} KB")
    return {"src": basemap.data_uri(path), **box}


def export_fire(fire_id: str, settings: Settings, fire_row, step_scores: pd.DataFrame, log, with_map: bool) -> dict[str, object]:
    case = load_case(fire_id, settings, replay._shared)
    layout = replay.Layout(case.cell375_keys)
    lookup = {key: index for index, key in enumerate(case.cell375_keys)}
    cells = case.arrival[[key in lookup for key in zip(case.arrival.cell_x, case.arrival.cell_y)]]
    cell_index = np.array([lookup[key] for key in zip(cells.cell_x, cells.cell_y)], dtype=int)
    first_seen = cells.first_observation.values
    seen = replay._shared["detections"][fire_id]
    seen_keys = list(zip(np.floor(seen.x / CELL_375_M).astype(int), np.floor(seen.y / CELL_375_M).astype(int)))
    in_window = np.array([key in lookup for key in seen_keys])
    seen_index = np.array([lookup[key] for key, keep in zip(seen_keys, in_window) if keep], dtype=int)
    seen_pass = seen.observation.values[in_window]
    times = case.observation_times
    square = np.ones((3, 3), dtype=bool)
    count = len(case.cell375_keys)

    first_detected = np.zeros(count, dtype=np.uint16)
    first_detected[cell_index] = first_seen
    first_in_area = np.zeros(count, dtype=np.uint16)  # first pass at which the cell is inside the burned area
    steps = []
    scored = {int(row.from_observation): row for row in step_scores.itertuples()}
    for observation in times.index:
        detected = np.zeros(count, dtype=bool)
        detected[cell_index[first_seen <= observation]] = True
        area = layout.burned_area(detected)
        first_in_area[area & (first_in_area == 0)] = observation
        if observation not in scored:
            continue
        row = scored[observation]
        recent = np.zeros(count, dtype=bool)
        recent[seen_index[(seen_pass <= observation) & (seen_pass > observation - replay.ACTIVE_PASSES)]] = True
        edge = area & ~layout.flat(ndimage.binary_erosion(layout.grid(area), structure=square))
        active = edge & layout.flat(ndimage.binary_dilation(layout.grid(recent), structure=square, iterations=replay.ACTIVE_REACH_CELLS))
        if not active.any():
            active = edge
        arrival = simulate(case, area[case.cell375], times[observation], min(row.hours, replay.MAX_HOURS), settings, active=active[case.cell375])
        reached = np.isfinite(arrival) & (arrival > 0)
        index = np.flatnonzero(reached.ravel()).astype(np.uint32)
        weather = case.weather[(case.weather.time_utc >= times[observation].floor("h")) & (case.weather.time_utc <= times[observation + 1])]
        steps.append(
            {
                "from": int(observation),
                "to": int(observation + 1),
                "startLocal": (times[observation] + pd.Timedelta(hours=int(fire_row.utc_offset_hours))).strftime("%Y-%m-%d %H:%M"),
                "hours": float(row.hours),
                "cells": b64(index),
                "minutes": b64(np.minimum(arrival.ravel()[index], 65535).astype(np.uint16)),
                "wind": [[round(float(w.wind_speed_kmh), 1), round(float(w.wind_direction_deg))] for w in weather.itertuples()],
                "ffmc": round(float(weather.ffmc_daily.iloc[0]), 1),
                "bui": round(float(weather.bui.iloc[0]), 1),
                "tempMax": round(float(weather.temp_c.max()), 1),
                "rhMin": round(float(weather.rh_pct.min())),
                "score": {
                    "observedNew": int(row.observed_new_cells),
                    "simulatedNew": int(row.simulated_new_cells),
                    "recall": None if pd.isna(row.recall) else round(float(row.recall), 3),
                    "evenRecall": None if pd.isna(row.even_growth_recall) else round(float(row.even_growth_recall), 3),
                    "observedDistance": round(float(row.observed_distance_m)),
                    "simulatedDistance": None if pd.isna(row.simulated_distance_m) else round(float(row.simulated_distance_m)),
                    "directionError": None if pd.isna(row.direction_error_deg) else round(float(row.direction_error_deg)),
                },
            }
        )

    ignition = None
    if case.ignition_cell is not None:
        # Start at 13:00 local standard time on the day satellites first saw the fire: the reported
        # start date has no time of day, and early afternoon is when a new fire usually makes its first run.
        offset = pd.Timedelta(hours=int(fire_row.utc_offset_hours))
        begin = (times.iloc[0] + offset).normalize() + pd.Timedelta(hours=IGNITION_LOCAL_HOUR) - offset
        burnable = np.vectorize(lambda code: FUEL_CLASS.get(int(code), 1) >= 2, otypes=[bool])(case.fuel)
        cell, moved_m = case.ignition_cell, 0.0
        if not burnable[cell]:  # the reported point can fall on a cell the 90 m map calls water or non-fuel
            nearest = ndimage.distance_transform_edt(~burnable, return_distances=True, return_indices=True)
            cell, moved_m = (int(nearest[1][0][cell]), int(nearest[1][1][cell])), float(nearest[0][cell] * case.pixel)
        start = np.zeros(case.shape, dtype=bool)
        start[cell] = True
        arrival = simulate(case, start, begin, IGNITION_HOURS, settings)
        reached = np.isfinite(arrival)
        index = np.flatnonzero(reached.ravel()).astype(np.uint32)
        conditions = weather_at(case, begin)
        hours = case.weather[(case.weather.time_utc >= begin.floor("h"))].head(int(IGNITION_HOURS) + 1)
        view = view_box(case.shape, *np.nonzero(reached))
        ignition = {
            "view": view,
            "basemap": map_image(f"{fire_id}_ignition", case, view, IGNITION_MAP_PX, log) if with_map else None,
            "row": cell[0],
            "column": cell[1],
            "movedMetres": round(moved_m),
            "startLocal": (begin + offset).strftime("%Y-%m-%d %H:%M"),
            "cells": b64(index),
            "minutes": b64(np.minimum(arrival.ravel()[index], 65535).astype(np.uint16)),
            "wind": [[round(float(w.wind_speed_kmh), 1), round(float(w.wind_direction_deg))] for w in hours.itertuples()],
            "ffmc": round(float(hours.ffmc_daily.iloc[0]), 1),
            "bui": round(conditions["bui"], 1),
            "tempMax": round(float(hours.temp_c.max()), 1),
            "rhMin": round(float(hours.rh_pct.min())),
        }

    fuel_class = np.vectorize(lambda code: FUEL_CLASS.get(int(code), 1), otypes=[np.uint8])(case.fuel)
    keys = np.array(case.cell375_keys, dtype=np.int32)
    return {
        "id": fire_id,
        "split": fire_row.split,
        "year": int(fire_row.fire_year),
        "sizeHa": float(fire_row.reported_size_ha),
        "cause": fire_row.cause,
        "response": fire_row.response_code,
        "startDate": fire_row.start_date,
        "rows": int(case.shape[0]),
        "columns": int(case.shape[1]),
        "pixel": float(case.pixel),
        "west": float(case.west),
        "north": float(case.north),
        "convergence": round(float(case.convergence_deg), 2),
        "basemap": map_image(fire_id, case, window_map_box(case.shape), WINDOW_MAP_PX, log) if with_map else None,
        "fuel": b64(fuel_class),
        "cellX": b64(keys[:, 0]),
        "cellY": b64(keys[:, 1]),
        "firstDetected": b64(first_detected),
        "firstInArea": b64(first_in_area),
        "steps": steps,
        "ignition": ignition,
    }


def write_page(data_script: str) -> None:
    """One self-contained page: the template with the data written into it, so it opens from a file or anywhere it is hosted."""
    template = TEMPLATE.read_text(encoding="utf-8")
    if DATA_TAG not in template:
        raise ValueError(f"{TEMPLATE.name} no longer contains {DATA_TAG}")
    PAGE.write_text(template.replace(DATA_TAG, "<script>" + data_script + "</script>"), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fires-per-split", type=int, default=3)
    parser.add_argument("--page-only", action="store_true", help="rebuild the page from the template and the data already exported, without simulating again")
    parser.add_argument("--no-base-map", action="store_true", help="leave out the Canada Base Map images; the viewer then draws the fuel map as its ground")
    args = parser.parse_args()
    log = action_log("11 viewer export")
    if args.page_only:
        write_page(OUTPUT.read_text(encoding="utf-8").strip())
        log.info(f"DONE: rebuilt {PAGE.name} from {TEMPLATE.name} and the existing {OUTPUT.name}, {PAGE.stat().st_size / 1_048_576:.2f} MB")
        return
    scores = json.loads((PROCESSED / "simulation/spread_simulator_v1_scores.json").read_text(encoding="utf-8"))
    settings = Settings(ros_scale=scores["chosen_ros_scale"], flank_scale=scores["chosen_flank_scale"])
    fires = pd.read_csv(PROCESSED / "ontario_fire_spread_fires_v1.csv", dtype={"fire_id": str}).set_index("fire_id")
    windows = {w["fire_id"]: w["rows"] * w["columns"] for w in json.loads((PROJECT_ROOT / "data/raw/fuel_terrain/fuel_windows_manifest.json").read_text(encoding="utf-8"))["windows"]}
    holdout = pd.read_csv(PROCESSED / "simulation/spread_simulator_holdout_steps_v1.csv", dtype={"fire_id": str})
    calibration = pd.read_csv(PROCESSED / "simulation/spread_simulator_calibration_steps_v1.csv", dtype={"fire_id": str})
    calibration = calibration[np.isclose(calibration.ros_scale, settings.ros_scale) & np.isclose(calibration.flank_scale, settings.flank_scale)]
    chosen: list[tuple[str, pd.DataFrame]] = []
    for table in (holdout, calibration):
        counts = table.groupby("fire_id").size()
        counts = counts[[windows[fire_id] <= MAX_GRID_CELLS for fire_id in counts.index]].sort_values(ascending=False, kind="stable")
        chosen += [(fire_id, table[table.fire_id == fire_id]) for fire_id in counts.index[: args.fires_per_split]]
    log.info(f"START: exporting {len(chosen)} fires for the viewer at rate-of-spread scale {settings.ros_scale} and flank scale {settings.flank_scale}: {[fire_id for fire_id, _ in chosen]}")
    replay._shared.setdefault("detections", dict(tuple(pd.read_csv(PROCESSED / "ontario_fire_spread_detections_v1.csv.gz", dtype={"fire_id": str}, usecols=["fire_id", "observation", "x", "y"]).groupby("fire_id"))))
    payload = {
        "settings": {
            "rosScale": settings.ros_scale, "flankScale": settings.flank_scale, "cell375": CELL_375_M, "detectableShare": replay.DETECTABLE_SHARE,
            "ignitionHours": IGNITION_HOURS, "basemapCredit": basemap.CREDIT,
        },  # fmt: skip
        "scores": {"holdout": scores["holdout"], "longRuns": next(value for key, value in scores.items() if key.startswith("holdout_steps_with")), "calibrationFires": len(scores["calibration_fires"]), "holdoutFires": scores["holdout"]["fires"]},
        "fires": [export_fire(fire_id, settings, fires.loc[fire_id], table, log, not args.no_base_map) for fire_id, table in chosen],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    data_script = "window.SPREAD_VIEWER_DATA = " + json.dumps(payload, separators=(",", ":")) + ";"
    OUTPUT.write_text(data_script + "\n", encoding="utf-8")
    write_page(data_script)
    log.info(f"DONE: wrote {PAGE.name}, {PAGE.stat().st_size / 1_048_576:.2f} MB, {sum(len(fire['steps']) for fire in payload['fires'])} steps from {len(payload['fires'])} fires")


if __name__ == "__main__":
    main()
