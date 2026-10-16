"""Executable contract tests for the fire-spread simulator."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import fbp_spread as FBP  # noqa: E402
import simulate_spread as SIM  # noqa: E402


START = pd.Timestamp("2019-07-10 18:00")
SUMMER = 191  # day of year of START
PIXEL = 90.0


def flat_case(fuel: np.ndarray, wind: float = 20.0, wind_from: float = 270.0, ffmc: float = 90.0, bui: float = 64.0) -> SIM.FireCase:
    """A fire on level ground with the same weather every hour."""
    times = pd.date_range(START, periods=12, freq="h")
    weather = pd.DataFrame({
        "time_utc": times, "wind_speed_kmh": wind, "wind_direction_deg": wind_from, "ffmc": ffmc, "ffmc_daily": ffmc,
        "bui": bui, "day_of_year": SUMMER, "temp_c": 25.0, "rh_pct": 30.0,
    })  # fmt: skip
    return SIM.FireCase(
        fire_id="TEST", west=0.0, north=fuel.shape[0] * PIXEL, pixel=PIXEL, fuel=fuel, fuel_as_mapped=fuel,
        elevation=np.zeros(fuel.shape, dtype=np.float32), weather=weather, convergence_deg=0.0, ignition_cell=None,
        arrival=pd.DataFrame(), observation_times=pd.Series(dtype="datetime64[ns]"),
    )  # fmt: skip


def one_cell(shape: tuple[int, int], row: int, column: int) -> np.ndarray:
    burning = np.zeros(shape, dtype=bool)
    burning[row, column] = True
    return burning


class RateOfSpreadTest(unittest.TestCase):
    def test_spread_index_matches_the_daily_fire_weather_code(self) -> None:
        # Van Wagner and Pickett (1985) test day 1: FFMC 87.7 and 25 km/h wind give ISI 10.9
        self.assertAlmostEqual(FBP.spread_index(87.7, 25.0), 10.9, delta=0.06)

    def test_boreal_spruce_rate_follows_the_published_equation(self) -> None:
        # C-2 at ISI 10: 110 * (1 - exp(-0.0282 * 10)) ** 1.5, and no buildup effect at BUI 64
        self.assertAlmostEqual(FBP.rate_of_spread(2, 10.0, 64.0, SUMMER), 110.0 * (1.0 - math.exp(-0.282)) ** 1.5, places=6)
        self.assertAlmostEqual(FBP.rate_of_spread(2, 10.0, 64.0, SUMMER), 13.40, delta=0.01)

    def test_deeper_dry_fuel_spreads_faster(self) -> None:
        self.assertLess(FBP.rate_of_spread(2, 10.0, 30.0, SUMMER), FBP.rate_of_spread(2, 10.0, 64.0, SUMMER))
        self.assertLess(FBP.rate_of_spread(2, 10.0, 64.0, SUMMER), FBP.rate_of_spread(2, 10.0, 120.0, SUMMER))

    def test_water_and_non_fuel_do_not_burn(self) -> None:
        for code in (101, 102, 105, 0, 999):
            self.assertEqual(FBP.rate_of_spread(code, 20.0, 80.0, SUMMER), 0.0)

    def test_green_aspen_carries_fire_only_when_the_duff_is_very_dry(self) -> None:
        self.assertEqual(FBP.rate_of_spread(13, 10.0, 60.0, SUMMER), 0.0)
        leafless, green = FBP.rate_of_spread(13, 10.0, 80.0, 120), FBP.rate_of_spread(13, 10.0, 80.0, SUMMER)
        self.assertAlmostEqual(green, 0.2 * leafless, places=6)

    def test_mixedwood_spreads_faster_with_more_conifer(self) -> None:
        rates = [FBP.rate_of_spread(code, 10.0, 50.0, SUMMER) for code in (625, 650, 675)]
        self.assertEqual(rates, sorted(rates))
        self.assertLess(rates[-1], FBP.rate_of_spread(2, 10.0, 50.0, SUMMER) * FBP._buildup_effect("M", 50.0) / FBP._buildup_effect("C-2", 50.0))


class EllipseTest(unittest.TestCase):
    def test_fire_is_round_in_calm_air_and_long_in_wind(self) -> None:
        self.assertAlmostEqual(FBP.length_to_breadth(0.0), 1.0)
        head, back, flank = FBP.ellipse_rates(2, 90.0, 64.0, 0.0, SUMMER)
        self.assertAlmostEqual(head, back)
        self.assertAlmostEqual(head, flank)
        head, back, flank = FBP.ellipse_rates(2, 90.0, 64.0, 30.0, SUMMER)
        self.assertGreater(head, flank)
        self.assertGreater(flank, back)

    def test_directional_rates_trace_the_ellipse(self) -> None:
        head, back, flank = 12.0, 1.5, 3.0
        self.assertAlmostEqual(FBP.directional_rate(head, back, flank, 0.0), head)
        self.assertAlmostEqual(FBP.directional_rate(head, back, flank, 180.0), back)
        semi_major, offset = (head + back) / 2.0, (head - back) / 2.0
        for angle in range(0, 360, 15):
            rate = FBP.directional_rate(head, back, flank, angle)
            along, across = rate * math.cos(math.radians(angle)), rate * math.sin(math.radians(angle))
            self.assertAlmostEqual(((along - offset) / semi_major) ** 2 + (across / flank) ** 2, 1.0, places=9)


class SimulationTest(unittest.TestCase):
    def test_fire_runs_downwind_as_far_as_the_head_rate_allows(self) -> None:
        case = flat_case(np.full((61, 81), 2, dtype=np.int32))
        arrival = SIM.simulate(case, one_cell(case.shape, 30, 10), START, hours=3.0)
        burned = np.isfinite(arrival)
        head, back, _ = FBP.ellipse_rates(2, 90.0, 64.0, 20.0, SUMMER)
        rows, columns = np.nonzero(burned)
        self.assertAlmostEqual((columns.max() - 10) * PIXEL, head * 180.0, delta=PIXEL)
        self.assertAlmostEqual((10 - columns.min()) * PIXEL, back * 180.0, delta=PIXEL)
        self.assertEqual(abs(int(rows.max()) - 30), abs(int(rows.min()) - 30))  # level ground, so both flanks match
        self.assertGreater(columns.max() - 10, rows.max() - 30)  # longer than wide

    def test_wind_direction_is_where_the_wind_comes_from(self) -> None:
        for wind_from, (row_step, column_step) in {0.0: (1, 0), 90.0: (0, -1), 180.0: (-1, 0), 270.0: (0, 1)}.items():
            case = flat_case(np.full((61, 61), 2, dtype=np.int32), wind_from=wind_from)
            rows, columns = np.nonzero(np.isfinite(SIM.simulate(case, one_cell(case.shape, 30, 30), START, hours=2.0)))
            centre = np.array([rows.mean() - 30, columns.mean() - 30])
            self.assertGreater(float(centre @ np.array([row_step, column_step])), 3.0, msg=f"wind from {wind_from}")

    def test_fire_goes_around_water_and_never_into_it(self) -> None:
        fuel = np.full((41, 61), 2, dtype=np.int32)
        fuel[15:26, 30:33] = SIM.WATER_CODE  # a lake across the fire's path
        case = flat_case(fuel)
        arrival = SIM.simulate(case, one_cell(case.shape, 20, 20), START, hours=6.0)
        self.assertFalse(np.isfinite(arrival[fuel == SIM.WATER_CODE]).any())
        open_ground = SIM.simulate(flat_case(np.full(fuel.shape, 2, dtype=np.int32)), one_cell(case.shape, 20, 20), START, hours=6.0)
        self.assertTrue(np.isfinite(arrival[20, 45]))
        self.assertGreater(arrival[20, 45], open_ground[20, 45])  # the detour costs time

    def test_flank_scale_narrows_the_fire_without_slowing_its_head(self) -> None:
        case = flat_case(np.full((61, 81), 2, dtype=np.int32))
        full = np.isfinite(SIM.simulate(case, one_cell(case.shape, 30, 10), START, hours=3.0))
        narrow = np.isfinite(SIM.simulate(case, one_cell(case.shape, 30, 10), START, hours=3.0, settings=SIM.Settings(flank_scale=0.25)))
        self.assertEqual(np.nonzero(full)[1].max(), np.nonzero(narrow)[1].max())
        self.assertLess(narrow.sum(), 0.5 * full.sum())

    def test_rate_scale_slows_the_whole_fire(self) -> None:
        case = flat_case(np.full((61, 81), 2, dtype=np.int32))
        full = SIM.simulate(case, one_cell(case.shape, 30, 10), START, hours=3.0)
        half = SIM.simulate(case, one_cell(case.shape, 30, 10), START, hours=3.0, settings=SIM.Settings(ros_scale=0.5))
        reached = np.isfinite(half) & (full > 0)
        np.testing.assert_allclose(half[reached], 2.0 * full[reached], rtol=1e-4)

    def test_fire_climbs_faster_than_it_crosses_level_ground(self) -> None:
        case = flat_case(np.full((41, 41), 2, dtype=np.int32), wind=0.0)
        case.elevation = np.tile(np.arange(41, dtype=np.float32) * PIXEL * 0.3, (41, 1))  # rises to the east at 30%
        arrival = SIM.simulate(case, one_cell(case.shape, 20, 20), START, hours=8.0)
        self.assertLess(arrival[20, 25], arrival[20, 15])
        self.assertAlmostEqual(float(arrival[25, 20]), float(arrival[15, 20]), places=3)

    def test_already_burned_ground_spreads_only_from_the_active_edge(self) -> None:
        case = flat_case(np.full((41, 81), 2, dtype=np.int32), wind=0.0)
        burning = np.zeros(case.shape, dtype=bool)
        burning[18:23, 20:60] = True
        active = np.zeros(case.shape, dtype=bool)
        active[:, 55:] = True  # only the east end is still alight
        arrival = SIM.simulate(case, burning, START, hours=1.0, active=active)
        new = np.isfinite(arrival) & ~burning
        self.assertTrue(new.any())
        self.assertGreaterEqual(np.nonzero(new)[1].min(), 45)


if __name__ == "__main__":
    unittest.main()
