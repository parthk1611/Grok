"""Synthetic-grid tests for the point-start API (scripts/spread_api.py).

Every case is a made-up grid with fuel, wind and ground set by hand, so the
expected behaviour follows from the physics rather than from any real fire.
Some tests are marked expectedFailure: they document real simulator artefacts
(see their docstrings) and must be revisited, not deleted, if the simulator changes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import spread_api as API  # noqa: E402
import simulate_spread as SIM  # noqa: E402


START = pd.Timestamp("2019-07-10 18:00")
PIXEL = 90.0
SPRUCE = 2  # C-2 boreal spruce
GREEN_ASPEN = 13  # carries no fire in summer at BUI 64
SIZE = 201  # a 6 h fire at 20 km/h spreads about 65 cells downwind, so this keeps it off the edge
CENTRE = SIZE // 2


def synthetic_case(fuel: np.ndarray | None = None, wind: float = 20.0, wind_from: float = 270.0, ffmc: float = 90.0, bui: float = 64.0, convergence: float = 0.0) -> SIM.FireCase:
    fuel = np.full((SIZE, SIZE), SPRUCE, dtype=np.int32) if fuel is None else fuel
    weather = pd.DataFrame({
        "time_utc": pd.date_range(START, periods=12, freq="h"), "wind_speed_kmh": wind, "wind_direction_deg": wind_from, "ffmc": ffmc,
        "ffmc_daily": ffmc, "bui": bui, "day_of_year": 191, "temp_c": 25.0, "rh_pct": 30.0,
    })  # fmt: skip
    return SIM.FireCase(
        fire_id="SYNTH", west=0.0, north=fuel.shape[0] * PIXEL, pixel=PIXEL, fuel=fuel, fuel_as_mapped=fuel,
        elevation=np.zeros(fuel.shape, dtype=np.float32), weather=weather, convergence_deg=convergence, ignition_cell=None,
        arrival=pd.DataFrame(), observation_times=pd.Series(dtype="datetime64[ns]"),
    )  # fmt: skip


def centre_xy(case: SIM.FireCase, row: int = CENTRE, column: int = CENTRE) -> tuple[float, float]:
    """EPSG:3978 coordinates of the middle of a grid cell."""
    return case.west + (column + 0.5) * case.pixel, case.north - (row + 0.5) * case.pixel


def run(case: SIM.FireCase, **kwargs) -> API.PointResult:
    result = API.run_point(case, START, ignition_xy=kwargs.pop("ignition_xy", centre_xy(case)), **kwargs)
    border = np.zeros(case.shape, dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    assert not (result.masks[result.horizons[-1]] & border).any(), "fire reached the grid edge: the test grid is too small"
    return result


class IsotropyTest(unittest.TestCase):
    def test_no_wind_on_uniform_fuel_grows_round(self) -> None:
        result = run(synthetic_case(wind=0.0))
        for hours in (1, 3, 6):
            self.assertIsNone(API.head_bearing_deg(result, hours), msg="a round fire has no head direction")
            self.assertAlmostEqual(API.length_to_breadth(result, hours), 1.0, delta=0.05)
        mask = result.masks[6]
        rows, columns = np.nonzero(mask)
        extents = [CENTRE - rows.min(), rows.max() - CENTRE, CENTRE - columns.min(), columns.max() - CENTRE]  # N, S, W, E
        self.assertLessEqual(max(extents) - min(extents), 1)
        self.assertTrue(np.array_equal(mask, mask[::-1, :]) and np.array_equal(mask, mask[:, ::-1]))  # symmetric about both axes

    def test_wind_stretches_the_fire(self) -> None:
        calm = API.length_to_breadth(run(synthetic_case(wind=0.0)), 6)
        windy = API.length_to_breadth(run(synthetic_case(wind=20.0)), 6)
        self.assertGreater(windy, 2.0)
        self.assertGreater(windy, calm + 1.0)


class WindDirectionTest(unittest.TestCase):
    """The fire runs downwind: wind FROM the west pushes it east (bearing 90)."""

    def test_head_bearing_is_the_wind_from_direction_plus_180(self) -> None:
        for wind_from, expected in {270.0: 90.0, 0.0: 180.0, 90.0: 270.0, 180.0: 0.0, 225.0: 45.0, 315.0: 135.0}.items():
            result = run(synthetic_case(wind_from=wind_from))
            for hours in (1, 3, 6):
                bearing = API.head_bearing_deg(result, hours)
                self.assertIsNotNone(bearing, msg=f"wind from {wind_from}, {hours} h")
                difference = abs((bearing - expected + 180.0) % 360.0 - 180.0)
                self.assertLess(difference, 5.0, msg=f"wind from {wind_from}: head {bearing:.1f} deg, expected {expected}")

    def test_head_bearing_is_true_north_not_grid_north(self) -> None:
        # grid north points 10 deg east of true north, so a wind from the west still drives the fire to true east (90)
        # but along grid bearing 80
        result = run(synthetic_case(wind_from=270.0, convergence=10.0))
        self.assertAlmostEqual(API.head_bearing_deg(result, 6), 90.0, delta=2.0)

    def test_head_is_longer_than_back_and_flanks(self) -> None:
        rows, columns = np.nonzero(run(synthetic_case(wind_from=270.0)).masks[6])
        self.assertGreater(columns.max() - CENTRE, 3 * (CENTRE - columns.min()))  # head far beyond the back
        self.assertGreater(columns.max() - CENTRE, 2 * (rows.max() - CENTRE))  # and well beyond the flank

    def test_more_wind_burns_more(self) -> None:
        areas = [run(synthetic_case(wind=wind), horizons=(1, 3, 6)).area_ha[3] for wind in (0.0, 10.0, 20.0)]
        self.assertEqual(areas, sorted(areas))
        self.assertLess(areas[0], areas[-1])

    def test_weather_override_replaces_the_cases_weather(self) -> None:
        case = synthetic_case(wind_from=270.0)
        result = run(case, weather_override={"wind_direction_deg": 0.0})
        self.assertAlmostEqual(API.head_bearing_deg(result, 6), 180.0, delta=5.0)
        self.assertEqual(case.weather.wind_direction_deg.unique().tolist(), [270.0], msg="the case must not be modified")

    def test_ffmc_override_reaches_the_simulator(self) -> None:
        # the simulator reads ffmc_daily unless hourly moisture is on; the override has to set that column too
        dry = run(synthetic_case(), weather_override={"ffmc": 93.0}, horizons=(1, 3))
        wet = run(synthetic_case(), weather_override={"ffmc": 80.0}, horizons=(1, 3))
        self.assertGreater(dry.area_ha[3], 2.0 * wet.area_ha[3])


class GrowthTest(unittest.TestCase):
    def test_area_never_shrinks_and_masks_are_nested(self) -> None:
        for kwargs in ({"wind": 0.0}, {"wind": 20.0}, {"wind": 10.0, "wind_from": 135.0}):
            result = run(synthetic_case(**kwargs), horizons=(1, 3, 6))
            areas = [result.area_ha[h] for h in (1, 3, 6)]
            self.assertLessEqual(areas[0], areas[1])
            self.assertLessEqual(areas[1], areas[2])
            self.assertLess(areas[0], areas[2], msg="the fire must actually grow")
            self.assertFalse((result.masks[1] & ~result.masks[3]).any())
            self.assertFalse((result.masks[3] & ~result.masks[6]).any())

    def test_masks_are_slices_of_one_arrival_grid(self) -> None:
        result = run(synthetic_case())
        for hours, mask in result.masks.items():
            np.testing.assert_array_equal(mask, result.arrival_min <= hours * 60.0)

    def test_a_shorter_run_matches_the_start_of_a_longer_one(self) -> None:
        # cutting the search off at the horizon must not change arrival times before it
        case = synthetic_case()
        short, long = run(case, horizons=(1, 3)), run(case, horizons=(1, 3, 6))
        np.testing.assert_array_equal(short.masks[3], long.masks[3])
        np.testing.assert_array_equal(short.masks[1], long.masks[1])

    def test_custom_horizons_and_area_units(self) -> None:
        result = run(synthetic_case(wind=0.0), horizons=(2.0,))
        self.assertEqual(result.horizons, (2.0,))
        self.assertAlmostEqual(result.area_ha[2.0], result.masks[2.0].sum() * 0.81)  # 90 m cell = 0.81 ha


class SlopeTest(unittest.TestCase):
    def test_fire_climbs_faster_than_it_descends(self) -> None:
        case = synthetic_case(wind=0.0)
        case.elevation = np.tile(np.arange(SIZE, dtype=np.float32) * PIXEL * 0.2, (SIZE, 1))  # rises to the east at 20%
        result = run(case)
        self.assertLess(result.arrival_min[CENTRE, CENTRE + 10], result.arrival_min[CENTRE, CENTRE - 10])
        self.assertAlmostEqual(API.head_bearing_deg(result, 6), 90.0, delta=5.0)  # drifts upslope
        self.assertGreater(API.length_to_breadth(result, 6), 1.15)  # measured 1.22
        # no slope effect across the slope
        self.assertAlmostEqual(float(result.arrival_min[CENTRE + 10, CENTRE]), float(result.arrival_min[CENTRE - 10, CENTRE]), places=3)


class BarrierTest(unittest.TestCase):
    def test_a_wide_lake_stops_the_fire_crossing(self) -> None:
        fuel = np.full((SIZE, SIZE), SPRUCE, dtype=np.int32)
        fuel[:, CENTRE + 20 : CENTRE + 22] = SIM.WATER_CODE  # a river two cells (180 m) wide, edge to edge
        result = run(synthetic_case(fuel, wind=20.0, wind_from=270.0))
        self.assertTrue(np.isfinite(result.arrival_min[CENTRE, CENTRE + 19]), msg="the fire must reach the river bank")
        self.assertFalse(result.masks[6][:, CENTRE + 20 :].any(), msg="no fire beyond the river")
        self.assertFalse(result.masks[6][fuel == SIM.WATER_CODE].any())

    def test_non_fuel_blocks_like_water(self) -> None:
        for code in (101, 105, 0):
            fuel = np.full((SIZE, SIZE), SPRUCE, dtype=np.int32)
            fuel[:, CENTRE + 20 : CENTRE + 22] = code
            result = run(synthetic_case(fuel))
            self.assertFalse(result.masks[6][:, CENTRE + 20 :].any(), msg=f"code {code}")

    def test_fire_goes_round_a_finite_lake_and_loses_time(self) -> None:
        fuel = np.full((SIZE, SIZE), SPRUCE, dtype=np.int32)
        fuel[CENTRE - 4 : CENTRE + 5, CENTRE + 15 : CENTRE + 19] = SIM.WATER_CODE
        blocked, open_ground = run(synthetic_case(fuel)), run(synthetic_case())
        behind = (CENTRE, CENTRE + 30)  # directly downwind of the lake
        self.assertTrue(np.isfinite(blocked.arrival_min[behind]))
        self.assertGreater(blocked.arrival_min[behind], open_ground.arrival_min[behind])

    def test_a_one_cell_wide_barrier_blocks_the_fire(self) -> None:
        """Was a known artefact: knight moves such as (+1, +2) stepped over a single 90 m cell.  Fixed in simulate_spread
        (a move is blocked when any cell it passes through is water or non-fuel).  Before the fix, on this grid with a
        20 km/h west wind at 6 h, 690 cells burned beyond a 1-cell wall."""
        fuel = np.full((SIZE, SIZE), SPRUCE, dtype=np.int32)
        fuel[:, CENTRE + 20] = SIM.WATER_CODE
        result = run(synthetic_case(fuel))
        self.assertTrue(np.isfinite(result.arrival_min[CENTRE, CENTRE + 19]), msg="the fire must reach the wall")
        self.assertFalse(result.masks[6][:, CENTRE + 21 :].any())
        self.assertFalse(result.masks[6][fuel == SIM.WATER_CODE].any())

    def test_a_one_cell_wall_in_every_axis_direction_blocks_like_a_two_cell_wall(self) -> None:
        for wind_from, build in (
            (270.0, lambda f: f.__setitem__((slice(None), CENTRE + 20), SIM.WATER_CODE)),
            (90.0, lambda f: f.__setitem__((slice(None), CENTRE - 20), SIM.WATER_CODE)),
            (180.0, lambda f: f.__setitem__((CENTRE - 20, slice(None)), SIM.WATER_CODE)),
            (0.0, lambda f: f.__setitem__((CENTRE + 20, slice(None)), SIM.WATER_CODE)),
        ):
            fuel = np.full((SIZE, SIZE), SPRUCE, dtype=np.int32)
            build(fuel)
            result = run(synthetic_case(fuel, wind_from=wind_from))
            burned = result.masks[6]
            rows, columns = np.indices(burned.shape)
            side = {270.0: columns > CENTRE + 20, 90.0: columns < CENTRE - 20, 180.0: rows < CENTRE - 20, 0.0: rows > CENTRE + 20}[wind_from]
            self.assertFalse((burned & side).any(), msg=f"wind from {wind_from}")

    def test_an_unobstructed_fire_is_unchanged_by_the_barrier_rule(self) -> None:
        # reference areas measured before the fix (uniform spruce, 20 km/h wind from the west, 90 m cells)
        result = run(synthetic_case())
        self.assertEqual([result.area_ha[h] for h in (1, 3, 6)], [28.35, 234.9, 935.55])

    @unittest.expectedFailure
    def test_a_diagonal_one_cell_wall_blocks_the_fire(self) -> None:
        """KNOWN SIMULATOR ARTEFACT (reported, not fixed): fire still squeezes between two water cells that touch only at
        a corner, by a diagonal (+-1, +-1) move.  Measured with a 1-cell anti-diagonal wall through (centre, centre + 20):
        616 cells burned beyond it before the knight-move fix and 567 after."""
        fuel = np.full((SIZE, SIZE), SPRUCE, dtype=np.int32)
        for row in range(SIZE):
            column = CENTRE + 20 - (row - CENTRE)
            if 0 <= column < SIZE:
                fuel[row, column] = SIM.WATER_CODE
        result = run(synthetic_case(fuel))
        rows, columns = np.indices(result.masks[6].shape)
        self.assertFalse((result.masks[6] & ((rows - CENTRE) + (columns - CENTRE) > 20)).any())


class IgnitionPointTest(unittest.TestCase):
    def test_water_or_non_fuel_ignition_does_not_spread(self) -> None:
        for code in (SIM.WATER_CODE, 101, 0):
            fuel = np.full((SIZE, SIZE), SPRUCE, dtype=np.int32)
            fuel[CENTRE, CENTRE] = code
            result = run(synthetic_case(fuel))
            self.assertFalse(result.ignition_burnable)
            self.assertEqual(sum(result.area_ha.values()), 0.0)
            self.assertFalse(any(mask.any() for mask in result.masks.values()))
            self.assertIsNone(API.head_bearing_deg(result, 6))

    def test_fuel_that_carries_no_fire_burns_only_the_start_cell(self) -> None:
        # green aspen at BUI 64 has a rate of spread of 0
        result = run(synthetic_case(np.full((SIZE, SIZE), GREEN_ASPEN, dtype=np.int32)))
        self.assertTrue(result.ignition_burnable)
        self.assertEqual(int(result.masks[6].sum()), 1)

    def test_start_cell_is_inside_every_mask_when_burnable(self) -> None:
        result = run(synthetic_case())
        self.assertTrue(all(mask[CENTRE, CENTRE] for mask in result.masks.values()))
        self.assertEqual(result.ignition_cell, (CENTRE, CENTRE))

    def test_point_may_be_given_as_lon_lat(self) -> None:
        case = synthetic_case()
        xy = centre_xy(case, 100, 120)
        lon, lat = Transformer.from_crs("EPSG:3978", "EPSG:4326", always_xy=True).transform(*xy)
        by_xy = API.run_point(case, START, ignition_xy=xy, horizons=(1,))
        by_lonlat = API.run_point(case, START, lonlat=(lon, lat), horizons=(1,))
        self.assertEqual(by_lonlat.ignition_cell, (100, 120))
        np.testing.assert_array_equal(by_xy.masks[1], by_lonlat.masks[1])

    def test_bad_inputs_raise(self) -> None:
        case = synthetic_case()
        with self.assertRaises(ValueError):
            API.run_point(case, START)  # no point
        with self.assertRaises(ValueError):
            API.run_point(case, START, ignition_xy=centre_xy(case), lonlat=(-80.0, 48.0))  # two points
        with self.assertRaises(ValueError):
            API.run_point(case, START, ignition_xy=(-5.0, 100.0))  # off the grid
        with self.assertRaises(ValueError):
            API.run_point(case, START, ignition_xy=centre_xy(case), horizons=(0,))
        with self.assertRaises(ValueError):
            API.run_point(case, START, ignition_xy=centre_xy(case), weather_override={"wind": 5})  # misspelt key

    def test_weather_that_does_not_cover_the_run_raises_instead_of_silently_stopping(self) -> None:
        case = synthetic_case()  # 12 h of weather from START
        with self.assertRaises(ValueError):
            API.run_point(case, START + pd.Timedelta(hours=8), ignition_xy=centre_xy(case))  # 6 h run needs up to hour 14
        with self.assertRaises(ValueError):
            API.run_point(case, START - pd.Timedelta(hours=1), ignition_xy=centre_xy(case))

    def test_timezone_aware_start_is_read_as_utc(self) -> None:
        case = synthetic_case()
        aware = API.run_point(case, START.tz_localize("UTC"), ignition_xy=centre_xy(case), horizons=(1,))
        naive = API.run_point(case, START, ignition_xy=centre_xy(case), horizons=(1,))
        np.testing.assert_array_equal(aware.masks[1], naive.masks[1])


class NoLeakageTest(unittest.TestCase):
    def test_final_perimeter_repair_is_never_used(self) -> None:
        # `fuel` stands for a grid repaired from the final perimeter; the API must use `fuel_as_mapped`
        case = synthetic_case()
        repaired = case.fuel.copy()
        repaired[:, CENTRE + 5 :] = SIM.WATER_CODE
        leaky = SIM.FireCase(**{**case.__dict__, "fuel": repaired})
        np.testing.assert_array_equal(run(leaky).masks[6], run(case).masks[6])

    def test_repair_setting_is_forced_off(self) -> None:
        result = run(synthetic_case(), settings=SIM.Settings(repair_burn_scar=True, ros_scale=0.8))
        self.assertFalse(result.settings.repair_burn_scar)
        self.assertEqual(result.settings.ros_scale, 0.8)  # other settings pass through
        self.assertFalse(result.uses_final_perimeter)

    def test_observed_arrival_cells_are_ignored(self) -> None:
        case = synthetic_case()
        spoiled = SIM.FireCase(**{**case.__dict__, "arrival": pd.DataFrame({"cell_x": [1], "cell_y": [1], "first_observation": [1]})})
        np.testing.assert_array_equal(run(spoiled).arrival_min, run(case).arrival_min)

    def test_caller_settings_and_case_are_not_mutated(self) -> None:
        case, settings = synthetic_case(), SIM.Settings(repair_burn_scar=True)
        weather_before = case.weather.copy()
        run(case, settings=settings, weather_override={"wind_speed_kmh": 5.0})
        self.assertTrue(settings.repair_burn_scar)
        pd.testing.assert_frame_equal(case.weather, weather_before)


class DeterminismTest(unittest.TestCase):
    def test_same_inputs_give_identical_output(self) -> None:
        a, b = run(synthetic_case(wind_from=200.0)), run(synthetic_case(wind_from=200.0))
        np.testing.assert_array_equal(a.arrival_min, b.arrival_min)
        for hours in a.masks:
            np.testing.assert_array_equal(a.masks[hours], b.masks[hours])
        self.assertEqual(a.area_ha, b.area_ha)

    def test_seed_is_recorded_and_reproducible(self) -> None:
        a, b = run(synthetic_case(), seed=42), run(synthetic_case(), seed=42)
        self.assertEqual((a.seed, b.seed), (42, 42))
        np.testing.assert_array_equal(a.arrival_min, b.arrival_min)

    def test_seed_does_not_change_the_unperturbed_run(self) -> None:
        # placeholder behaviour until T1.7 adds perturbation: the seed is carried but nothing consumes it
        np.testing.assert_array_equal(run(synthetic_case(), seed=1).arrival_min, run(synthetic_case(), seed=2).arrival_min)
        np.testing.assert_array_equal(run(synthetic_case(), seed=1).arrival_min, run(synthetic_case(), rng=np.random.default_rng(9)).arrival_min)


class GridAnisotropyTest(unittest.TestCase):
    @unittest.expectedFailure
    def test_burned_area_does_not_depend_on_the_wind_azimuth(self) -> None:
        """KNOWN SIMULATOR ARTEFACT (reported, not fixed here): identical fuel, weather and wind speed should burn the
        same area whichever way the wind blows. Measured at 6 h, 20 km/h: about 936 ha for wind from W, N, E or S and
        about 1,036 ha (+11%) for wind from the SW, so a diagonal-wind fire is bigger purely because of the grid.
        Likely cause (not confirmed): the 16-move shortest-path search lets fire mix fast directions along the
        ellipse differently on diagonals. Matters for Monte Carlo wind-direction perturbation."""
        areas = [run(synthetic_case(wind_from=wind_from)).area_ha[6] for wind_from in (270.0, 225.0)]
        self.assertAlmostEqual(areas[1] / areas[0], 1.0, delta=0.05)

    def test_axis_aligned_winds_burn_the_same_area(self) -> None:
        areas = [run(synthetic_case(wind_from=wind_from)).area_ha[6] for wind_from in (0.0, 90.0, 180.0, 270.0)]
        self.assertAlmostEqual(max(areas) / min(areas), 1.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
