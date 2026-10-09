"""Canadian Forest Fire Weather Index System equations (Van Wagner 1987).

Daily codes from local-noon temperature (°C), relative humidity (%), 10 m wind
speed (km/h) and rain in the preceding 24 hours (mm).  Each day's codes depend
on the previous day's, so a season is computed in date order from the standard
start-up values.  Pure Python and deterministic, so the live feed can call the
same functions as the historical build.
"""

from __future__ import annotations

import math


STARTUP = {"ffmc": 85.0, "dmc": 6.0, "dc": 15.0}
# Effective day length by month for latitudes north of 30°N.
DMC_DAY_LENGTH = [6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0]
DC_DAY_LENGTH = [-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6]


def fine_fuel_moisture(ffmc: float) -> float:
    """Fine fuel moisture content (%) implied by an FFMC value."""
    return 147.2 * (101.0 - ffmc) / (59.5 + ffmc)


def ffmc(temp: float, rh: float, wind: float, rain: float, previous: float) -> float:
    rh = min(rh, 100.0)
    moisture = fine_fuel_moisture(previous)
    if rain > 0.5:
        effective = rain - 0.5
        wetting = 42.5 * effective * math.exp(-100.0 / (251.0 - moisture)) * (1.0 - math.exp(-6.93 / effective))
        if moisture > 150.0:
            wetting += 0.0015 * (moisture - 150.0) ** 2 * math.sqrt(effective)
        moisture = min(moisture + wetting, 250.0)
    drying_equilibrium = 0.942 * rh**0.679 + 11.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
    if moisture > drying_equilibrium:
        rate = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (1.0 - (rh / 100.0) ** 8)
        moisture = drying_equilibrium + (moisture - drying_equilibrium) * 10.0 ** (-rate * 0.581 * math.exp(0.0365 * temp))
    else:
        wetting_equilibrium = 0.618 * rh**0.753 + 10.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
        if moisture < wetting_equilibrium:
            rate = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (1.0 - ((100.0 - rh) / 100.0) ** 8)
            moisture = wetting_equilibrium - (wetting_equilibrium - moisture) * 10.0 ** (-rate * 0.581 * math.exp(0.0365 * temp))
    return max(0.0, min(59.5 * (250.0 - moisture) / (147.2 + moisture), 101.0))


def dmc(temp: float, rh: float, rain: float, previous: float, month: int) -> float:
    rh = min(rh, 100.0)
    temp = max(temp, -1.1)
    drying = 1.894 * (temp + 1.1) * (100.0 - rh) * DMC_DAY_LENGTH[month - 1] * 1e-4
    if rain > 1.5:
        effective = 0.92 * rain - 1.27
        moisture = 20.0 + math.exp(5.6348 - previous / 43.43)
        if previous <= 33.0:
            slope = 100.0 / (0.5 + 0.3 * previous)
        elif previous <= 65.0:
            slope = 14.0 - 1.3 * math.log(previous)
        else:
            slope = 6.2 * math.log(previous) - 17.2
        moisture += 1000.0 * effective / (48.77 + slope * effective)
        previous = max(244.72 - 43.43 * math.log(moisture - 20.0), 0.0)
    return previous + drying


def dc(temp: float, rain: float, previous: float, month: int) -> float:
    temp = max(temp, -2.8)
    drying = max((0.36 * (temp + 2.8) + DC_DAY_LENGTH[month - 1]) / 2.0, 0.0)
    if rain > 2.8:
        effective = 0.83 * rain - 1.27
        moisture = 800.0 * math.exp(-previous / 400.0) + 3.937 * effective
        previous = max(400.0 * math.log(800.0 / moisture), 0.0)
    return previous + drying


def isi(wind: float, ffmc_value: float) -> float:
    moisture = fine_fuel_moisture(ffmc_value)
    fuel = 91.9 * math.exp(-0.1386 * moisture) * (1.0 + moisture**5.31 / 4.93e7)
    return 0.208 * math.exp(0.05039 * wind) * fuel


def bui(dmc_value: float, dc_value: float) -> float:
    if dmc_value == 0 and dc_value == 0:
        return 0.0
    if dmc_value <= 0.4 * dc_value:
        return max(0.8 * dmc_value * dc_value / (dmc_value + 0.4 * dc_value), 0.0)
    return max(dmc_value - (1.0 - 0.8 * dc_value / (dmc_value + 0.4 * dc_value)) * (0.92 + (0.0114 * dmc_value) ** 1.7), 0.0)


def fwi(isi_value: float, bui_value: float) -> float:
    duff = 0.626 * bui_value**0.809 + 2.0 if bui_value <= 80.0 else 1000.0 / (25.0 + 108.64 * math.exp(-0.023 * bui_value))
    intensity = 0.1 * isi_value * duff
    return math.exp(2.72 * (0.434 * math.log(intensity)) ** 0.647) if intensity > 1.0 else intensity


def daily_codes(temp: float, rh: float, wind: float, rain: float, month: int, previous: dict[str, float]) -> dict[str, float]:
    """One day's six codes from that day's noon weather and the previous day's FFMC, DMC and DC."""
    ffmc_value = ffmc(temp, rh, wind, rain, previous["ffmc"])
    dmc_value = dmc(temp, rh, rain, previous["dmc"], month)
    dc_value = dc(temp, rain, previous["dc"], month)
    isi_value = isi(wind, ffmc_value)
    bui_value = bui(dmc_value, dc_value)
    return {"ffmc": ffmc_value, "dmc": dmc_value, "dc": dc_value, "isi": isi_value, "bui": bui_value, "fwi": fwi(isi_value, bui_value)}
