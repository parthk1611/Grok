#!/usr/bin/env python3
"""Summarise the fuel-type window of each fire.

Step 6b of the fire-spread build.  For every fire with a fuel window this
measures the mix of fuel types inside the final perimeter and in the ring
around it: shares of conifer, mixedwood, deciduous, open, non-fuel and water,
the dominant fuel type, and a percent-conifer estimate.

The fuel map is from 2024.  Inside the perimeter of an older fire it shows what
has regrown since, so the ring just outside the perimeter is the better guide
to what the fire burned through.  Both are reported.  Needs numpy, pandas,
Pillow, and shapely.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import shapely
from PIL import Image

from spread_common import DATASET_VERSION, INTERIM, RAW, action_log, load_perimeters_3978


SOURCE = RAW / "fuel_terrain"
# Codes of the FBP Fuel Types 2024 map, from its published legend:
# code -> (fuel type, description, group, conifer share of the stand in percent)
FUEL_CODES = {
    1: ("C-1", "Spruce-Lichen Woodland", "conifer", 100),
    2: ("C-2", "Boreal Spruce", "conifer", 100),
    3: ("C-3", "Mature Jack or Lodgepole Pine", "conifer", 100),
    4: ("C-4", "Immature Jack or Lodgepole Pine", "conifer", 100),
    5: ("C-5", "Red and White Pine", "conifer", 100),
    7: ("C-7", "Ponderosa Pine / Douglas Fir", "conifer", 100),
    11: ("D-1", "Leafless Aspen", "deciduous", 0),
    13: ("D-1/D-2", "Aspen", "deciduous", 0),
    31: ("O-1a", "Matted Grass", "open", 0),
    101: ("NF", "Non-fuel", "non_fuel", None),
    102: ("WA", "Water", "water", None),
    105: ("VNF", "Vegetated Non-Fuel", "non_fuel", None),
    415: ("M-1 (15% conifer)", "Boreal Mixedwood - Leafless", "mixedwood", 15),
    625: ("M-1/M-2 (25% conifer)", "Boreal Mixedwood - Green", "mixedwood", 25),
    650: ("M-1/M-2 (50% conifer)", "Boreal Mixedwood - Green", "mixedwood", 50),
    675: ("M-1/M-2 (75% conifer)", "Boreal Mixedwood - Green", "mixedwood", 75),
}
GROUPS = ["conifer", "mixedwood", "deciduous", "open", "non_fuel", "water"]


def summarise(codes: np.ndarray, prefix: str) -> dict[str, object]:
    values, counts = np.unique(codes, return_counts=True)
    total = int(counts.sum())
    result: dict[str, object] = {f"{prefix}_cells": total}
    shares = dict.fromkeys(GROUPS, 0.0)
    unknown = conifer_weight = burnable = 0.0
    for value, count in zip(values.tolist(), counts.tolist()):
        entry = FUEL_CODES.get(value)
        if entry is None:
            unknown += count
            continue
        shares[entry[2]] += count
        if entry[3] is not None:
            burnable += count
            conifer_weight += count * entry[3]
    for group in GROUPS:
        result[f"{prefix}_{group}_share"] = round(shares[group] / total, 4) if total else None
    result[f"{prefix}_unrecognised_code_share"] = round(unknown / total, 4) if total else None
    result[f"{prefix}_percent_conifer"] = round(conifer_weight / burnable, 1) if burnable else None
    known = [(count, value) for value, count in zip(values.tolist(), counts.tolist()) if value in FUEL_CODES and FUEL_CODES[value][2] not in ("water", "non_fuel")]
    result[f"{prefix}_dominant_fuel_type"] = FUEL_CODES[max(known)[1]][0] if known else ""
    return result


def main() -> None:
    log = action_log("6b fuel summary")
    manifest = json.loads((SOURCE / "fuel_windows_manifest.json").read_text(encoding="utf-8"))
    perimeters = load_perimeters_3978()
    log.info(f"START: fuel summaries for {len(manifest['windows'])} fire windows; codes from the FBP Fuel Types 2024 legend")
    rows = []
    unrecognised: set[int] = set()
    for window in manifest["windows"]:
        codes = np.array(Image.open(SOURCE / "windows" / window["filename"]))
        if codes.shape != (window["rows"], window["columns"]):
            raise ValueError(f"{window['filename']}: expected {(window['rows'], window['columns'])}, got {codes.shape}")
        unrecognised.update(set(np.unique(codes).tolist()) - set(FUEL_CODES))
        pixel = window["pixel_size_m"]
        x = window["west"] + (np.arange(window["columns"]) + 0.5) * pixel
        y = window["north"] - (np.arange(window["rows"]) + 0.5) * pixel
        grid_x, grid_y = np.meshgrid(x, y)
        perimeter = perimeters[window["fire_id"]]
        shapely.prepare(perimeter)
        inside = shapely.contains_xy(perimeter, grid_x, grid_y)
        row = {"fire_id": window["fire_id"], "fuel_map_year": manifest["fuel_map_year"], "fuel_window_file": window["filename"]}
        row.update(summarise(codes[inside], "fuel_inside") if inside.any() else {"fuel_inside_cells": 0})
        row.update(summarise(codes[~inside], "fuel_around"))
        rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(INTERIM / "spread_fuel_summary.csv", index=False)
    pd.DataFrame(
        [{"code": code, "fuel_type": entry[0], "description": entry[1], "group": entry[2], "percent_conifer": entry[3]} for code, entry in FUEL_CODES.items()]
    ).to_csv(INTERIM / "fbp_fuel_type_codes.csv", index=False)
    report = {
        "dataset_version": DATASET_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "fires": len(table),
        "unrecognised_codes_seen": sorted(unrecognised),
        "dominant_fuel_type_around_fires": table.fuel_around_dominant_fuel_type.value_counts().to_dict(),
        "median_shares_around_fires": {group: round(float(table[f"fuel_around_{group}_share"].median()), 3) for group in GROUPS},
        "median_shares_inside_perimeters": {group: round(float(table[f"fuel_inside_{group}_share"].median()), 3) for group in GROUPS},
        "caveat": "The fuel map is from 2024; inside older perimeters it shows post-fire regrowth, not the fuel that burned.",
        "outputs": {"summary": "spread_fuel_summary.csv", "codes": "fbp_fuel_type_codes.csv"},
    }
    (INTERIM / "spread_fuel_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(f"DONE: fuel summary for {len(table)} fires; dominant fuel around fires: {report['dominant_fuel_type_around_fires']}; unrecognised codes: {sorted(unrecognised) or 'none'}")


if __name__ == "__main__":
    main()
