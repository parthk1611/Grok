"""Create portable charts, feature dictionary, report and notebook from EDA outputs."""
import argparse
import base64
import html
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--eda-dir", type=Path, default=ROOT / "docs/eda")
parser.add_argument("--feature-selection", type=Path, default=ROOT / "data/metadata/ignition_feature_selection_v1.json")
args = parser.parse_args()
OUT = args.eda_dir.resolve()
S = json.loads((OUT / "eda_summary.json").read_text(encoding="utf-8"))
annual = pd.read_csv(OUT / "annual_summary.csv")
monthly = pd.read_csv(OUT / "monthly_summary.csv")
missing = pd.read_csv(OUT / "missingness.csv")
stats = pd.read_csv(OUT / "feature_statistics.csv").set_index("column")
cells = pd.read_csv(OUT / "cell_summary.csv")
correlations = pd.read_csv(OUT / "sample_feature_correlations.csv", index_col=0)
strong = pd.read_csv(OUT / "strong_correlations.csv")
sample = pd.read_csv(OUT / "uniform_sample_100k.csv.gz")
positive = pd.read_csv(OUT / "positive_cell_days.csv.gz")
selection = json.loads(args.feature_selection.read_text(encoding="utf-8"))
FWI_URL = "https://natural-resources.canada.ca/forest-forestry/wildland-fires/canada-fire-weather-index-system"
plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":10, "axes.spines.top":False, "axes.spines.right":False, "axes.titleweight":"bold", "figure.facecolor":"white", "axes.facecolor":"white", "savefig.facecolor":"white"})
BLUE, ORANGE = "#1565a5", "#c55420"

def save(fig, name):
    fig.savefig(OUT / name, dpi=160, bbox_inches="tight")
    plt.close(fig)

fig, axs = plt.subplots(2, 2, figsize=(13,8), layout="constrained")
axs[0,0].bar(annual.year, annual.positives, color=BLUE)
axs[0,0].set(title="Recorded positive cell-days by year", ylabel="Positive cell-days", xticks=annual.year)
axs[0,1].bar(monthly.month, monthly.ignition_rate*10000, color=BLUE)
axs[0,1].set(title="Seasonal ignition rate", ylabel="Positive cell-days per 10,000 cell-days", xticks=monthly.month, xticklabels=["May","Jun","Jul","Aug","Sep","Oct"])
axs[1,0].bar(annual.year, annual.weather_missing_fraction*100, color=ORANGE)
axs[1,0].set(title="Prior-day weather missingness by year", ylabel="Missing rows (%)", ylim=(0,100), xticks=annual.year)
quality_rows = [{"state":"Weather available" if not m else "Weather missing", "rows":sum(x["rows"] for x in S["weather_label_counts"] if x["cwfis_missing"]==m), "positives":sum(x["rows"] for x in S["weather_label_counts"] if x["cwfis_missing"]==m and x["ignition"]==1)} for m in [0,1]]
quality = pd.DataFrame(quality_rows)
quality["rate_per_10000"] = quality.positives / quality.rows * 10000
axs[1,1].bar(quality.state, quality.rate_per_10000, color=[BLUE,ORANGE])
axs[1,1].set(title="Ignition rate by weather availability", ylabel="Positive cell-days per 10,000 cell-days")
fig.suptitle(f"Ontario ignition EDA | exact aggregates from all {S['rows']:,} rows", fontsize=16)
save(fig, "eda_overview.png")

fig, ax = plt.subplots(figsize=(12,8), layout="constrained")
nonzero = missing[missing.missing_pct>0].sort_values("missing_pct")
ax.barh(nonzero.column, nonzero.missing_pct, color=BLUE)
ax.set(title="Missingness is part of the feature contract", xlabel="Missing rows (%)", xlim=(0,100))
for i,v in enumerate(nonzero.missing_pct):
    ax.text(v+.5,i,f"{v:.2f}%",va="center",fontsize=8)
save(fig, "feature_missingness.png")

fig, axs = plt.subplots(1,2,figsize=(13,6),layout="constrained")
x,y = cells.grid_x_m/1000, cells.grid_y_m/1000
a = axs[0].scatter(x,y,c=cells.weather_missing_fraction*100,s=7,marker="s",cmap="magma",vmin=0,vmax=100)
b = axs[1].scatter(x,y,c=np.log1p(cells.positive_cell_days),s=7,marker="s",cmap="viridis")
fig.colorbar(a,ax=axs[0],label="Weather missing (%)")
fig.colorbar(b,ax=axs[1],label="log(1 + positive cell-days)")
axs[0].set_title("Coverage varies across cells")
axs[1].set_title("Recorded ignition concentration")
for ax in axs:
    ax.set(xlabel="EPSG:3978 x (km)",ylabel="EPSG:3978 y (km)",aspect="equal")
fig.suptitle("Exact cell aggregates | schematic cell locations, no basemap",fontsize=15)
save(fig,"spatial_coverage.png")

fig,axs = plt.subplots(2,3,figsize=(13,8),layout="constrained")
distribution_features = [("cwfis_temp_c","Temperature (C)"),("cwfis_rh_pct","Relative humidity (%)"),("cwfis_ffmc","Fine Fuel Moisture Code"),("cwfis_fwi","Fire Weather Index"),("cwfis_precip_7d_mm","7-day precipitation (mm)"),("fire_history_neighborhood_365d","Earlier neighbourhood events (365d)")]
for ax,(c,label) in zip(axs.flat,distribution_features):
    neg=sample.loc[sample.ignition.eq(0),c].dropna()
    pos=positive[c].dropna()
    lo=min(neg.min(),pos.min())
    hi=max(neg.quantile(.99),pos.quantile(.99))
    bins=np.linspace(lo,hi if hi>lo else lo+1,31)
    for vals,color,name in [(neg,BLUE,"No ignition: uniform sample"),(pos,ORANGE,"Ignition: all observed positives")]:
        counts,edges=np.histogram(vals,bins=bins)
        ax.stairs(counts/max(len(vals),1)*100,edges,label=name,color=color,linewidth=1.6)
    ax.set(xlabel=label,ylabel="Observed class rows per bin (%)")
axs[0,0].legend(fontsize=8)
fig.suptitle("Class distributions | observed values; axes capped at the larger class p99",fontsize=13)
save(fig,"class_distributions.png")

fig,ax = plt.subplots(figsize=(13,11),layout="constrained")
image=ax.imshow(correlations,vmin=-1,vmax=1,cmap="RdBu_r",interpolation="nearest")
labels=[c.replace("calendar_","cal_").replace("fire_history_","hist_").replace("cwfis_","").replace("terrain_","terr_") for c in correlations.columns]
ax.set(xticks=range(len(labels)),yticks=range(len(labels)),xticklabels=labels,yticklabels=labels,title=f"Predictor correlations | uniform {S['uniform_sample_rows']:,}-row sample")
plt.setp(ax.get_xticklabels(),rotation=90,fontsize=7)
plt.setp(ax.get_yticklabels(),fontsize=7)
fig.colorbar(image,ax=ax,label="Pearson r; pairwise available values",shrink=.7)
save(fig,"feature_correlations.png")

dictionary=[]
def add(group, specs):
    for name,meaning,why in specs:
        dictionary.append({"feature":name,"group":group,"meaning":meaning,"why_include":why,"missing_pct":float(stats.loc[name,"missing_pct"])})

add("Calendar / solar",[
 ("calendar_day_of_year","Day number within year","Tests seasonal timing; compare with cyclic encodings during training."),
 ("calendar_doy_sin","Sine of annual day angle","Paired with cosine, represents a repeating annual cycle without an end-of-year jump."),
 ("calendar_doy_cos","Cosine of annual day angle","Completes the cyclic seasonal representation; neither component should be interpreted alone."),
 ("calendar_is_weekend","Saturday/Sunday flag","Tests a possible human-activity pattern; usefulness has not been established."),
 ("calendar_day_length_hours","Astronomical daylight hours","Provides latitude-and-date solar context; not a direct fuel-moisture observation.")])
add("Static terrain",[
 ("terrain_elevation_mean_m","Mean sampled ground elevation (m)","Tests differences associated with static topographic context."),
 ("terrain_slope_deg","Fitted terrain slope (degrees)","Describes local ground inclination; relevance to ignition must be evaluated."),
 ("terrain_aspect_sin","Sine of terrain aspect","Together with cosine, encodes slope orientation without the 0/360-degree discontinuity."),
 ("terrain_aspect_cos","Cosine of terrain aspect","Completes the orientation pair; does not substitute for a vegetation or fuel map."),
 ("terrain_ruggedness_m","Variability among sampled elevations (m)","Describes local topographic variation, rather than vegetation, access or fire spread.")])
add("Prior-day weather",[
 ("cwfis_temp_c","Prior-day noon air temperature (C)","Tests atmospheric warmth as a candidate signal of ignition conditions."),
 ("cwfis_rh_pct","Prior-day relative humidity (%)","Tests atmospheric dryness; not a direct measure of fuel moisture."),
 ("cwfis_wind_speed_kmh","Prior-day wind speed (km/h)","Describes wind conditions; direction and future hourly winds are absent."),
 ("cwfis_precip_mm","Prior-day precipitation (mm)","Represents recent rain; zero rain and missing rain must remain distinct."),
 ("cwfis_precip_7d_mm","Total rain over seven completed prior dates (mm)","Captures recent wet/dry conditions over a longer window; blank unless all seven observations exist.")])
add("FWI components",[
 ("cwfis_ffmc","Fine Fuel Moisture Code","Describes dryness of litter and fine surface fuels relevant to ignition."),
 ("cwfis_dmc","Duff Moisture Code","Describes moisture in moderately deep organic layers; adds a longer moisture-memory signal."),
 ("cwfis_dc","Drought Code","Describes deep organic-layer moisture and seasonal drought."),
 ("cwfis_isi","Initial Spread Index","Combines fine-fuel dryness and wind; tests associated conditions, not a spread target."),
 ("cwfis_bui","Buildup Index","Combines DMC/DC to represent fuel available for combustion."),
 ("cwfis_fwi","Fire Weather Index","Combines ISI/BUI to rate potential fire intensity; not an ignition probability."),
 ("cwfis_dsr","Daily Severity Rating","A transformation of FWI related to suppression difficulty; likely redundant with FWI.")])
add("Past ignition history",[
 ("fire_history_same_cell_30d","Earlier same-cell ignition records in 30d","Tests recent recorded activity; all dates precede the issue day."),
 ("fire_history_neighborhood_30d","Earlier ignition records in the 3x3-cell neighbourhood in 30d","Adds nearby recent context; neighbourhood size is about 30x30 km away from boundary clipping."),
 ("fire_history_same_cell_365d","Earlier same-cell records in 365d","Tests longer-term recurrence in the available archive; early-2010 history is left-censored."),
 ("fire_history_neighborhood_365d","Earlier neighbourhood records in 365d","Adds broader annual recurrence context without using future records."),
 ("fire_history_days_since_same_cell","Days since an earlier observed same-cell ignition","Captures recency; blank means no earlier observed event in the available 2010+ history, not zero days.")])
add("Coverage / quality",[
 ("terrain_sample_count","Number of valid terrain samples (0-9)","Makes terrain reliability visible."),
 ("terrain_coverage_fraction","Terrain sample count / 9","Expresses terrain coverage; exactly redundant with its count."),
 ("cwfis_station_count","Usable selected weather stations (0-4)","Identifies strength of available weather support."),
 ("cwfis_nearest_station_km","Distance to nearest usable selected station (km)","Shows how far the weather estimate is extrapolated from a station."),
 ("cwfis_observation_coverage_fraction","Usable station count / 4","Expresses station coverage; exactly redundant with station count."),
 ("cwfis_imputed_station_fraction","Fraction of selected source records marked interpolated/imputed","Preserves source processing; all observed v1 values are zero, so test redundancy with the missingness flag."),
 ("cwfis_calcstatus_nonzero_fraction","Fraction with a nonzero source calculation-status flag","Preserves calculation states; all observed v1 values are zero. Nonzero is not automatically invalid in other sources."),
 ("cwfis_missing","No usable selected prior-day weather station (0/1)","Distinguishes unavailable weather from real physical zeroes."),
 ("cwfis_precip_7d_coverage_days","Usable days in seven-day rain window (0-7)","Explains why rain totals are missing and distinguishes partial coverage.")])
assert len(dictionary)==36 and {x["feature"] for x in dictionary}==set(selection["predictors"])
pd.DataFrame(dictionary).to_csv(OUT/"feature_dictionary.csv",index=False)

def table(df, formats=None):
    display=df.copy()
    for c,fmt in (formats or {}).items():
        display[c]=display[c].map(lambda x:fmt.format(x))
    return display.to_html(index=False,border=0,escape=True,classes="data-table")

def picture(name,alt):
    encoded=base64.b64encode((OUT/name).read_bytes()).decode()
    return f'<img src="data:image/png;base64,{encoded}" alt="{html.escape(alt)}">'

year_missing=annual.loc[annual.weather_missing_fraction.idxmax()]
missing_2016=annual.loc[annual.year.eq(2016),"weather_missing_fraction"].iloc[0]*100
month_names={5:"May",6:"June",7:"July",8:"August",9:"September",10:"October"}
season_peak=monthly.loc[monthly.ignition_rate.idxmax()]
violations={k:v for k,v in S["validation_violation_counts"].items() if v}
weather_missing_count=int(missing.loc[missing.column.eq("cwfis_temp_c"),"missing_rows"].iloc[0])
positive_weather_missing=next(x["rows"] for x in S["weather_label_counts"] if x["ignition"]==1 and x["cwfis_missing"]==1)
findings=[
 f'{S["positive_cell_days"]:,} positive cell-days ({S["ignition_prevalence_pct"]:.5f}%); {S["negative_to_positive_ratio"]:,.0f} negatives per positive. An all-negative classifier reaches {S["all_negative_accuracy_pct"]:.5f}% accuracy while detecting zero ignitions.',
 f'Daily weather/FWI is missing on {weather_missing_count:,} rows ({stats.loc["cwfis_temp_c","missing_pct"]:.2f}%). Seven-day precipitation is missing on {stats.loc["cwfis_precip_7d_mm","missing_pct"]:.2f}%. Keep these rows and quality flags.',
 f'No earlier observed same-cell event is recorded for {stats.loc["fire_history_days_since_same_cell","missing_pct"]:.2f}% of rows. This censored history state differs from unavailable weather.',
 f'{positive_weather_missing:,} positive cell-days ({100*positive_weather_missing/S["positive_cell_days"]:.2f}%) lack daily weather. Complete-case deletion would discard these recorded ignitions.',
 f'The largest annual weather gap occurs in {int(year_missing.year)}: only {int(year_missing.weather_available):,} of {int(year_missing.rows):,} rows have weather ({100*year_missing.weather_missing_fraction:.5f}% missing). In 2016, {missing_2016:.2f}% of rows lack weather. The highest pooled monthly ignition rate is in {month_names[int(season_peak.month)]} ({season_peak.ignition_rate*10000:.2f} per 10,000 cell-days).',
 'Two quality fields (cwfis_imputed_station_fraction and cwfis_calcstatus_nonzero_fraction) are zero whenever observed. Counts and their coverage fractions are deterministic rescalings. They remain in the recommended shared source set; their incremental usefulness should be tested inside training splits.',
 'Coverage, seasonal timing and nearby observations can confound apparent feature differences. Class-distribution plots do not prove causal effects or predictive usefulness.'
]
contract_text='This panel estimates whether at least one recorded ignition occurs in a 10 km cell on a target local calendar day. Issue time is 00:00 America/Toronto. Weather is from t-1; seven-day rain uses seven completed preceding dates; ignition histories exclude the target date. Archives lack historical per-record available_at times. This is retrospective hindcast, not a validated operational forecast replay.'
excluded='Use ignition as the binary target. Exclude ignition_count and large_fire_200ha from predictors. Use grid_id and ignition_date to join and split, then remove them from the model matrix. Also exclude issue_time_utc, cwfis_observation_date and source/version metadata. Final perimeters, final fire size and future observations must not become ignition predictors.'
preprocessing='Logistic regression: training-only imputation, numeric scaling and explicit missingness/censoring indicators. Histogram gradient boosting: preserve NaNs and quality fields. The baseline uses training-label prevalence and no predictors. The 36 columns are source inputs; derived indicators may increase the final matrix width.'
future='HRDPS, ERA5-Land, fuel type, vegetation density, lightning, roads and settlement are not present in this v1 matrix. The PDF describes proposed future work. For HRDPS, match variables, units, grid, forecast lead times and issue timing explicitly; a same-day hour 0-6 live forecast cannot silently replace prior-day observations. CWFIS-derived FWI and seven-day rain also require a documented calculation/initialisation contract. Wind direction and a dated fuel layer are required separately for spread work. Existing fires should feed a separate incident-conditioned spread simulator.'
evaluation='Use whole dates/seasons in chronological development and final holdouts, plus a separate spatial-block experiment. Fit transformations and feature selection only on each training side. Preserve natural prevalence in calibration/test data; tune weighting only in training/validation. Report average precision, precision/recall and alert burden, reliability plots, Brier score/log loss and performance by weather coverage. Calibration data must be later and separate from model fitting. Default random early-stopping validation must be replaced by a chronological strategy.'
limits='The listed full-file checks cover schema parsing, keys/universe, labels/count consistency, timing strings, selected bounds and missingness relationships. They do not independently reconstruct NFDB labels, station-selection history, past-fire features, precipitation sums or source publication availability. The documented station-candidate selection issue and event-dependent edge-cell inclusion remain sensitivity questions; EDA does not resolve them.'
validation='No violations were found in the listed checks.' if not violations else 'Checks requiring review: '+json.dumps(violations)
mean_features=["cwfis_temp_c","cwfis_rh_pct","cwfis_ffmc","cwfis_fwi","cwfis_precip_7d_mm","fire_history_neighborhood_365d"]
class_means=stats.loc[mean_features,["negative_mean","positive_mean","negative_observed_rows","positive_observed_rows"]].reset_index()
class_means.to_csv(OUT/"class_feature_means.csv",index=False)

glossary=pd.DataFrame(dictionary)
section_html=''.join(f'<h3>{html.escape(g)}</h3>'+table(glossary[glossary.group.eq(g)][["feature","meaning","why_include","missing_pct"]],{"missing_pct":"{:.2f}%"}) for g in glossary.group.drop_duplicates())
html_report=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ontario ignition dataset: EDA and feature guide</title><style>body{{font:16px/1.6 system-ui,sans-serif;background:#f4f6f8;color:#182839;margin:0}}main{{max-width:1160px;margin:auto;padding:36px}}h1{{font-size:34px;line-height:1.15}}h2{{margin-top:42px}}.lead{{font-size:19px}}.card{{background:white;padding:22px;border-radius:12px;margin:20px 0}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.metric{{padding:16px;background:#e8f0f6;border-radius:10px}}.metric strong{{font-size:25px;display:block}}img{{max-width:100%;height:auto;display:block;margin:22px auto}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:10px;text-align:left;vertical-align:top;border-bottom:1px solid #dbe2e8}}th{{background:#eef3f7}}code{{font-size:13px;overflow-wrap:anywhere}}a{{color:#1565a5}}li{{margin:10px 0}}.muted{{color:#526575}}@media(max-width:700px){{main{{padding:18px}}.metrics{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:28px}}}}</style><main>
<p class="muted">COMP-385 | 5 October 2026 | read-only exploratory analysis</p><h1>What is in the ignition dataset, and why do we use these features?</h1><p class="lead">Features describe conditions before a day begins. The label records whether an ignition occurred. The model will learn associations between those conditions and recorded ignition risk; EDA checks whether those inputs are usable.</p>
<div class="metrics"><div class="metric"><strong>{S['rows']:,}</strong>cell-days scanned</div><div class="metric"><strong>{S['positive_cell_days']:,}</strong>positive cell-days</div><div class="metric"><strong>36</strong>recommended source predictors</div><div class="metric"><strong>{stats.loc['cwfis_temp_c','missing_pct']:.2f}%</strong>daily weather missing</div></div>
<section class="card"><h2>Findings</h2><ul>{''.join('<li>'+html.escape(f)+'</li>' for f in findings)}</ul>{picture('eda_overview.png','Year, season and weather-coverage aggregates')}</section>
<section class="card"><h2>Scope, timing and leakage</h2><p>{contract_text}</p><p>{excluded}</p><p>{preprocessing}</p><p class="muted">The feature dictionary follows data/metadata/ignition_feature_selection_v1.json and repository ABOUT.md/AGENTS.md. None of those source files or the CSV were edited.</p></section>
<section class="card"><h2>Missingness and spatial coverage</h2>{picture('feature_missingness.png','Full-file feature missingness')}{picture('spatial_coverage.png','Spatial weather coverage and ignition concentration')}<div class="scroll">{table(quality,{"rate_per_10000":"{:.3f}"})}</div><p>Weather availability can represent archive and station-coverage patterns. A relationship with ignition rate should not be interpreted as missing weather causing fires.</p></section>
<section class="card"><h2>Observed class distributions</h2>{picture('class_distributions.png','Feature distributions by ignition label')}<p>All positive cell-days are used; non-ignition rows come from the seeded uniform sample. Only observed values enter each plot. Each class uses its own denominator, so plots compare conditional distributions rather than the extremely unequal class sizes.</p><h3>Exact class means across all observed rows</h3><div class="scroll">{table(class_means,{"negative_mean":"{:.2f}","positive_mean":"{:.2f}"})}</div><p>Weather-covered ignition days are warmer, less humid and have higher FFMC/FWI on average. Seasonal and geographic differences can explain part of these associations; EDA is not a causal or held-out modelling test.</p></section>
<section class="card"><h2>Correlations and redundant inputs</h2>{picture('feature_correlations.png','Sample predictor correlation matrix')}<div class="scroll">{table(strong.head(14),{"pearson_r":"{:.4f}"})}</div><p>Correlations use pairwise available values in a uniform 100,000-row sample. They are not feature-importance scores. Coverage fractions are deterministic rescalings of counts. FWI components are mathematically related. Two quality fields are constant zero whenever observed; their correlation entries are undefined. Keep the shared contract for the first comparison, then test reduced groups inside training-only feature selection.</p></section>
<section class="card"><h2>Every recommended feature</h2><p>Reasons below are candidate modelling rationales, not claims that usefulness has already been demonstrated. Terrain remains terrain-only. FWI terminology is based on <a href="{FWI_URL}">Natural Resources Canada's FWI documentation</a>.</p><div class="scroll">{section_html}</div></section>
<section class="card"><h2>Your HRDPS work and future spread inputs</h2><p>{future}</p><h2>Next modelling stage</h2><p>{evaluation}</p></section>
<section class="card"><h2>Validation and reproducibility</h2><p>{validation}</p><p>{limits}</p><p>{html.escape(S['method'])}</p><p>CSV read: {S['source_bytes']:,} bytes; 48-column header; {S['dates']:,} dates; {S['grid_cells']:,} cells; scan {S['scan_seconds']:.1f} seconds. Uniform sample seed: {S['sample_seed']}; {S['uniform_sample_rows']:,} rows including {S['uniform_sample_positive_rows']} positives.</p><p>scripts/run_ignition_eda.py reproduces the scan using pandas/numpy. scripts/build_ignition_eda_report.py renders charts with Matplotlib. Tables, samples, checks, feature dictionary and a notebook are saved beside this report. No models were fitted and no values were filled or rows removed.</p><details><summary>Full-file check counts (zero means no violation)</summary>{table(pd.DataFrame(S['validation_violation_counts'].items(),columns=['check','violations']))}</details></section>
</main></html>'''
(OUT/"eda_report.html").write_text(html_report,encoding="utf-8")
annual_markdown = '\n'.join(['| Year | Positive cell-days | Weather missing |', '| --- | ---: | ---: |', *[f'| {int(r.year)} | {int(r.positives):,} | {r.weather_missing_fraction*100:.5f}% |' for r in annual.itertuples()]])
md=['# Ontario ignition EDA and feature guide','', 'Analysis date: 2026-10-05. No model fitting or data modification.','', '## Findings','',*['- '+f for f in findings],'','## Timing and predictor contract','',contract_text,'',excluded,'',preprocessing,'','## Annual aggregates','',annual_markdown,'','## Feature dictionary','']
for entry in dictionary:
    md.append(f'- **{entry["feature"]}** ({entry["group"]}): {entry["meaning"]}. Why: {entry["why_include"]} Missing: {entry["missing_pct"]:.2f}%.')
md.extend(['','FWI definitions: [Natural Resources Canada]('+FWI_URL+').','','## HRDPS and spread','',future,'','## Evaluation','',evaluation,'','## Validation scope','',validation,'',limits,'',S['method']])
(OUT/"eda_report.md").write_text('\n'.join(md)+'\n',encoding="utf-8",newline="\n")

def cell(kind,source):
    obj={"cell_type":kind,"metadata":{},"source":source.splitlines(keepends=True)}
    if kind=="code":
        obj.update(execution_count=None,outputs=[])
    return obj
notebook={"nbformat":4,"nbformat_minor":5,"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python"}},"cells":[cell("markdown","# Ontario ignition EDA\nFull-file aggregates plus a uniform sampled analysis. No modelling, imputation or row deletion. Run `scripts/run_ignition_eda.py` to regenerate cached tables; scanning the CSV reads 6.54 GB. The HTML report includes all figures and explanations."),cell("code",'import json\nfrom pathlib import Path\nimport pandas as pd\nfrom IPython.display import display, Image\n# Works when Jupyter starts in this folder or any parent inside the checkout.\nEDA = next((p for root in [Path.cwd(), *Path.cwd().parents] for p in [root, root / "docs/eda"] if (p / "eda_summary.json").is_file()), None)\nif EDA is None:\n    raise FileNotFoundError("Start Jupyter inside the COMP-385 checkout.")\nsummary = json.loads((EDA / "eda_summary.json").read_text(encoding="utf-8"))\ndisplay({k: summary[k] for k in ["rows","positive_cell_days","ignition_prevalence_pct","grid_cells","dates"]})'),cell("code",'display(pd.read_csv(EDA / "annual_summary.csv"))\ndisplay(pd.read_csv(EDA / "monthly_summary.csv"))'),cell("code",'display(pd.read_csv(EDA / "missingness.csv").sort_values("missing_pct", ascending=False))'),cell("code",'display(pd.read_csv(EDA / "feature_dictionary.csv"))'),cell("code",'display(pd.read_csv(EDA / "feature_statistics.csv"))\ndisplay(pd.read_csv(EDA / "strong_correlations.csv"))'),cell("code",'for chart in ["eda_overview.png","feature_missingness.png","spatial_coverage.png","class_distributions.png","feature_correlations.png"]:\n    display(Image(filename=str(EDA / chart)))'),cell("markdown",contract_text+'\n\n'+preprocessing+'\n\n'+future+'\n\n'+limits),cell("code",'display(pd.DataFrame(summary["validation_violation_counts"].items(), columns=["check","violations"]))')]}
for i,c in enumerate(notebook['cells']):
    c['id']=f'eda-{i:02d}'
(OUT/"ontario_ignition_eda.ipynb").write_text(json.dumps(notebook,indent=2),encoding="utf-8")
print(json.dumps({"findings":findings,"annual":annual.to_dict(orient="records"),"quality":quality.to_dict(orient="records"),"strong_correlations":strong.head(10).to_dict(orient="records"),"violations":violations,"report":str(OUT/"eda_report.html")},indent=2))
