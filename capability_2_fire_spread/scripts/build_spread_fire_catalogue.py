#!/usr/bin/env python3
"""Clean Ontario's fire layers into the list of fires the spread dataset covers.

Step 2 of the fire-spread build.  A fire is kept when it is a wildfire with a
final perimeter polygon, started in the satellite era, and has usable dates.
Each kept fire gets its reported start point from the point layer, its cleaned
perimeter, and quality flags.  Nothing is imputed: a fire that fails a check
is dropped and counted, or kept with a flag that says what is uncertain.

Needs pandas, numpy, shapely, and pyproj.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from pyproj import Geod
from shapely import contains_xy
from shapely.geometry import Point, mapping, shape
from shapely.ops import nearest_points

from spread_common import DATASET_VERSION, INTERIM, RAW, action_log


SOURCE = RAW / "fire_disturbance"
FIRST_SATELLITE_YEAR = 2012  # VIIRS on Suomi NPP has detections from 20 January 2012.
CAUSES = {
    "LTG": "lightning", "REC": "recreation", "RES": "resident", "RWY": "railway", "MIS": "miscellaneous",
    "INC": "incendiary", "IDF": "industrial forestry", "IDO": "industrial other", "UNK": "unknown",
}  # fmt: skip
HUMAN_CAUSES = {"REC", "RES", "RWY", "MIS", "INC", "IDF", "IDO"}
FIRE_FIELDS = [
    "fire_id", "dataset_version", "fire_year", "fire_ident", "district_code",
    "start_date", "out_date", "duration_days", "reported_size_ha", "size_class",
    "cause_code", "cause", "cause_group", "response_code", "location_accuracy",
    "ignition_longitude", "ignition_latitude", "ignition_source", "ignition_inside_perimeter",
    "ignition_distance_to_perimeter_m", "start_date_matches_point_record",
    "perimeter_area_ha", "perimeter_to_reported_size_ratio", "perimeter_parts", "perimeter_was_repaired",
    "centroid_longitude", "centroid_latitude", "bbox_west", "bbox_south", "bbox_east", "bbox_north",
]  # fmt: skip


def load_layer(name: str) -> pd.DataFrame:
    features = json.loads((SOURCE / f"fire_disturbance_{name}.geojson").read_text(encoding="utf-8"))["features"]
    frame = pd.DataFrame([feature["properties"] for feature in features])
    frame["geometry"] = [shape(feature["geometry"]) if feature["geometry"] else None for feature in features]
    for column in ("FIRE_START_DATE", "FIRE_OUT_DATE"):
        frame[column] = pd.to_datetime(frame[column], unit="ms", errors="coerce").dt.normalize()
    return frame


def size_class(hectares: float) -> str:
    for limit, label in ((200, "40-200 ha"), (1_000, "200-1,000 ha"), (10_000, "1,000-10,000 ha")):
        if hectares < limit:
            return label
    return "10,000 ha or more"


def build(args: argparse.Namespace) -> dict[str, object]:
    log = action_log("2 fire catalogue")
    geod = Geod(ellps="GRS80")
    points, areas = load_layer("point"), load_layer("area")
    log.info(f"START: loaded {len(points):,} fire points and {len(areas):,} fire perimeters")
    counts: dict[str, int] = {"point_records": len(points), "area_records": len(areas)}

    wildfire = areas.FIRE_TYPE_CODE == "IFR"
    counts["dropped_not_a_wildfire"] = int((~wildfire).sum())
    log.info(f"dropped {counts['dropped_not_a_wildfire']} perimeters that are prescribed burns (PB) or outside the fire region (OFR)")
    areas = areas[wildfire]
    early = areas.FIRE_YEAR < args.first_year
    counts["dropped_before_satellite_era"] = int(early.sum())
    log.info(f"dropped {counts['dropped_before_satellite_era']:,} perimeters from before {args.first_year} (no VIIRS satellite coverage)")
    areas = areas[~early]
    bad_dates = areas.FIRE_START_DATE.isna() | areas.FIRE_OUT_DATE.isna() | (areas.FIRE_OUT_DATE < areas.FIRE_START_DATE)
    counts["dropped_unusable_dates"] = int(bad_dates.sum())
    log.info(f"dropped {counts['dropped_unusable_dates']} perimeters with a missing start/out date or an out date before the start date")
    areas = areas[~bad_dates].copy()
    duplicate = areas.duplicated(["FIRE_DISTURBANCE_AREA_IDENT", "FIRE_YEAR"], keep=False)
    counts["dropped_ambiguous_identifier"] = int(duplicate.sum())
    log.info(f"dropped {counts['dropped_ambiguous_identifier']} perimeters whose fire identifier repeats within a year")
    areas = areas[~duplicate].copy()

    # One point record per (identifier, year); where the point layer repeats a key, take the record
    # whose final size is closest to the perimeter's.
    candidates = areas[["FIRE_DISTURBANCE_AREA_IDENT", "FIRE_YEAR", "FIRE_FINAL_SIZE"]].merge(
        points, on=["FIRE_DISTURBANCE_AREA_IDENT", "FIRE_YEAR"], how="left", suffixes=("", "_point")
    )
    candidates["size_gap"] = (candidates.FIRE_FINAL_SIZE - candidates.FIRE_FINAL_SIZE_point).abs()
    counts["fires_with_several_point_records"] = int(candidates.duplicated(["FIRE_DISTURBANCE_AREA_IDENT", "FIRE_YEAR"]).sum())
    matched = candidates.sort_values("size_gap").drop_duplicates(["FIRE_DISTURBANCE_AREA_IDENT", "FIRE_YEAR"]).set_index(["FIRE_DISTURBANCE_AREA_IDENT", "FIRE_YEAR"])
    counts["fires_without_a_point_record"] = int(matched.geometry.isna().sum())
    log.info(
        f"linked perimeters to point records on (identifier, year): {len(matched) - counts['fires_without_a_point_record']:,} linked, "
        f"{counts['fires_without_a_point_record']} without a point, {counts['fires_with_several_point_records']} resolved from repeated point keys"
    )

    rows, features = [], []
    repaired = 0
    for record in areas.sort_values(["FIRE_YEAR", "FIRE_DISTURBANCE_AREA_IDENT"]).itertuples():
        ident, year = record.FIRE_DISTURBANCE_AREA_IDENT, int(record.FIRE_YEAR)
        perimeter = record.geometry
        was_repaired = not perimeter.is_valid
        if was_repaired:
            perimeter = perimeter.buffer(0)
            repaired += 1
        area_ha = abs(geod.geometry_area_perimeter(perimeter)[0]) / 10_000
        point_record = matched.loc[(ident, year)]
        point = point_record.geometry
        if point is None or (isinstance(point, float) and np.isnan(point)):
            ignition = (None, None, "none", None, None)
        else:
            inside = bool(contains_xy(perimeter, point.x, point.y))
            if inside:
                distance = 0.0
            else:
                nearest = nearest_points(perimeter, Point(point.x, point.y))[0]
                distance = geod.inv(point.x, point.y, nearest.x, nearest.y)[2]
            ignition = (point.x, point.y, "ontario_fire_disturbance_point", inside, distance)
        west, south, east, north = perimeter.bounds
        cause = record.FIRE_GENERAL_CAUSE_CODE if isinstance(record.FIRE_GENERAL_CAUSE_CODE, str) else "UNK"
        fire_id = f"{year}_{ident}"
        rows.append(
            {
                "fire_id": fire_id,
                "dataset_version": DATASET_VERSION,
                "fire_year": year,
                "fire_ident": ident,
                "district_code": "".join(character for character in ident if character.isalpha()),
                "start_date": record.FIRE_START_DATE.date().isoformat(),
                "out_date": record.FIRE_OUT_DATE.date().isoformat(),
                "duration_days": (record.FIRE_OUT_DATE - record.FIRE_START_DATE).days + 1,
                "reported_size_ha": round(float(record.FIRE_FINAL_SIZE), 2),
                "size_class": size_class(float(record.FIRE_FINAL_SIZE)),
                "cause_code": cause,
                "cause": CAUSES.get(cause, "unknown"),
                "cause_group": "lightning" if cause == "LTG" else "human" if cause in HUMAN_CAUSES else "unknown",
                "response_code": record.FIRE_RESPONSE_OBJ_CODE if isinstance(record.FIRE_RESPONSE_OBJ_CODE, str) else "",
                "location_accuracy": record.LOCATION_ACCURACY if isinstance(record.LOCATION_ACCURACY, str) else "",
                "ignition_longitude": None if ignition[0] is None else round(ignition[0], 5),
                "ignition_latitude": None if ignition[1] is None else round(ignition[1], 5),
                "ignition_source": ignition[2],
                "ignition_inside_perimeter": ignition[3],
                "ignition_distance_to_perimeter_m": None if ignition[4] is None else round(ignition[4]),
                "start_date_matches_point_record": bool(point_record.FIRE_START_DATE == record.FIRE_START_DATE) if ignition[0] is not None else None,
                "perimeter_area_ha": round(area_ha, 2),
                "perimeter_to_reported_size_ratio": round(area_ha / float(record.FIRE_FINAL_SIZE), 3),
                "perimeter_parts": len(getattr(perimeter, "geoms", [perimeter])),
                "perimeter_was_repaired": was_repaired,
                "centroid_longitude": round(perimeter.centroid.x, 5),
                "centroid_latitude": round(perimeter.centroid.y, 5),
                "bbox_west": round(west, 5),
                "bbox_south": round(south, 5),
                "bbox_east": round(east, 5),
                "bbox_north": round(north, 5),
            }
        )
        features.append({"type": "Feature", "properties": {"fire_id": fire_id, "fire_year": year, "reported_size_ha": float(record.FIRE_FINAL_SIZE)}, "geometry": mapping(perimeter)})
    log.info(f"repaired {repaired} self-intersecting perimeter polygons")
    fires = pd.DataFrame(rows, columns=FIRE_FIELDS)
    if not fires.fire_id.is_unique:
        raise AssertionError("fire_id is not unique")

    INTERIM.mkdir(parents=True, exist_ok=True)
    fires_path = INTERIM / "spread_fires.csv"
    perimeters_path = INTERIM / "spread_fire_perimeters.geojson"
    fires.to_csv(fires_path, index=False)
    perimeters_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")

    outside = fires[fires.ignition_inside_perimeter == False]  # noqa: E712 - None means no point record
    counts.update(
        {
            "fires_kept": len(fires),
            "perimeters_repaired": repaired,
            "ignition_point_inside_perimeter": int((fires.ignition_inside_perimeter == True).sum()),  # noqa: E712
            "ignition_point_outside_perimeter": len(outside),
            "perimeter_area_within_20pct_of_reported_size": int(fires.perimeter_to_reported_size_ratio.between(0.8, 1.2).sum()),
        }
    )
    report = {
        "dataset_version": DATASET_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "selection": {
            "fire_type": "IFR (wildfire inside the fire region); prescribed burns and outside-region fires dropped",
            "first_fire_year": args.first_year,
            "requires": "a final perimeter polygon, a start date, and an out date on or after the start date",
        },
        "counts": counts,
        "fires_by_year": {str(year): int(n) for year, n in fires.groupby("fire_year").size().items()},
        "fires_by_size_class": fires.size_class.value_counts().to_dict(),
        "fires_by_cause_group": fires.cause_group.value_counts().to_dict(),
        "fires_by_response_code": fires.response_code.replace("", "blank").value_counts().to_dict(),
        "notes": [
            "Dates carry no time of day; a fire's start date is the reported start, not the first satellite detection.",
            "The reported point is the agency's fire location. It is used as the ignition point and flagged when it falls outside the perimeter.",
            "response_code describes how the fire was managed. It is kept to interpret spread results and must not be an ignition predictor.",
            "FIRE_WEATHER_INDEX from the source is deliberately not carried forward.",
        ],
        "outputs": {"fires": fires_path.name, "perimeters": perimeters_path.name},
    }
    (INTERIM / "spread_fires_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(
        f"DONE: kept {len(fires):,} fires for {fires.fire_year.min()}-{fires.fire_year.max()}; "
        f"ignition point inside its perimeter for {counts['ignition_point_inside_perimeter']}, outside for {len(outside)} "
        f"(median {outside.ignition_distance_to_perimeter_m.median():.0f} m away); wrote {fires_path.name} and {perimeters_path.name}"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-year", type=int, default=FIRST_SATELLITE_YEAR)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
