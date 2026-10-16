#!/usr/bin/env python3
"""Replay real fires through the simulator and score it against the satellite record.

For each fire, and each pair of consecutive satellite observations, the
simulator starts from the area burned up to the first observation, runs for the
hours until the second, and is scored on the new cells it predicts against the
new cells the satellite saw.  Scoring is on the satellite's own 375 m grid.

A satellite pass shows only where fire is active at that moment, so the cells
seen so far are a patchy under-count of what has burned.  The starting area is
therefore those cells with the gaps between them closed and holes filled.  For
the same reason the observed new cells are a lower bound: the fair questions
are whether the simulator reaches them (recall), how far its head runs against
theirs, and whether it runs the same way.

Two things are done here, in order:

1. two settings are tuned on a sample of calibration fires: a scale on every
   rate of spread, and a further scale on the flank and back rates, to the
   values at which the simulated head runs as far as the observed one and the
   simulated area matches what the satellite record implies;
2. the tuned simulator is scored once on a sample of holdout fires.

Each result is set beside a baseline that grows the fire by the same area
evenly in all directions, so the score shows what wind, fuel and terrain add.
Needs the packages listed in requirements.txt, including rasterio.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from multiprocessing import Pool

import numpy as np
import pandas as pd
from scipy import ndimage
from scipy.spatial import cKDTree

from simulate_spread import CELL_375_M, Settings, load_case, simulate
from spread_common import DATASET_VERSION, PROCESSED, action_log


MIN_HOURS, MAX_HOURS = 6.0, 30.0  # shorter steps move less than one 375 m cell
MIN_NEW_CELLS = 5
SCALES = [0.3, 0.5, 0.8, 1.2]
FLANK_SCALES = [0.1, 0.25, 0.5, 1.0]
TARGET_AREA_RATIO = 1.3  # satellite cells cover a median 77% of a fire's final perimeter area
CLOSING_PASSES = 2  # each pass bridges gaps one cell wider between detections
DETECTABLE_SHARE = 0.25  # a 375 m cell counts as reached once fire covers this much of it
LONG_RUN_M = 1500.0  # steps whose observed head ran at least this far are also scored on their own
ACTIVE_PASSES = 1  # flames seen in this many most recent passes mark the perimeter as still alight
ACTIVE_REACH_CELLS = 1  # how far (in 375 m cells) from those detections the perimeter counts as alight
_shared: dict = {}


class Layout:
    """The case's 375 m cells arranged as a 2-D array, for neighbourhood operations."""

    def __init__(self, keys: list[tuple[int, int]]) -> None:
        array = np.array(keys, dtype=int)
        self.x, self.y = array[:, 0] - array[:, 0].min(), array[:, 1] - array[:, 1].min()
        self.shape = (int(self.x.max()) + 1, int(self.y.max()) + 1)

    def grid(self, mask: np.ndarray) -> np.ndarray:
        out = np.zeros(self.shape, dtype=bool)
        out[self.x[mask], self.y[mask]] = True
        return out

    def flat(self, grid: np.ndarray) -> np.ndarray:
        return grid[self.x, self.y]

    def burned_area(self, detected: np.ndarray) -> np.ndarray:
        """Detected cells with the gaps between them closed and holes filled."""
        closed = ndimage.binary_closing(self.grid(detected), structure=np.ones((3, 3), dtype=bool), iterations=CLOSING_PASSES)
        return self.flat(ndimage.binary_fill_holes(closed)) | detected


def _bearing(offsets: np.ndarray) -> float | None:
    east, north = offsets[:, 0].mean(), offsets[:, 1].mean()
    return None if np.hypot(east, north) < 1e-9 else float(np.degrees(np.arctan2(east, north)) % 360)


def _growth_shape(new_keys: np.ndarray, start_keys: np.ndarray) -> tuple[float, float | None]:
    """90th-percentile distance (m) and head bearing of new cells from the nearest starting cell."""
    distance, index = cKDTree(start_keys).query(new_keys)
    offsets = (new_keys - start_keys[index]) * CELL_375_M
    head = distance >= np.quantile(distance, 0.9)
    return float(np.quantile(distance, 0.9) * CELL_375_M), _bearing(offsets[head])


def score_step(case, layout: Layout, start: np.ndarray, active: np.ndarray, observed_new: np.ndarray, simulated_new: np.ndarray) -> dict[str, float | None]:
    """Compare predicted and observed new 375 m cells (boolean arrays over the case's 375 m cells)."""
    keys = np.array(case.cell375_keys, dtype=float)
    hits = int((simulated_new & observed_new).sum())
    union = int((simulated_new | observed_new).sum())
    result = {
        "observed_new_cells": int(observed_new.sum()),
        "simulated_new_cells": int(simulated_new.sum()),
        "iou": hits / union if union else None,
        "recall": hits / int(observed_new.sum()),
        "precision": hits / int(simulated_new.sum()) if simulated_new.any() else None,
        "area_ratio": float(simulated_new.sum() / observed_new.sum()),
    }
    observed_distance, observed_bearing = _growth_shape(keys[observed_new], keys[start])
    result["observed_distance_m"] = observed_distance
    # bearings are true bearings: grid bearing plus the grid's convergence at this fire
    result["observed_head_bearing_deg"] = None if observed_bearing is None else (observed_bearing + case.convergence_deg) % 360
    if simulated_new.any():
        simulated_distance, simulated_bearing = _growth_shape(keys[simulated_new], keys[start])
        result["simulated_distance_m"] = simulated_distance
        result["simulated_head_bearing_deg"] = None if simulated_bearing is None else (simulated_bearing + case.convergence_deg) % 360
        if observed_bearing is not None and simulated_bearing is not None:
            result["direction_error_deg"] = float(abs((simulated_bearing - observed_bearing + 180) % 360 - 180))
    # Baseline: the same number of new cells, taken evenly outward from the same active stretch of perimeter.
    distance = layout.flat(ndimage.distance_transform_edt(~layout.grid(active)))
    distance[start] = np.inf
    count = int(simulated_new.sum())
    if count:
        even = np.zeros(len(keys), dtype=bool)
        even[np.argsort(distance, kind="stable")[:count]] = True
        even_hits = int((even & observed_new).sum())
        result["even_growth_iou"] = even_hits / int((even | observed_new).sum())
        result["even_growth_recall"] = even_hits / int(observed_new.sum())
    return result


def replay_fire(fire_id: str, settings: Settings, keep_grids: bool = False):
    """Score every usable step of one fire.  With `keep_grids`, also return what the viewer needs."""
    case = load_case(fire_id, settings, _shared)
    lookup = {key: index for index, key in enumerate(case.cell375_keys)}
    cells = case.arrival[[key in lookup for key in zip(case.arrival.cell_x, case.arrival.cell_y)]]
    cell_index = np.array([lookup[key] for key in zip(cells.cell_x, cells.cell_y)], dtype=int)
    first_seen = cells.first_observation.values
    if "detections" not in _shared:
        table = pd.read_csv(PROCESSED / "ontario_fire_spread_detections_v1.csv.gz", dtype={"fire_id": str}, usecols=["fire_id", "observation", "x", "y"])
        _shared["detections"] = dict(tuple(table.groupby("fire_id")))
    seen = _shared["detections"][fire_id]
    seen_keys = list(zip(np.floor(seen.x / CELL_375_M).astype(int), np.floor(seen.y / CELL_375_M).astype(int)))
    in_window = np.array([key in lookup for key in seen_keys])
    seen_index = np.array([lookup[key] for key, keep in zip(seen_keys, in_window) if keep], dtype=int)
    seen_pass = seen.observation.values[in_window]
    times = case.observation_times
    layout = Layout(case.cell375_keys)
    square = np.ones((3, 3), dtype=bool)
    rows = []
    stitched = np.full(case.shape, np.inf, dtype=np.float32) if keep_grids else None
    origin = times.iloc[0]
    for observation in times.index[:-1]:
        nxt = observation + 1
        if nxt not in times.index:
            continue
        hours = (times[nxt] - times[observation]).total_seconds() / 3600
        detected = np.zeros(len(case.cell375_keys), dtype=bool)
        detected[cell_index[first_seen <= observation]] = True
        start = layout.burned_area(detected)
        observed_new = np.zeros_like(start)
        observed_new[cell_index[first_seen == nxt]] = True
        observed_new &= ~start
        usable = MIN_HOURS <= hours <= MAX_HOURS and observed_new.sum() >= MIN_NEW_CELLS and start.any()
        if not usable and not keep_grids:
            continue
        # Fire spreads from the stretch of perimeter near where the last passes saw flames, not from
        # edges that burned out days ago.
        recent = np.zeros_like(start)
        recent[seen_index[(seen_pass <= observation) & (seen_pass > observation - ACTIVE_PASSES)]] = True
        edge = start & ~layout.flat(ndimage.binary_erosion(layout.grid(start), structure=square))
        active = edge & layout.flat(ndimage.binary_dilation(layout.grid(recent), structure=square, iterations=ACTIVE_REACH_CELLS))
        if not active.any():
            active = edge
        burning = start[case.cell375]
        arrival = simulate(case, burning, times[observation], min(hours, MAX_HOURS), settings, active=active[case.cell375])
        reached = np.isfinite(arrival)
        if keep_grids:
            since_origin = (times[observation] - origin).total_seconds() / 3600 + arrival / 60.0
            stitched = np.minimum(stitched, np.where(reached, since_origin, np.inf).astype(np.float32))
        if usable:
            simulated_new = case.burned_375(reached, DETECTABLE_SHARE) & ~start
            rows.append({"fire_id": fire_id, "from_observation": int(observation), "to_observation": int(nxt), "hours": round(hours, 2), **score_step(case, layout, start, active, observed_new, simulated_new)})
    return (rows, case, stitched) if keep_grids else rows


def _worker(job: tuple[str, float, float]) -> list[dict]:
    fire_id, ros_scale, flank_scale = job
    rows = replay_fire(fire_id, Settings(ros_scale=ros_scale, flank_scale=flank_scale))
    for row in rows:
        row["ros_scale"], row["flank_scale"] = ros_scale, flank_scale
    return rows


def summarise(scores: pd.DataFrame) -> dict[str, float]:
    return {
        "steps": int(len(scores)),
        "fires": int(scores.fire_id.nunique()),
        "mean_recall": round(float(scores.recall.mean()), 4),
        "mean_even_growth_recall": round(float(scores.even_growth_recall.fillna(0).mean()), 4),
        "mean_iou": round(float(scores.iou.mean()), 4),
        "mean_even_growth_iou": round(float(scores.even_growth_iou.fillna(0).mean()), 4),
        "mean_precision": round(float(scores.precision.fillna(0).mean()), 4),
        "median_area_ratio": round(float(scores.area_ratio.median()), 3),
        "median_direction_error_deg": round(float(scores.direction_error_deg.median()), 1),
        "share_direction_within_45_deg": round(float((scores.direction_error_deg <= 45).mean()), 4),
        # a step where the simulator predicts no growth counts as a head distance of zero
        "median_distance_ratio": round(float((scores.simulated_distance_m.fillna(0) / scores.observed_distance_m).median()), 3),
        "distance_rank_correlation": round(float(scores.simulated_distance_m.fillna(0).corr(scores.observed_distance_m, method="spearman")), 3),
    }


def pick_fires(split: str, count: int, seed: int) -> list[str]:
    """A seeded sample of trackable, monitored fires small enough to replay quickly."""
    fires = pd.read_csv(PROCESSED / "ontario_fire_spread_fires_v1.csv", dtype={"fire_id": str})
    pool = fires[(fires.split == split) & fires.trackable & (fires.response_code == "MON") & fires.reported_size_ha.between(500, 30_000)]
    return sorted(pool.sample(min(count, len(pool)), random_state=seed).fire_id)


def tuning_error(summary: dict[str, float]) -> float:
    """How far a setting is from running the head as far as observed and burning the target area."""
    distance = abs(math.log(max(summary["median_distance_ratio"], 1e-6)))
    area = abs(math.log(max(summary["median_area_ratio"], 1e-6) / TARGET_AREA_RATIO))
    return distance + area


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-fires", type=int, default=24)
    parser.add_argument("--holdout-fires", type=int, default=24)
    parser.add_argument("--seed", type=int, default=385)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    log = action_log("10 simulator replay")
    calibration, holdout = pick_fires("calibration", args.calibration_fires, args.seed), pick_fires("holdout", args.holdout_fires, args.seed)
    log.info(
        f"START: tuning rate-of-spread scale {SCALES} and flank scale {FLANK_SCALES} on {len(calibration)} calibration fires, then scoring on {len(holdout)} holdout fires; "
        f"steps of {MIN_HOURS}-{MAX_HOURS} h with at least {MIN_NEW_CELLS} new cells; targets: head distance ratio 1, area ratio {TARGET_AREA_RATIO}"
    )
    with Pool(args.workers) as pool:
        jobs = [(fire, ros, flank) for fire in calibration for ros in SCALES for flank in FLANK_SCALES]
        tuning = pd.DataFrame([row for rows in pool.imap_unordered(_worker, jobs) for row in rows])
        by_setting = {setting: summarise(group) for setting, group in tuning.groupby(["ros_scale", "flank_scale"])}
        for (ros, flank), summary in by_setting.items():
            log.info(f"calibration rate {ros}, flank {flank}: head distance ratio {summary['median_distance_ratio']:.2f}, area ratio {summary['median_area_ratio']:.2f}, recall {summary['mean_recall']:.3f} (even growth {summary['mean_even_growth_recall']:.3f})")
        best = min(by_setting, key=lambda setting: tuning_error(by_setting[setting]))
        log.info(f"chosen rate-of-spread scale {best[0]} and flank scale {best[1]} (closest to the head-distance and area targets on calibration fires)")
        test = pd.DataFrame([row for rows in pool.imap_unordered(_worker, [(fire, best[0], best[1]) for fire in holdout]) for row in rows])
    holdout_summary = summarise(test)
    long_runs = summarise(test[test.observed_distance_m >= LONG_RUN_M])
    out = PROCESSED / "simulation"
    out.mkdir(parents=True, exist_ok=True)
    tuning.round(4).to_csv(out / "spread_simulator_calibration_steps_v1.csv", index=False)
    test.round(4).to_csv(out / "spread_simulator_holdout_steps_v1.csv", index=False)
    report = {
        "dataset_version": DATASET_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "simulator": "FBP rate of spread, wind ellipse, slope, shortest travel time on a 90 m grid; burn scar in the 2024 fuel map repaired from surrounding fuel",
        "scoring": f"new 375 m cells between consecutive satellite observations {MIN_HOURS}-{MAX_HOURS} h apart with at least {MIN_NEW_CELLS} observed new cells",
        "fire_selection": "seeded sample of trackable, monitored fires of 500-30,000 ha",
        "calibration_fires": calibration,
        "holdout_fires": holdout,
        "tuning_targets": {"median_head_distance_ratio": 1.0, "median_area_ratio": TARGET_AREA_RATIO, "why_this_area": "satellite cells cover a median 77% of a fire's final perimeter area, so true growth is about 1.3 times the cells seen"},
        "calibration_by_setting": {f"rate {ros}, flank {flank}": summary for (ros, flank), summary in by_setting.items()},
        "chosen_ros_scale": best[0],
        "chosen_flank_scale": best[1],
        "holdout": holdout_summary,
        f"holdout_steps_with_observed_head_run_of_{LONG_RUN_M:.0f}_m_or_more": long_runs,
        "direction_chance_level": 0.25,
        "note": "Satellites see only where fire is active at a pass, so observed new cells are a lower bound on what burned; recall is the fairer of the two rates.",
    }
    (out / "spread_simulator_v1_scores.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(
        f"DONE: holdout {holdout_summary['steps']} steps from {holdout_summary['fires']} fires: recall {holdout_summary['mean_recall']:.3f} "
        f"(even-growth baseline {holdout_summary['mean_even_growth_recall']:.3f}), head distance ratio {holdout_summary['median_distance_ratio']:.2f}, "
        f"area ratio {holdout_summary['median_area_ratio']:.2f}, direction within 45 deg {holdout_summary['share_direction_within_45_deg']:.1%}"
    )
    log.info(
        f"holdout long runs ({long_runs['steps']} steps, observed head at least {LONG_RUN_M:.0f} m): recall {long_runs['mean_recall']:.3f} "
        f"(even-growth baseline {long_runs['mean_even_growth_recall']:.3f}), direction within 45 deg {long_runs['share_direction_within_45_deg']:.1%}"
    )


if __name__ == "__main__":
    main()
