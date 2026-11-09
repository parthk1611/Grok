"""Tests for the Monte Carlo band layer (scripts/spread_montecarlo.py, task T1.7).

Everything runs on made-up grids (uniform spruce, constant weather); nothing here is real fire data.
Grids are at least 201 x 201 cells of 90 m.  With the default (provisional) sigmas a few of the largest runs reach the
edge of a 201-cell grid; the tests that depend on whole bands use 301 cells or report `edge_runs`.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from jsonschema import Draft202012Validator
from pyproj import Geod, Transformer
from shapely.geometry import shape


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import spread_api as API  # noqa: E402
import spread_montecarlo as MC  # noqa: E402
import simulate_spread as SIM  # noqa: E402


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "spread_output.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
START = MC.SYNTHETIC_START
FIXED_TIME = "2026-11-02T12:00:00Z"
HORIZONS = (1, 3, 6)


def mc(case: SIM.FireCase | None = None, n_runs: int = 40, seed: int = 385, **kwargs) -> MC.MonteCarloResult:
    case = case or MC.synthetic_case(size=201)
    kwargs.setdefault("mode", "retrospective")
    kwargs.setdefault("run_timestamp", FIXED_TIME)
    return MC.run_monte_carlo(case, START, ignition_xy=MC.synthetic_ignition_xy(case), seed=seed, n_runs=n_runs, **MC.SYNTHETIC_LABELS, **kwargs)


def zero_sigmas() -> MC.PerturbationConfig:
    return MC.PerturbationConfig(wind_speed_log_sigma=0.0, wind_direction_sigma_deg=0.0, ffmc_sigma=0.0, ros_scale_log_sigma=0.0)


def band(payload: dict, horizon: int, name: str) -> dict:
    return next(f for f in payload["features"] if f["properties"]["horizon_h"] == horizon and f["properties"]["band"] == name)


def geometry(payload: dict, horizon: int, name: str):
    return shape(band(payload, horizon, name)["geometry"])


def errors(payload: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in VALIDATOR.iter_errors(payload)]


class SchemaTest(unittest.TestCase):
    def test_default_output_validates_against_contract(self) -> None:
        payload = mc(MC.synthetic_case(size=301)).geojson
        self.assertEqual(errors(payload), [])
        self.assertEqual(len(payload["features"]), 9)
        self.assertEqual({(f["properties"]["horizon_h"], f["properties"]["band"]) for f in payload["features"]}, {(h, b) for h in HORIZONS for b in ("p10", "p50", "p90")})

    def test_required_labels_and_fixed_values(self) -> None:
        payload = mc().geojson
        self.assertEqual(payload["disclaimer"], MC.DISCLAIMER)
        self.assertEqual(payload["validated_horizons_h"], [], msg="nothing is validated yet")
        self.assertTrue(all(f["properties"]["validated"] is False for f in payload["features"]))
        self.assertEqual(payload["model_version"], "blaze-fbp-mc-0.1.0-dev")
        self.assertEqual(payload["grid_resolution_m"], 90.0)
        self.assertEqual(payload["mode"], "retrospective")
        self.assertRegex(payload["run_timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(payload["start_time"], "2019-07-10T18:00:00Z")
        self.assertEqual(payload["inputs"]["wind_direction_deg"], 270.0)
        self.assertIn("NOT real", payload["inputs"]["weather_source"])
        self.assertTrue(all(f["geometry"]["type"] == "MultiPolygon" for f in payload["features"]))

    def test_n_runs_and_seed_are_recorded(self) -> None:
        payload = mc(n_runs=25, seed=7).geojson
        self.assertEqual((payload["n_runs"], payload["seed"]), (25, 7))

    def test_perturbation_record_lists_every_perturbed_input_and_the_base_ros_scale(self) -> None:
        parameters = {p["name"]: p for p in mc().geojson["perturbation"]["parameters"]}
        self.assertEqual(set(parameters), {"ln_factor_wind_speed_kmh", "wind_direction_deg", "ffmc", "ln_factor_ros_scale", "ros_scale"})
        self.assertEqual(parameters["ros_scale"], {"name": "ros_scale", "distribution": "fixed", "value": 0.8})
        self.assertEqual(parameters["ffmc"]["low"], 0.0)
        self.assertEqual(parameters["ffmc"]["high"], 101.0)
        self.assertEqual(parameters["wind_direction_deg"]["sigma"], MC.PerturbationConfig().wind_direction_sigma_deg)

    def test_weather_series_is_the_base_hourly_weather_over_the_horizon(self) -> None:
        series = mc().geojson["inputs"]["weather_series"]
        self.assertEqual([row["valid_time"] for row in series], [_z(START + pd.Timedelta(hours=h)) for h in range(7)])
        self.assertTrue(all(row["wind_speed_kmh"] == 20.0 and row["wind_direction_deg"] == 270.0 for row in series))
        self.assertNotIn("weather_series", mc(include_weather_series=False).geojson["inputs"])

    def test_stale_level_and_flag_are_consistent_and_validate(self) -> None:
        for level in MC.STALE_LEVELS:
            payload = mc(stale_level=level, age_minutes=45.0, stale_reason=None if level == "fresh" else "test").geojson
            self.assertEqual(payload["inputs"]["stale_flag"], level != "fresh")
            self.assertEqual(payload["age_minutes"], 45.0)
            self.assertEqual(errors(payload), [], msg=level)

    def test_bad_arguments_raise(self) -> None:
        for kwargs in ({"mode": "live"}, {"stale_level": "old"}, {"n_runs": 0}, {"horizons": (2,)}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                mc(**kwargs)

    def test_off_grid_point_and_short_weather_raise(self) -> None:
        case = MC.synthetic_case(size=201)
        with self.assertRaises(ValueError):
            MC.run_monte_carlo(case, START, ignition_xy=(1e9, 1e9), seed=1, mode="retrospective", n_runs=2, **MC.SYNTHETIC_LABELS)
        with self.assertRaises(ValueError):  # 12 hours of weather from 18:00, so a start at 08:00 next day is not covered
            MC.run_monte_carlo(case, START + pd.Timedelta(hours=14), ignition_xy=MC.synthetic_ignition_xy(case), seed=1, mode="retrospective", n_runs=2, **MC.SYNTHETIC_LABELS)

    def test_the_cases_weather_is_not_modified(self) -> None:
        case = MC.synthetic_case(size=201)
        before = case.weather.copy()
        mc(case)
        pd.testing.assert_frame_equal(case.weather, before)


class EmptyBandTest(unittest.TestCase):
    def test_empty_multipolygon_validates_and_is_what_an_unburnable_ignition_emits(self) -> None:
        fuel = np.full((201, 201), MC.SPRUCE, dtype=np.int32)
        fuel[100, 100] = SIM.WATER_CODE
        payload = mc(MC.synthetic_case(fuel=fuel)).geojson
        self.assertEqual(errors(payload), [])
        for feature in payload["features"]:
            self.assertEqual(feature["geometry"], {"type": "MultiPolygon", "coordinates": []})
            self.assertEqual(feature["properties"]["area_ha"], 0)
            self.assertEqual(feature["properties"]["perimeter_km"], 0)
            self.assertIsNone(feature["properties"]["head_direction_deg"])

    def test_an_empty_band_is_accepted_by_the_schema_but_a_bare_empty_polygon_would_too(self) -> None:
        # the schema only asks for type in (Polygon, MultiPolygon) and an array; check both empty forms validate
        payload = mc().geojson
        for kind in ("MultiPolygon", "Polygon"):
            trial = json.loads(json.dumps(payload))
            trial["features"][0]["geometry"] = {"type": kind, "coordinates": []}
            trial["features"][0]["properties"]["area_ha"] = 0
            self.assertEqual(errors(trial), [], msg=kind)

    def test_a_burnable_start_cell_means_no_band_is_ever_empty(self) -> None:
        # the ignition cell burns in every run, so even p90 at 1 h contains it
        payload = mc(n_runs=30, config=MC.PerturbationConfig(wind_speed_log_sigma=1.0, ros_scale_log_sigma=1.0)).geojson
        self.assertTrue(all(f["properties"]["area_ha"] >= 0.81 for f in payload["features"]))


class NestingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = mc(MC.synthetic_case(size=301), n_runs=60)
        cls.payload = cls.result.geojson

    def test_bands_are_nested_within_each_horizon(self) -> None:
        for h in HORIZONS:
            p10, p50, p90 = (geometry(self.payload, h, b) for b in ("p10", "p50", "p90"))
            self.assertLess(p90.difference(p50).area, 1e-9, msg=f"{h} h p90 outside p50")
            self.assertLess(p50.difference(p10).area, 1e-9, msg=f"{h} h p50 outside p10")

    def test_areas_order_p10_p50_p90_and_grow_with_horizon(self) -> None:
        area = {(f["properties"]["horizon_h"], f["properties"]["band"]): f["properties"]["area_ha"] for f in self.payload["features"]}
        for h in HORIZONS:
            self.assertGreaterEqual(area[h, "p10"], area[h, "p50"])
            self.assertGreaterEqual(area[h, "p50"], area[h, "p90"])
            self.assertGreater(area[h, "p10"], area[h, "p90"], msg="with sigmas > 0 the bands should have some width")
        for b in ("p10", "p50", "p90"):
            self.assertLess(area[1, b], area[3, b])
            self.assertLess(area[3, b], area[6, b])

    def test_each_band_is_inside_the_same_band_at_the_next_horizon(self) -> None:
        for b in ("p10", "p50", "p90"):
            for small, large in ((1, 3), (3, 6)):
                self.assertLess(geometry(self.payload, small, b).difference(geometry(self.payload, large, b)).area, 1e-9, msg=f"{b}: {small} h outside {large} h")

    def test_burn_fraction_grids_are_ordered_and_bands_are_its_thresholds(self) -> None:
        fractions = self.result.burn_fraction
        self.assertTrue((fractions[1] <= fractions[3]).all() and (fractions[3] <= fractions[6]).all())
        for h in HORIZONS:
            for name, level in MC.BANDS:
                cells = int((fractions[h] >= level - 1e-6).sum())
                self.assertAlmostEqual(band(self.payload, h, name)["properties"]["area_ha"], cells * 0.81, places=2)

    def test_sampled_bands_match_the_definition_on_a_tiny_ensemble(self) -> None:
        # 10 runs: p90 needs 9 of 10, p50 5 of 10, p10 1 of 10
        self.assertEqual([MC._cell_threshold(level, 10) for _, level in MC.BANDS], [1, 5, 9])
        self.assertEqual([MC._cell_threshold(level, 1) for _, level in MC.BANDS], [1, 1, 1])


class DeterminismTest(unittest.TestCase):
    def test_same_seed_is_byte_identical_ignoring_run_timestamp(self) -> None:
        one = mc(seed=11, run_timestamp="2026-01-01T00:00:00Z").geojson
        two = mc(seed=11, run_timestamp="2027-02-02T10:20:30Z").geojson
        self.assertNotEqual(one["run_timestamp"], two["run_timestamp"])
        one.pop("run_timestamp"), two.pop("run_timestamp")
        self.assertEqual(MC.to_json(one), MC.to_json(two))

    def test_same_seed_and_timestamp_is_fully_identical(self) -> None:
        self.assertEqual(MC.to_json(mc(seed=3).geojson), MC.to_json(mc(seed=3).geojson))

    def test_different_seeds_differ_when_sigmas_are_positive(self) -> None:
        one, two = mc(seed=1).geojson, mc(seed=2).geojson
        self.assertNotEqual([f["geometry"] for f in one["features"]], [f["geometry"] for f in two["features"]])

    def test_different_seeds_are_identical_when_sigmas_are_zero(self) -> None:
        one, two = mc(seed=1, config=zero_sigmas(), n_runs=5).geojson, mc(seed=2, config=zero_sigmas(), n_runs=5).geojson
        self.assertEqual([f["geometry"] for f in one["features"]], [f["geometry"] for f in two["features"]])
        self.assertEqual(one["seed"], 1)

    def test_draws_follow_the_configured_sigmas(self) -> None:
        zero = mc(config=zero_sigmas(), n_runs=6).draws
        self.assertTrue((zero["wind_speed_factor"] == 1.0).all() and (zero["wind_direction_offset_deg"] == 0.0).all() and (zero["ffmc_offset"] == 0.0).all())
        self.assertTrue((zero["ros_scale"] == 0.8).all())
        draws = mc(n_runs=4000, config=MC.PerturbationConfig(), horizons=(1,), case=MC.synthetic_case(size=201, hours=4)).draws
        config = MC.PerturbationConfig()
        self.assertAlmostEqual(float(np.log(draws["wind_speed_factor"]).std()), config.wind_speed_log_sigma, delta=0.02)
        self.assertAlmostEqual(float(draws["wind_direction_offset_deg"].std()), config.wind_direction_sigma_deg, delta=0.8)
        self.assertAlmostEqual(float(np.log(draws["ros_scale"] / 0.8).mean()), 0.0, delta=0.02)


class CollapseTest(unittest.TestCase):
    def test_zero_sigmas_collapse_all_bands_to_the_deterministic_run(self) -> None:
        case = MC.synthetic_case(size=201)
        result = mc(case, n_runs=7, config=zero_sigmas())
        reference = API.run_point(case, START, ignition_xy=MC.synthetic_ignition_xy(case), settings=SIM.Settings(ros_scale=0.8))
        self.assertEqual(result.geojson["uncertainty_status"], "not_estimated")
        for h in HORIZONS:
            for name in ("p10", "p50", "p90"):
                self.assertAlmostEqual(band(result.geojson, h, name)["properties"]["area_ha"], reference.area_ha[float(h)], places=2, msg=f"{h} h {name}")
            np.testing.assert_array_equal(result.burn_fraction[h] > 0, reference.masks[float(h)])
            self.assertTrue(set(np.unique(result.burn_fraction[h])) <= {0.0, 1.0})
        self.assertEqual(errors(result.geojson), [])

    def test_a_single_run_is_not_estimated_even_with_sigmas(self) -> None:
        self.assertEqual(mc(n_runs=1).geojson["uncertainty_status"], "not_estimated")
        self.assertEqual(mc(n_runs=2).geojson["uncertainty_status"], "estimated")

    def test_a_single_run_is_the_unperturbed_base_run_whatever_the_seed(self) -> None:
        case = MC.synthetic_case(size=201)
        base = API.run_point(case, START, ignition_xy=MC.synthetic_ignition_xy(case), settings=SIM.Settings(ros_scale=0.8))
        for seed in (7, 385):
            result = mc(case, n_runs=1, seed=seed)
            for h in HORIZONS:
                np.testing.assert_array_equal(result.burn_fraction[h] > 0, base.masks[float(h)], err_msg=f"seed {seed}, {h} h")


class GeometryTest(unittest.TestCase):
    def test_head_direction_is_downwind_in_true_bearing(self) -> None:
        for wind_from, expected in ((270.0, 90.0), (0.0, 180.0), (225.0, 45.0)):
            case = MC.synthetic_case(size=301, wind_from=wind_from)
            payload = mc(case, n_runs=40).geojson
            for h in HORIZONS:
                head = band(payload, h, "p50")["properties"]["head_direction_deg"]
                self.assertLess(abs((head - expected + 180.0) % 360.0 - 180.0), 10.0, msg=f"wind from {wind_from}, {h} h: {head}")

    def test_geometry_bearing_in_lonlat_matches_head_direction_where_grid_north_is_rotated(self) -> None:
        # far from the central meridian grid north differs from true north; the reprojected polygon must still point downwind
        case = MC.synthetic_case(size=301, centre_xy=(900_000.0, 600_000.0))
        self.assertGreater(abs(case.convergence_deg), 3.0, msg="this test needs a rotated grid")
        payload = mc(case, n_runs=40, config=zero_sigmas()).geojson
        lon, lat = payload["ignition_point"]["longitude"], payload["ignition_point"]["latitude"]
        for h in HORIZONS:
            centroid = geometry(payload, h, "p50").centroid
            bearing = Geod(ellps="GRS80").inv(lon, lat, centroid.x, centroid.y)[0] % 360
            self.assertLess(abs(bearing - 90.0), 4.0, msg=f"{h} h: polygon bearing {bearing:.1f}")
            self.assertLess(abs(band(payload, h, "p50")["properties"]["head_direction_deg"] - 90.0), 4.0)

    def test_area_and_perimeter_are_measured_on_the_grid(self) -> None:
        payload = mc(config=zero_sigmas(), n_runs=3).geojson
        to_grid = Transformer.from_crs("EPSG:4326", "EPSG:3978", always_xy=True)
        for h in HORIZONS:
            feature = band(payload, h, "p50")
            projected = shapely.transform(geometry(payload, h, "p50"), lambda xy: np.column_stack(to_grid.transform(xy[:, 0], xy[:, 1])))
            self.assertAlmostEqual(projected.area / 10_000.0, feature["properties"]["area_ha"], delta=feature["properties"]["area_ha"] * 1e-4 + 0.01)
            self.assertAlmostEqual(projected.length / 1000.0, feature["properties"]["perimeter_km"], delta=0.02)

    def test_ignition_point_is_inside_every_band_and_matches_lonlat_input(self) -> None:
        case = MC.synthetic_case(size=201)
        payload = mc(case).geojson
        point = shapely.Point(payload["ignition_point"]["longitude"], payload["ignition_point"]["latitude"])
        for feature in payload["features"]:
            self.assertTrue(shape(feature["geometry"]).buffer(1e-4).contains(point))
        by_lonlat = MC.run_monte_carlo(case, START, lonlat=(payload["ignition_point"]["longitude"], payload["ignition_point"]["latitude"]), seed=385, n_runs=40, mode="retrospective", run_timestamp=FIXED_TIME, **MC.SYNTHETIC_LABELS)
        self.assertEqual(MC.to_json(by_lonlat.geojson), MC.to_json(payload))

    def test_exterior_rings_are_counter_clockwise_per_rfc_7946(self) -> None:
        for feature in mc().geojson["features"]:
            for polygon in feature["geometry"]["coordinates"]:
                self.assertTrue(shapely.LinearRing(polygon[0]).is_ccw)
                self.assertTrue(all(not shapely.LinearRing(hole).is_ccw for hole in polygon[1:]))

    def test_a_barrier_splits_bands_into_valid_geometries(self) -> None:
        fuel = np.full((301, 301), MC.SPRUCE, dtype=np.int32)
        fuel[:, 150 + 25] = SIM.WATER_CODE  # 1-cell wall downwind
        fuel[120:131, 150 + 10 : 150 + 14] = SIM.WATER_CODE  # a small lake the fire goes round (hole or notch)
        payload = mc(MC.synthetic_case(fuel=fuel), n_runs=30).geojson
        self.assertEqual(errors(payload), [])
        self.assertTrue(all(shape(f["geometry"]).is_valid for f in payload["features"]))

    def test_edge_runs_are_counted_when_the_grid_is_too_small(self) -> None:
        small = mc(MC.synthetic_case(size=61), n_runs=5, config=zero_sigmas())
        self.assertEqual(small.edge_runs, 5)
        self.assertEqual(mc(MC.synthetic_case(size=401), n_runs=5, config=zero_sigmas()).edge_runs, 0)


class DirectionBiasTest(unittest.TestCase):
    """Wind-direction perturbation on uniform fuel and flat ground.  The true burned area cannot depend on the wind azimuth
    there, so any variation of single-run area with direction is the grid artefact (about +11% on diagonals at 6 h, see
    test_spread_point_api.GridAnisotropyTest).  Measured numbers are in docs/spread_montecarlo.md."""

    def test_direction_only_perturbation_changes_run_area_only_through_the_grid_artefact(self) -> None:
        config = MC.PerturbationConfig(wind_speed_log_sigma=0.0, wind_direction_sigma_deg=15.0, ffmc_sigma=0.0, ros_scale_log_sigma=0.0)
        result = mc(MC.synthetic_case(size=301), n_runs=60, config=config)
        areas = result.run_area_ha[:, HORIZONS.index(6)]
        cv = float(areas.std() / areas.mean())
        spread = float(areas.max() / areas.min())
        self.assertGreater(cv, 0.0, msg="direction perturbation moves cells between runs, so areas should vary a little")
        self.assertLess(spread, 1.25, msg="grid artefact should stay near the +11% diagonal bias, not more")
        print(f"\n[direction-only, 6 h, sigma 15 deg, n=60, 301x301] per-run area CV {cv:.3%}, max/min {spread:.3f}", file=sys.stderr)

    def test_axis_aligned_perturbation_centres_are_equal(self) -> None:
        areas = []
        for wind_from in (0.0, 90.0, 180.0, 270.0):
            result = mc(MC.synthetic_case(size=301, wind_from=wind_from), n_runs=3, config=zero_sigmas())
            areas.append(band(result.geojson, 6, "p50")["properties"]["area_ha"])
        self.assertAlmostEqual(max(areas) / min(areas), 1.0, delta=0.01)


class RuntimeTest(unittest.TestCase):
    def test_runtime_is_recorded_for_100_and_200_runs(self) -> None:
        case = MC.synthetic_case(size=201)
        for n_runs in (100, 200):
            tick = time.perf_counter()
            result = mc(case, n_runs=n_runs)
            seconds = time.perf_counter() - tick
            print(f"\n[runtime] 201x201, n_runs={n_runs}: {seconds:.2f} s total ({result.timings_s['simulate_s']:.2f} s simulate, {result.timings_s['polygonize_s']:.3f} s polygonize)", file=sys.stderr)
            self.assertLess(seconds, 120.0, msg="loose guard only; the numbers above are the record")


def _z(moment: pd.Timestamp) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    unittest.main()
