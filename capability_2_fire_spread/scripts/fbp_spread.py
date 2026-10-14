"""Rate-of-spread equations of the Canadian Forest Fire Behavior Prediction System.

Head, back and flank spread rates (m/min) for the fuel types on the Ontario
fuel map, from the Initial Spread Index, the Buildup Index and 10 m wind.
Equations and constants follow Forestry Canada Fire Danger Group (1992),
ST-X-3, and Wotton, Alexander and Taylor (2009), GLC-X-10; the constants were
checked against the `cffdrs` R package.  Crown-fire and foliar-moisture effects,
which only change the rate of spread for C-6, are not modelled.

Pure Python so the live system can call the same functions as the replay.
"""

from __future__ import annotations

import math

from fire_weather_index import fine_fuel_moisture


# fuel type -> (a, b, c) of RSI = a * (1 - exp(-b * ISI)) ** c, and (q, BUI0) of the buildup effect
SPREAD = {
    "C-1": (90.0, 0.0649, 4.5),
    "C-2": (110.0, 0.0282, 1.5),
    "C-3": (110.0, 0.0444, 3.0),
    "C-4": (110.0, 0.0293, 1.5),
    "C-5": (30.0, 0.0697, 4.0),
    "C-7": (45.0, 0.0305, 2.0),
    "D-1": (30.0, 0.0232, 1.6),
    "O-1a": (190.0, 0.0310, 1.4),
}
BUILDUP = {
    "C-1": (0.90, 72.0), "C-2": (0.70, 64.0), "C-3": (0.75, 62.0), "C-4": (0.80, 66.0), "C-5": (0.80, 56.0),
    "C-7": (0.85, 106.0), "D-1": (0.90, 32.0), "M": (0.80, 50.0), "O-1a": (1.0, 1.0),
}  # fmt: skip
# Fuel map code -> (kind, fuel type or percent conifer).  Anything not listed does not burn.
MAP_CODES = {
    1: ("conifer", "C-1"), 2: ("conifer", "C-2"), 3: ("conifer", "C-3"), 4: ("conifer", "C-4"), 5: ("conifer", "C-5"),
    7: ("conifer", "C-7"), 11: ("leafless_aspen", None), 13: ("aspen", None), 31: ("grass", None),
    415: ("leafless_mixedwood", 15.0), 625: ("mixedwood", 25.0), 650: ("mixedwood", 50.0), 675: ("mixedwood", 75.0),
}  # fmt: skip
GREEN_SEASON = (152, 258)  # day of year: leaves out about 1 June, falling about 15 September
D2_MINIMUM_BUI = 70.0


def hourly_ffmc(temp: float, rh: float, wind: float, rain: float, previous: float) -> float:
    """Fine Fuel Moisture Code one hour on (Van Wagner 1977 hourly version)."""
    rh = min(rh, 100.0)
    moisture = fine_fuel_moisture(previous)
    if rain > 0:
        wetting = 42.5 * rain * math.exp(-100.0 / (251.0 - moisture)) * (1.0 - math.exp(-6.93 / rain))
        if moisture > 150.0:
            wetting += 0.0015 * (moisture - 150.0) ** 2 * math.sqrt(rain)
        moisture = min(moisture + wetting, 250.0)
    drying_equilibrium = 0.942 * rh**0.679 + 11.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
    wetting_equilibrium = 0.618 * rh**0.753 + 10.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
    if moisture > drying_equilibrium:
        rate = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (1.0 - (rh / 100.0) ** 8)
        moisture = drying_equilibrium + (moisture - drying_equilibrium) * 10.0 ** (-rate * 0.0579 * math.exp(0.0365 * temp))
    elif moisture < wetting_equilibrium:
        rate = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (1.0 - ((100.0 - rh) / 100.0) ** 8)
        moisture = wetting_equilibrium - (wetting_equilibrium - moisture) * 10.0 ** (-rate * 0.0579 * math.exp(0.0365 * temp))
    return max(0.0, min(59.5 * (250.0 - moisture) / (147.2 + moisture), 101.0))


def spread_index(ffmc: float, wind: float, backing: bool = False) -> float:
    """Initial Spread Index with the wind (head fire) or against it (back fire)."""
    moisture = fine_fuel_moisture(ffmc)
    fuel = 91.9 * math.exp(-0.1386 * moisture) * (1.0 + moisture**5.31 / 4.93e7)
    if backing:
        wind_effect = math.exp(-0.05039 * wind)
    elif wind <= 40.0:
        wind_effect = math.exp(0.05039 * wind)
    else:
        wind_effect = 12.0 * (1.0 - math.exp(-0.0818 * (wind - 28.0)))
    return 0.208 * wind_effect * fuel


def _basic(fuel_type: str, isi: float) -> float:
    a, b, c = SPREAD[fuel_type]
    return a * (1.0 - math.exp(-b * isi)) ** c


def _buildup_effect(key: str, bui: float) -> float:
    q, bui0 = BUILDUP[key]
    return math.exp(50.0 * math.log(q) * (1.0 / bui - 1.0 / bui0)) if bui > 0 else 1.0


def grass_curing_factor(day_of_year: int) -> float:
    """Assumed share of dead grass: cured in spring, green in summer, curing again in autumn."""
    cured = 90.0 if day_of_year < GREEN_SEASON[0] else 50.0 if day_of_year <= GREEN_SEASON[1] else 75.0
    return 0.005 * (math.exp(0.061 * cured) - 1.0) if cured < 58.8 else 0.176 + 0.02 * (cured - 58.8)


def rate_of_spread(code: int, isi: float, bui: float, day_of_year: int) -> float:
    """Equilibrium rate of spread (m/min) for one fuel-map code; 0 for water and non-fuel."""
    entry = MAP_CODES.get(code)
    if entry is None:
        return 0.0
    kind, value = entry
    green = GREEN_SEASON[0] <= day_of_year <= GREEN_SEASON[1]
    if kind == "conifer":
        return _basic(value, isi) * _buildup_effect(value, bui)
    if kind == "grass":
        return _basic("O-1a", isi) * grass_curing_factor(day_of_year)
    aspen = _basic("D-1", isi)
    if kind == "leafless_aspen" or (kind == "aspen" and not green):
        return aspen * _buildup_effect("D-1", bui)
    if kind == "aspen":  # green aspen (D-2) only carries fire when the duff is very dry
        return 0.2 * aspen * _buildup_effect("D-1", bui) if bui >= D2_MINIMUM_BUI else 0.0
    conifer_share = value / 100.0
    aspen_weight = 0.2 if (kind == "mixedwood" and green) else 1.0  # M-2 when green, M-1 when leafless
    return (conifer_share * _basic("C-2", isi) + aspen_weight * (1.0 - conifer_share) * aspen) * _buildup_effect("M", bui)


def length_to_breadth(wind: float) -> float:
    """Length-to-breadth ratio of the fire ellipse in forest fuels."""
    return 1.0 + 8.729 * (1.0 - math.exp(-0.030 * wind)) ** 2.155


def directional_rate(head: float, back: float, flank: float, angle_from_head_deg: float) -> float:
    """Spread rate in a direction, for an ellipse set by its head, back and flank rates.

    The fire's origin sits on the long axis so that the ellipse reaches `head`
    ahead of it and `back` behind it; `flank` is the half-width.
    """
    if head <= 0:
        return 0.0
    semi_major, offset = (head + back) / 2.0, (head - back) / 2.0
    cos_t, sin_t = math.cos(math.radians(angle_from_head_deg)), math.sin(math.radians(angle_from_head_deg))
    denominator = flank**2 * cos_t**2 + semi_major**2 * sin_t**2
    root = flank**2 * cos_t**2 + (semi_major**2 - offset**2) * sin_t**2
    return (offset * flank**2 * cos_t + semi_major * flank * math.sqrt(max(root, 0.0))) / denominator


def ellipse_rates(code: int, ffmc: float, bui: float, wind: float, day_of_year: int) -> tuple[float, float, float]:
    """(head, back, flank) rates of spread in m/min for one fuel-map code."""
    head = rate_of_spread(code, spread_index(ffmc, wind), bui, day_of_year)
    back = rate_of_spread(code, spread_index(ffmc, wind, backing=True), bui, day_of_year)
    return head, back, (head + back) / (2.0 * length_to_breadth(wind))
