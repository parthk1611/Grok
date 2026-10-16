#!/usr/bin/env python3
"""Exploratory charts of the fire-spread dataset.

Draws ten figures from the fire list and the master table into `docs/eda/`
and writes the numbers behind them to `docs/eda/eda_numbers.json`.  The
explanations that go with the figures are in `docs/fire_spread_eda.md`.
Needs numpy, pandas and matplotlib.
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import patheffects
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as ShapePath
from matplotlib.transforms import IdentityTransform

from spread_common import PROCESSED, PROJECT_ROOT, action_log


OUT = PROJECT_ROOT / "docs/eda"
DPI = 200
PX = DPI / 100  # device pixels per design pixel; sizes below are in design pixels
SURFACE, INK, SECONDARY, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BLUE, ORANGE, QUIET = "#2a78d6", "#eb6834", "#c3c2b7"  # two series colours and a grey for what is set aside
CELL_HA = 375.0 * 375.0 / 10_000  # one satellite cell
TOWNS = {"Kenora": (-94.49, 49.77), "Red Lake": (-93.83, 51.02), "Sioux Lookout": (-91.92, 50.10), "Thunder Bay": (-89.25, 48.38), "Timmins": (-81.33, 48.47), "Moosonee": (-80.65, 51.27), "Sudbury": (-81.00, 46.49)}  # fmt: skip

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 9.5, "figure.dpi": DPI, "savefig.dpi": DPI,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": SECONDARY, "axes.labelsize": 9.5,
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": SECONDARY, "ytick.labelcolor": SECONDARY,
    "xtick.major.size": 0, "ytick.major.size": 0, "xtick.minor.size": 0, "ytick.minor.size": 0,
    "grid.color": GRID, "grid.linewidth": 0.8, "legend.frameon": False, "legend.fontsize": 9.5,
})  # fmt: skip


def figure(title: str, subtitle: str, panels: int = 1, legend: bool = False, left: float = 0.085, bottom: float = 0.14, height: float = 4.6):
    """A figure with a title block and one or two plots laid out at fixed positions, so bar shapes can be drawn in pixels."""
    fig, axes = plt.subplots(1, panels, figsize=(8, height))
    fig.subplots_adjust(left=left, right=0.975, top=0.75 if legend else 0.80, bottom=bottom, wspace=0.28)
    fig.text(0.03, 0.945, title, fontsize=13, fontweight="semibold", color=INK, va="center")
    fig.text(0.03, 0.885, subtitle, fontsize=9.5, color=SECONDARY, va="center")
    for ax in np.atleast_1d(axes):
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.set_axisbelow(True)
    return fig, axes


def save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name)
    plt.close(fig)


def bar(ax, position: float, base: float, tip: float, thickness: float, colour: str, horizontal: bool = False, rounded: bool = True, trim: tuple[float, float] = (0, 0)) -> None:
    """One bar with a rounded data end and a square foot.  `trim` shortens the foot and tip by that many pixels, to leave a gap in a stack."""
    if tip == base:
        return
    if horizontal:
        (a0, centre), (a1, _) = ax.transData.transform([(base, position), (tip, position)])
    else:
        (centre, a0), (_, a1) = ax.transData.transform([(position, base), (position, tip)])
    sign = 1.0 if a1 >= a0 else -1.0
    a0, a1 = a0 + sign * trim[0] * PX, a1 - sign * trim[1] * PX
    if (a1 - a0) * sign <= 0:
        return
    c0, c1 = centre - thickness * PX / 2, centre + thickness * PX / 2
    r = min(4 * PX, abs(a1 - a0), (c1 - c0) / 2) if rounded else 0.0
    points = [(a0, c0), (a1 - sign * r, c0), (a1, c0), (a1, c0 + r), (a1, c1 - r), (a1, c1), (a1 - sign * r, c1), (a0, c1), (a0, c0)]
    codes = [ShapePath.MOVETO, ShapePath.LINETO, ShapePath.CURVE3, ShapePath.CURVE3, ShapePath.LINETO, ShapePath.CURVE3, ShapePath.CURVE3, ShapePath.LINETO, ShapePath.CLOSEPOLY]
    vertices = points if horizontal else [(c, a) for a, c in points]
    ax.add_patch(PathPatch(ShapePath(vertices, codes), transform=IdentityTransform(), facecolor=colour, edgecolor="none", zorder=3))


def swatches(ax, entries: list[tuple[str, str]], line: bool = False) -> None:
    handles = [Line2D([], [], color=colour, lw=1.6, marker="o", markersize=5) if line else Patch(facecolor=colour, edgecolor="none") for _, colour in entries]
    ax.legend(handles, [label for label, _ in entries], loc="lower left", bbox_to_anchor=(-0.01, 1.02), ncol=len(entries), handlelength=1.0, handleheight=1.0, columnspacing=1.6, labelcolor=SECONDARY, borderaxespad=0)


def fires_per_year(fires: pd.DataFrame, numbers: dict) -> None:
    years = np.arange(int(fires.fire_year.min()), int(fires.fire_year.max()) + 1)
    tracked = fires[fires.trackable].groupby("fire_year").size().reindex(years, fill_value=0)
    untracked = fires[~fires.trackable].groupby("fire_year").size().reindex(years, fill_value=0)
    fig, ax = figure("Fires of 40 ha or more, by year", "Ontario, 2012 to 2025. Blue fires were caught growing at three or more satellite observations.", legend=True)
    ax.set_xlim(years[0] - 0.7, years[-1] + 0.7)
    ax.set_ylim(0, 140)
    ax.set_xticks(years)
    ax.yaxis.grid(True)
    ax.set_ylabel("Fires")
    for year in years:
        bar(ax, year, 0, tracked[year], 24, BLUE, rounded=untracked[year] == 0, trim=(0, 1 if untracked[year] else 0))
        bar(ax, year, tracked[year], tracked[year] + untracked[year], 24, QUIET, trim=(1 if tracked[year] else 0, 0))
    swatches(ax, [("Trackable: three or more growth steps seen", BLUE), ("Fewer growth steps seen, or none archived yet (2025)", QUIET)])
    save(fig, "01_fires_per_year.png")
    numbers["fires_per_year"] = {int(year): {"tracked": int(tracked[year]), "not_tracked": int(untracked[year])} for year in years}
    numbers["fires"] = {"all": int(len(fires)), "tracked": int(fires.trackable.sum())}


def fire_sizes(fires: pd.DataFrame, numbers: dict) -> None:
    order = ["40-200 ha", "200-1,000 ha", "1,000-10,000 ha", "10,000 ha or more"]
    counts = fires.size_class.value_counts().reindex(order)
    area = fires.groupby("size_class").reported_size_ha.sum().reindex(order)
    share = 100 * area / area.sum()
    fig, axes = figure("A few very large fires account for most of the area burned", "Number of fires in each size class, and each class's share of all the area burned.", panels=2, left=0.17)
    for ax, values, label, text in ((axes[0], counts, "Fires", "{:,.0f}"), (axes[1], share, "Share of area burned (%)", "{:.0f}%")):
        ax.set_ylim(-0.6, len(order) - 0.4)
        ax.set_xlim(0, values.max() * 1.18)
        ax.set_yticks(range(len(order)), order if ax is axes[0] else [""] * len(order))
        ax.xaxis.grid(True)
        ax.spines["bottom"].set_visible(False)
        ax.set_xlabel(label)
        for row, value in enumerate(values):
            bar(ax, row, 0, value, 22, BLUE, horizontal=True)
            ax.annotate(text.format(value), (value, row), xytext=(5, 0), textcoords="offset points", va="center", color=SECONDARY)
    save(fig, "02_fire_sizes.png")
    numbers["size_classes"] = {name: {"fires": int(counts[name]), "share_of_area_pct": round(float(share[name]), 1)} for name in order}
    numbers["size_ha"] = {"median": float(fires.reported_size_ha.median()), "largest": float(fires.reported_size_ha.max()), "total": float(fires.reported_size_ha.sum())}


def fire_locations(fires: pd.DataFrame, numbers: dict) -> None:
    fig, ax = figure("Where the fires are", "Each circle is one fire, placed at its centre and sized by the area it burned. Most are in the northwest.", legend=True, left=0.07, height=5.6)
    ordered = fires.sort_values("reported_size_ha", ascending=False)
    scale = lambda hectares: 6 + 0.9 * np.sqrt(hectares)  # noqa: E731  marker area, so circle area follows burned area
    ax.scatter(ordered.centroid_longitude, ordered.centroid_latitude, s=scale(ordered.reported_size_ha), color=BLUE, alpha=0.55, edgecolor=SURFACE, linewidth=0.8, zorder=3)
    halo = [patheffects.withStroke(linewidth=2.4, foreground=SURFACE)]
    for name, (longitude, latitude) in TOWNS.items():
        ax.scatter([longitude], [latitude], s=14, marker="s", color=INK, edgecolor=SURFACE, linewidth=0.8, zorder=5)
        ax.annotate(name, (longitude, latitude), xytext=(5, -3), textcoords="offset points", fontsize=8.5, color=INK, zorder=5, path_effects=halo)
    ax.set_aspect(1 / np.cos(np.radians(50.5)))
    ax.set_xlim(-96, -76.5)
    ax.set_ylim(44.8, 56.8)
    ax.set_xticks([-95, -90, -85, -80], ["95°W", "90°W", "85°W", "80°W"])
    ax.set_yticks([46, 48, 50, 52, 54, 56], ["46°N", "48°N", "50°N", "52°N", "54°N", "56°N"])
    ax.grid(True)
    ax.spines["bottom"].set_visible(False)
    handles = [plt.scatter([], [], s=scale(size), color=BLUE, alpha=0.55, edgecolor=SURFACE, linewidth=0.8) for size in (100, 10_000, 100_000)]
    ax.legend(handles, ["100 ha", "10,000 ha", "100,000 ha"], loc="lower left", bbox_to_anchor=(-0.01, 1.02), ncol=3, columnspacing=2.4, handletextpad=1.2, labelcolor=SECONDARY, borderaxespad=0)
    save(fig, "03_fire_locations.png")
    numbers["districts"] = {code: int(count) for code, count in fires.district_code.value_counts().head(6).items()}


def season_and_cause(fires: pd.DataFrame, numbers: dict) -> None:
    month = pd.to_datetime(fires.start_date).dt.month
    months, names = list(range(4, 11)), ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"]
    lightning = month[fires.cause_group == "lightning"].value_counts().reindex(months, fill_value=0)
    human = month[fires.cause_group == "human"].value_counts().reindex(months, fill_value=0)
    fig, ax = figure("When the fires start, and what starts them", "Fires by the month they started. Three fires of unknown cause are left out.", legend=True)
    ax.set_xlim(3.4, 10.6)
    ax.set_ylim(0, 400)
    ax.set_xticks(months, names)
    ax.yaxis.grid(True)
    ax.set_ylabel("Fires")
    for m in months:
        bar(ax, m, 0, lightning[m], 24, BLUE, rounded=human[m] == 0, trim=(0, 1 if human[m] else 0))
        bar(ax, m, lightning[m], lightning[m] + human[m], 24, ORANGE, trim=(1 if lightning[m] else 0, 0))
    swatches(ax, [("Lightning", BLUE), ("Human", ORANGE)])
    save(fig, "04_season_and_cause.png")
    numbers["start_month"] = {name: {"lightning": int(lightning[m]), "human": int(human[m])} for m, name in zip(months, names)}
    numbers["cause"] = {key: int(value) for key, value in fires.cause_group.value_counts().items()}


def satellite_record(fires: pd.DataFrame, master: pd.DataFrame, numbers: dict) -> None:
    gaps = master.hours_since_previous_observation.dropna()
    seen = fires.loc[fires.observations > 0, "detected_share_of_perimeter"].dropna()
    fig, axes = figure("What the satellite record looks like", "Left: hours between one look at a fire and the next. Right: how much of each fire's final area the satellites saw burning.", panels=2)
    edges = np.arange(0, 37, 1.0)
    share = 100 * np.histogram(gaps, edges)[0] / len(gaps)
    ax = axes[0]
    ax.set_xlim(-0.5, 36.5)
    ax.set_ylim(0, np.ceil(share.max() / 5) * 5)
    ax.set_xticks([0, 6, 12, 18, 24, 30, 36])
    ax.yaxis.grid(True)
    ax.set_xlabel("Hours since the previous observation")
    ax.set_ylabel("Share of observations (%)")
    for left, value in zip(edges[:-1], share):
        bar(ax, left + 0.5, 0, value, 6, BLUE)
    edges = np.arange(0, 2.01, 0.1)
    counts = np.histogram(seen.clip(upper=1.999), edges)[0]
    ax = axes[1]
    ax.set_xlim(-0.05, 2.05)
    ax.set_xticks([0, 0.5, 1.0, 1.5, 1.95], ["0", "0.5", "1.0", "1.5", "2.0+"])
    ax.set_ylim(0, np.ceil(counts.max() / 20) * 20)
    ax.yaxis.grid(True)
    ax.set_xlabel("Satellite cells' area as a share of the final perimeter")
    ax.set_ylabel("Fires")
    for left, value in zip(edges[:-1], counts):
        bar(ax, left + 0.05, 0, value, 11, BLUE)
    ax.axvline(seen.median(), color=SECONDARY, linewidth=0.9, zorder=2)
    ax.annotate(f"median {seen.median():.2f}", (seen.median(), ax.get_ylim()[1]), xytext=(5, -9), textcoords="offset points", color=SECONDARY)
    save(fig, "05_satellite_record.png")
    numbers["hours_between_observations"] = {"median": round(float(gaps.median()), 1), "share_under_3h_pct": round(float(100 * (gaps < 3).mean()), 1), "share_6_to_30h_pct": round(float(100 * gaps.between(6, 30).mean()), 1), "share_over_36h_pct": round(float(100 * (gaps > 36).mean()), 1)}
    numbers["detected_share_of_perimeter"] = {"fires": int(len(seen)), "median": round(float(seen.median()), 2), "share_of_fires_over_2": round(float(100 * (seen >= 2).mean()), 1)}


def growth_per_step(master: pd.DataFrame, numbers: dict) -> None:
    grew = master[master.new_cells > 0]
    cells = np.array([1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1600, 6000])  # each range runs from one count up to just below the next
    centres = np.sqrt(cells[:-1] * (cells[1:] - 1)) * CELL_HA
    fig, ax = figure("How much a fire grows between two looks", "Area first seen burning at an observation, for the observations that showed any growth. Each point is a range of sizes.", legend=True)
    fig.subplots_adjust(top=0.70)  # room for a two-line legend
    ax.set_xscale("log")
    ax.set_xlim(10, 40_000)
    ax.set_xticks([14, 50, 200, 1_000, 5_000, 20_000], ["14", "50", "200", "1,000", "5,000", "20,000"])
    ax.set_ylim(0, 30)
    ax.set_yticks([0, 10, 20, 30])
    ax.yaxis.grid(True)
    ax.set_xlabel("Newly burning area (ha, log scale; one satellite cell is 14 ha)")
    ax.set_ylabel("Share of observations (%)")
    summary, series = {}, (("Seen at an afternoon pass: burned overnight and in the morning", "D", BLUE), ("Seen at a night pass: burned in the afternoon and evening", "N", ORANGE))
    for label, code, colour in series:
        rows = grew[grew.day_or_night == code]
        ax.plot(centres, 100 * np.histogram(rows.new_cells, cells - 0.5)[0] / len(rows), color=colour, linewidth=1.6, marker="o", markersize=5, markeredgecolor=SURFACE, markeredgewidth=0.9, zorder=3)
        local_hour = pd.to_datetime(rows.time_local).dt.hour
        summary["day_pass" if code == "D" else "night_pass"] = {
            "observations": int(len(rows)), "median_ha": round(float(rows.new_area_ha.median())), "p90_ha": round(float(rows.new_area_ha.quantile(0.9))),
            "typical_local_hour": int(local_hour.mode().iloc[0]),
        }  # fmt: skip
    ax.legend(
        [Line2D([], [], color=colour, lw=1.6, marker="o", markersize=5) for _, _, colour in series], [label for label, _, _ in series],
        loc="lower left", bbox_to_anchor=(-0.01, 1.02), ncol=1, handlelength=1.4, labelcolor=SECONDARY, borderaxespad=0, labelspacing=0.3,
    )  # fmt: skip
    save(fig, "06_growth_per_step.png")
    numbers["growth_per_step"] = summary | {"observations_with_no_growth_pct": round(float(100 * (master.new_cells == 0).mean()), 1), "median_ha": round(float(grew.new_area_ha.median())), "p99_ha": round(float(grew.new_area_ha.quantile(0.99)))}


def growth_vs_weather(master: pd.DataFrame, numbers: dict) -> None:
    grew = master[master.new_cells > 0]
    fig, axes = figure("Growth rises with fire weather, but the ranges overlap heavily", "Dot: median area newly burning at an observation. Line: the middle half of observations. Log scale.", panels=2, bottom=0.2)
    summary = {}
    panels = (
        (axes[0], "isi", [0, 2, 5, 10, 15, 100], ["0-2", "2-5", "5-10", "10-15", "15+"], "Initial Spread Index (wind and fine fuel dryness)"),
        (axes[1], "wind_speed_mean_kmh", [0, 5, 10, 15, 20, 100], ["0-5", "5-10", "10-15", "15-20", "20+"], "Mean wind speed since the previous observation (km/h)"),
    )
    for ax, column, edges, labels, title in panels:
        groups = grew.groupby(pd.cut(grew[column], edges, labels=labels, right=False), observed=False).new_area_ha
        low, mid, high, count = groups.quantile(0.25), groups.median(), groups.quantile(0.75), groups.size()
        x = np.arange(len(labels))
        ax.vlines(x, low, high, color=BLUE, linewidth=1.6, zorder=3)
        ax.scatter(x, mid, s=46, color=BLUE, edgecolor=SURFACE, linewidth=1.2, zorder=4)
        ax.set_yscale("log")
        ax.set_ylim(8, 1_000)
        ax.set_yticks([10, 30, 100, 300, 1_000], ["10", "30", "100", "300", "1,000"])
        ax.set_xlim(-0.6, len(labels) - 0.4)
        ax.set_xticks(x, [f"{label}\nn = {n:,}" for label, n in zip(labels, count)])
        ax.yaxis.grid(True)
        ax.set_xlabel(title)
        summary[column] = {label: {"observations": int(count[label]), "p25_ha": round(float(low[label])), "median_ha": round(float(mid[label])), "p75_ha": round(float(high[label]))} for label in labels}
    axes[0].set_ylabel("Newly burning area (ha)")
    save(fig, "07_growth_vs_weather.png")
    numbers["growth_by_weather"] = summary


def direction_vs_wind(master: pd.DataFrame, numbers: dict) -> None:
    angle = master.head_vs_wind_angle_deg.dropna()
    edges = np.arange(0, 181, 15)
    share = 100 * np.histogram(angle, edges)[0] / len(angle)
    even = 100 / (len(edges) - 1)
    fig, ax = figure("Fires mostly run downwind, but far from always", "Angle between the direction a fire's head ran and the direction the wind was blowing towards. 0 is straight downwind.")
    ax.set_xlim(-6, 186)
    ax.set_ylim(0, np.ceil(share.max() / 5) * 5)
    ax.set_yticks(np.arange(0, np.ceil(share.max() / 5) * 5 + 1, 5))
    ax.set_xticks(edges)
    ax.yaxis.grid(True)
    ax.set_xlabel("Angle between fire run and wind (degrees)")
    ax.set_ylabel("Share of observations (%)")
    for left, value in zip(edges[:-1], share):
        bar(ax, left + 7.5, 0, value, 24, BLUE)
    ax.axhline(even, color=SECONDARY, linewidth=0.9, zorder=4)
    ax.annotate(f"{even:.1f}% in every bar if wind made no difference", (186, even), xytext=(0, 5), textcoords="offset points", ha="right", color=SECONDARY)
    save(fig, "08_direction_vs_wind.png")
    numbers["direction_vs_wind"] = {"observations": int(len(angle)), "within_45_deg_pct": round(float(100 * (angle <= 45).mean()), 1), "within_90_deg_pct": round(float(100 * (angle <= 90).mean()), 1), "median_deg": round(float(angle.median()))}


def fuel_inside_and_around(fires: pd.DataFrame, numbers: dict) -> None:
    classes = [("conifer", "Conifer"), ("mixedwood", "Mixedwood"), ("deciduous", "Aspen and other deciduous"), ("open", "Grass and open"), ("water", "Water"), ("non_fuel", "Non-fuel (bare, rock, regrowth)")]
    mapped = fires[fires.fuel_around_cells.notna()]
    around = [100 * mapped[f"fuel_around_{key}_share"].mean() for key, _ in classes]
    inside = [100 * mapped[f"fuel_inside_{key}_share"].mean() for key, _ in classes]
    fig, ax = figure("What grows around the fires, and what the 2024 map shows inside them", "Average share of land in each fuel class. Inside old perimeters the map shows the scar, not what burned.", legend=True, left=0.27)
    rows = np.arange(len(classes))[::-1]
    ax.set_ylim(-0.7, len(classes) - 0.3)
    ax.set_xlim(0, 64)
    ax.set_yticks(rows, [label for _, label in classes])
    ax.xaxis.grid(True)
    ax.spines["bottom"].set_visible(False)
    ax.set_xlabel("Share of land (%)")
    for row, outer, inner in zip(rows, around, inside):
        for offset, value, colour in ((0.19, outer, BLUE), (-0.19, inner, ORANGE)):
            bar(ax, row + offset, 0, value, 13, colour, horizontal=True)
            ax.annotate(f"{value:.0f}%", (value, row + offset), xytext=(5, 0), textcoords="offset points", va="center", color=SECONDARY, fontsize=8.5)
    swatches(ax, [("Ring around the fire", BLUE), ("Inside the final perimeter", ORANGE)])
    save(fig, "09_fuel_inside_and_around.png")
    numbers["fuel_share_pct"] = {label: {"around": round(outer, 1), "inside": round(inner, 1)} for (_, label), outer, inner in zip(classes, around, inside)}


def what_tracks_growth(master: pd.DataFrame, numbers: dict) -> None:
    grew = master[master.new_cells > 0]
    factors = [
        ("cumulative_area_ha", "Fire's size so far"), ("fwi", "Fire Weather Index"), ("isi", "Initial Spread Index"), ("bui", "Buildup Index"),
        ("ffmc", "Fine Fuel Moisture Code"), ("wind_speed_mean_kmh", "Mean wind speed"), ("temp_max_c", "Warmest hour"),
        ("hours_since_previous_observation", "Hours since previous look"), ("rh_min_pct", "Driest hour's humidity"), ("precip_total_mm", "Rain"),
    ]  # fmt: skip
    values = sorted(((float(grew.new_area_ha.corr(grew[column], method="spearman")), label) for column, label in factors), reverse=True)
    fig, ax = figure("What goes with bigger growth", "Rank correlation between each factor and the area newly burning at an observation. 0 is no link, 1 a perfect one.", left=0.27)
    rows = np.arange(len(values))[::-1]
    ax.set_ylim(-0.7, len(values) - 0.3)
    ax.set_xlim(-0.3, 0.6)
    ax.set_yticks(rows, [label for _, label in values])
    ax.xaxis.grid(True)
    ax.spines["bottom"].set_visible(False)
    ax.axvline(0, color=AXIS, linewidth=0.9, zorder=2)
    ax.set_xlabel("Rank correlation with newly burning area")
    for row, (value, _) in zip(rows, values):
        bar(ax, row, 0, value, 15, BLUE, horizontal=True)
        ax.annotate(f"{value:+.2f}", (value, row), xytext=(5 if value >= 0 else -5, 0), textcoords="offset points", va="center", ha="left" if value >= 0 else "right", color=SECONDARY, fontsize=8.5)
    save(fig, "10_what_tracks_growth.png")
    numbers["rank_correlation_with_new_area"] = {label: round(value, 2) for value, label in values}


def main() -> None:
    log = action_log("12 dataset eda")
    fires = pd.read_csv(PROCESSED / "ontario_fire_spread_fires_v1.csv", dtype={"fire_id": str})
    master = pd.read_csv(PROCESSED / "ontario_fire_spread_master_v1.csv", dtype={"fire_id": str})
    log.info(f"START: charts from {len(fires):,} fires and {len(master):,} observations")
    numbers: dict = {}
    fires_per_year(fires, numbers)
    fire_sizes(fires, numbers)
    fire_locations(fires, numbers)
    season_and_cause(fires, numbers)
    satellite_record(fires, master, numbers)
    growth_per_step(master, numbers)
    growth_vs_weather(master, numbers)
    direction_vs_wind(master, numbers)
    fuel_inside_and_around(fires, numbers)
    what_tracks_growth(master, numbers)
    (OUT / "eda_numbers.json").write_text(json.dumps(numbers, indent=2) + "\n", encoding="utf-8")
    log.info(f"DONE: wrote {len(list(OUT.glob('*.png')))} figures and eda_numbers.json to {OUT.relative_to(PROJECT_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
