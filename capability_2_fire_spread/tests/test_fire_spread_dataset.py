"""Executable contract tests for the fire-spread dataset builders."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import build_spread_fuel_summary as FUEL  # noqa: E402
import build_spread_master as MASTER  # noqa: E402
import build_spread_progression as PROGRESSION  # noqa: E402
import build_spread_weather as WEATHER  # noqa: E402
import download_spread_fuel_windows as FUEL_WINDOWS  # noqa: E402
import fire_weather_index as FWI  # noqa: E402


class FireWeatherIndexTest(unittest.TestCase):
    def test_codes_reproduce_the_published_van_wagner_and_pickett_test_days(self) -> None:
        # noon temperature, humidity, wind, rain -> FFMC, DMC, DC, ISI, BUI, FWI (Van Wagner and Pickett 1985)
        days = [
            ((17.0, 42, 25, 0.0), (87.7, 8.5, 19.0, 10.9, 8.5, 10.1)),
            ((20.0, 21, 25, 2.4), (86.2, 10.4, 23.6, 8.8, 10.4, 9.3)),
            ((8.5, 40, 17, 0.0), (87.0, 11.8, 26.1, 6.5, 11.7, 7.6)),
            ((6.5, 25, 6, 0.0), (88.8, 13.2, 28.2, 4.9, 13.1, 6.2)),
            ((13.0, 34, 24, 0.0), (89.1, 15.4, 31.5, 12.6, 15.3, 14.8)),
        ]
        previous = dict(FWI.STARTUP)
        for (temp, rh, wind, rain), expected in days:
            previous = FWI.daily_codes(temp, rh, wind, rain, 4, previous)
            for name, value in zip(("ffmc", "dmc", "dc", "isi", "bui", "fwi"), expected):
                self.assertAlmostEqual(previous[name], value, delta=0.06, msg=name)

    def test_noon_codes_use_the_rain_of_the_preceding_24_hours(self) -> None:
        times = pd.date_range("2019-06-01 00:00", periods=72, freq="h")
        hourly = pd.DataFrame({"time_local": times, "temp_c": 20.0, "rh_pct": 40.0, "wind_speed_kmh": 10.0, "wind_direction_deg": 270.0, "precip_mm": 0.0})
        hourly.loc[hourly.time_local == "2019-06-02 13:00", "precip_mm"] = 9.0  # after noon on day 2: counts toward day 3
        daily = WEATHER.daily_fire_weather(hourly)
        self.assertEqual(daily.date.tolist(), ["2019-06-02", "2019-06-03"])  # day 1 has no full 24 hours before noon
        self.assertEqual(daily.rain_24h_mm.tolist(), [0.0, 9.0])
        self.assertLess(daily.ffmc.iloc[1], daily.ffmc.iloc[0])


class SpreadGeometryTest(unittest.TestCase):
    def test_growth_is_measured_from_the_nearest_earlier_cell(self) -> None:
        earlier = np.array([[0.0, 0.0], [375.0, 0.0]])
        new = np.array([[750.0, 0.0], [1125.0, 0.0]])  # two cells further east
        geometry = PROGRESSION.growth_geometry(new, earlier)
        self.assertAlmostEqual(geometry["grid_bearing"], 90.0)
        self.assertAlmostEqual(geometry["grid_head_bearing"], 90.0)
        self.assertAlmostEqual(geometry["distance_max_m"], 750.0)
        self.assertAlmostEqual(geometry["distance_mean_m"], 562.5)
        self.assertAlmostEqual(geometry["directionality"], 1.0)

    def test_wind_components_follow_the_blowing_from_convention(self) -> None:
        u, v = WEATHER.wind_components(np.array([10.0, 10.0, 10.0]), np.array([270.0, 360.0, 90.0]))
        np.testing.assert_allclose(u, [10.0, 0.0, -10.0], atol=1e-9)
        np.testing.assert_allclose(v, [0.0, -10.0, 0.0], atol=1e-9)
        np.testing.assert_allclose(MASTER.direction_from(u, v), [270.0, 0.0, 90.0], atol=1e-9)
        self.assertTrue(np.isnan(MASTER.direction_from(np.array([0.0]), np.array([0.0]))[0]))
        np.testing.assert_allclose(MASTER.angle_between(np.array([350.0, 90.0]), np.array([10.0, 270.0])), [20.0, 180.0])

    def test_interval_weather_covers_hours_after_the_start_through_the_end(self) -> None:
        times = pd.date_range("2019-07-01 00:00", periods=6, freq="h")
        hourly = pd.DataFrame({"time_utc": times, "wind_speed_kmh": [5, 10, 20, 30, 40, 50], "wind_u_kmh": [5, 10, 20, 30, 40, 50], "wind_v_kmh": 0.0, "temp_c": 20.0, "rh_pct": [50, 40, 30, 20, 10, 5], "precip_mm": 1.0})
        values = MASTER.interval_weather(hourly, pd.Timestamp("2019-07-01 01:00"), pd.Timestamp("2019-07-01 03:30"))
        self.assertEqual(values["weather_hours"], 2)  # 02:00 and 03:00; 01:00 belongs to the earlier interval
        self.assertAlmostEqual(values["wind_speed_mean_kmh"], 25.0)
        self.assertAlmostEqual(values["wind_direction_deg"], 270.0)
        self.assertAlmostEqual(values["wind_steadiness"], 1.0)
        self.assertEqual(values["rh_min_pct"], 20)
        self.assertEqual(MASTER.interval_weather(hourly, pd.Timestamp("2019-07-02"), pd.Timestamp("2019-07-03"))["weather_hours"], 0)


class FuelAndTerrainTest(unittest.TestCase):
    def test_fuel_summary_gives_shares_and_percent_conifer(self) -> None:
        codes = np.array([2] * 50 + [650] * 20 + [13] * 10 + [102] * 15 + [101] * 5)
        summary = FUEL.summarise(codes, "fuel_around")
        self.assertEqual(summary["fuel_around_cells"], 100)
        self.assertEqual(summary["fuel_around_conifer_share"], 0.5)
        self.assertEqual(summary["fuel_around_mixedwood_share"], 0.2)
        self.assertEqual(summary["fuel_around_water_share"], 0.15)
        self.assertEqual(summary["fuel_around_dominant_fuel_type"], "C-2")
        # burnable cells: 50 at 100% conifer, 20 at 50%, 10 at 0%
        self.assertAlmostEqual(summary["fuel_around_percent_conifer"], 75.0)

    def test_fuel_window_is_snapped_outward_on_the_source_lattice(self) -> None:
        window = FUEL_WINDOWS.window_for((1_000_010.0, 200_010.0, 1_000_500.0, 200_400.0))
        self.assertLessEqual(window["west"], 1_000_010.0 - FUEL_WINDOWS.MARGIN_M)
        self.assertGreaterEqual(window["east"], 1_000_500.0 + FUEL_WINDOWS.MARGIN_M)
        self.assertLessEqual(window["south"], 200_010.0 - FUEL_WINDOWS.MARGIN_M)
        self.assertGreaterEqual(window["north"], 200_400.0 + FUEL_WINDOWS.MARGIN_M)
        self.assertAlmostEqual((window["west"] - FUEL_WINDOWS.SOURCE_WEST) % FUEL_WINDOWS.SOURCE_PIXEL_M, 0.0)
        self.assertAlmostEqual((FUEL_WINDOWS.SOURCE_NORTH - window["north"]) % FUEL_WINDOWS.SOURCE_PIXEL_M, 0.0, places=6)
        self.assertEqual(window["columns"] * FUEL_WINDOWS.WINDOW_PIXEL_M, window["east"] - window["west"])

    def test_slope_and_aspect_of_a_plane_rising_to_the_north(self) -> None:
        try:
            import build_spread_terrain_summary as TERRAIN
        except ImportError:
            self.skipTest("rasterio is not installed")
        rows = np.arange(6, dtype=float)[:, None] * np.ones((1, 5))
        elevation = 100.0 - 9.0 * rows  # row 0 is the north edge; ground drops 9 m per 90 m going south
        slope_pct, aspect = TERRAIN.slope_and_aspect(elevation, 90.0)
        np.testing.assert_allclose(slope_pct, 10.0)
        np.testing.assert_allclose(aspect, 180.0)  # the ground faces south


if __name__ == "__main__":
    unittest.main()
