# CWFIS weather gap and historical wind: options and decision

Status: ATLAS options paper, 2026-11-02. The tradeoffs below were written without picking an option; the Team Lead decision is recorded in the next section.

## Decision (Team Lead, 2026-11-02)

- **(b) the CWFIS archive is the primary weather/FWI source.** Gaps stay missing and carry a missingness flag (as in the existing `cwfis_missing` and coverage fields). No imputation is added later; this is also what `AGENTS.md` already requires.
- **(a) ECCC stations are a gap-fill candidate only after the ECCC licence is confirmed.** The API collection metadata states no licence (measured below), so nothing is adopted yet.
- **(c) ERA5 is not adopted.**
- Gate kept: daily inputs for target day t are rebuilt from hourly values that end by local midnight (`rebuild_daily_total_from_hourly` and its tests in `tests/test_source_alignment.py`); an ECCC LST daily record for t-1 is rejected in summer.

Every number below was either read from a committed repo audit (marked **repo audit**) or measured on 2026-11-02
from the box (marked **measured**). Anything else is marked **not measured**. The measurement scripts ran in
scratch space and are not committed; the method is stated beside each result so it can be repeated.

## 0. What the gap is, measured from the repo

- Original v1 panel: CWFIS weather/FWI missing on 9,340,916 of 17,798,320 rows = 52.48% (**repo audit**, `AGENTS.md`, `ABOUT.md`).
  By year it was 27.1% to 36.8% in 2010-2014, 99.9991% in 2015, 98.5% in 2016, 72.9% in 2017, 67.4% in 2018 and 25.0% in 2019
  (`docs/modeling/data_quality_report.md`).
- Corrected modelling cache (as-of station catalogue before nearest-4, global date index; same CWFIS archive): missing on
  1,275,992 of 17,798,320 rows = **7.17%** (**repo audit**, summed from `annual` in `docs/modeling/corrected_weather_audit.json`).
  By year: 2010 2.15%, 2011 4.70%, 2012 5.89%, 2013 8.72%, 2014 6.53%, 2015 3.95%, 2016 4.86%, 2017 10.33%, 2018 15.98%, 2019 8.59%.
  Seven-day precipitation is still missing on 2,171,969 rows = 12.20% (same audit).
- Consequence: most of the 52.5% gap was a join/ordering artefact of the v1 build, not absent station data. The corrected cache is
  gitignored (`data/interim/ignition_modeling_cache/`), so a fresh clone still shows the 52.48% panel.
- Not diagnosed by ATLAS: why 2017-2018 remain at 10-16% after the correction (**not measured**).

## Option (a): ECCC station observations, FWI computed by us

What it is: take noon local-standard-time observations from ECCC climate stations (`api.weather.gc.ca/collections/climate-hourly`
or the bulk CSV endpoint), compute FFMC/DMC/DC/ISI/BUI/FWI ourselves, and interpolate to cells.

Measured (2026-11-02):
- Endpoints work without authentication. `climate-hourly` holds 278,049,185 records and `climate-daily` 184,145,738; `climate-stations`
  lists 1,572 Ontario stations. The bulk CSV endpoint returned hourly (`Date/Time (LST)`) and daily CSV for station 4841.
- Sample of Ontario hourly records at 12:00 LST on 15 July of each year (one API call each, about 0.5 s):

| Year | Ontario stations reporting at noon | with temperature, RH and wind all present | % of 9,673 cells with such a station within 100 km | within 200 km |
| --- | ---: | ---: | ---: | ---: |
| 2010 | 104 | 90 | 71.9 | 96.1 |
| 2011 | 103 | 93 | 71.5 | 96.1 |
| 2012 | 102 | 92 | 68.3 | 95.6 |
| 2013 | 110 | 99 | 71.6 | 96.1 |
| 2014 | 110 | 102 | 71.6 | 96.1 |
| 2015 | 125 | 118 | 76.5 | 100.0 |
| 2016 | 129 | 122 | 76.5 | 100.0 |
| 2017 | 130 | 122 | 73.8 | 99.8 |
| 2018 | 128 | 120 | 73.0 | 98.2 |
| 2019 | 132 | 122 | 72.5 | 98.1 |

- Adding the 24 h rain window (13:00 LST the day before to 12:00 LST, stations with 24 of 24 hourly precipitation values) cuts it down:

| Date | stations with 24/24 precip | also noon T, RH, wind ("FWI-ready") | % cells within 100 km | within 200 km |
| --- | ---: | ---: | ---: | ---: |
| 2015-07-15 | 78 | 74 | 69.7 | 100.0 |
| 2018-07-15 | 69 | 66 | 63.5 | 95.9 |
| 2019-07-15 | 71 | 66 | 68.0 | 96.9 |

- These are single-date samples. Season-wide coverage, FWI start-up/overwinter handling, and interpolation error were **not measured**.
- `capability_2_fire_spread/scripts/fire_weather_index.py` already has FFMC, DMC, DC, ISI, BUI and FWI functions (`daily_codes`), so the
  equations exist in the repo; their agreement with CWFIS values was **not measured** by ATLAS.

Tradeoffs:
- Coverage: sample above suggests about 96-100% of cells within 200 km of a complete noon station, but only about 64-70% within 100 km;
  fewer stations than the CWFIS archive on the same dates (CWFIS counts are in option (b)).
- Availability time and leakage: ECCC hourly data are observations with a known valid time (the record carries `UTC_DATE`), which is
  better provenance than the CWFIS archive; archive publication time per record is still **not available**, so it remains hindcast.
  LST noon is 13:00 civil time in summer; the t-1 cutoff is safe for noon values. The ECCC *daily* record for LST day t-1 ends at 01:00
  local civil time on day t in summer, after the 00:00 issue time (tested in `tests/test_source_alignment.py`), so daily totals must be
  rebuilt from hourly values up to midnight.
- Compute and size: about 3,000 hourly records per Ontario day (measured on three dates) implies roughly 5.5 million records for 1,840
  days (arithmetic, not downloaded). FWI codes need a continuous daily chain from spring start-up, which is new code and new validation.
- Licensing: the API collection metadata does not state a licence (**measured**: `license` field absent). ECCC open data is generally under
  the Open Government Licence - Canada, but that was **not verified** here.
- Consistency with live HRDPS: observation-based codes differ in kind from a forecast field; a live feed would need either station
  observations (same family as this option) or an HRDPS-derived FWI, which is a different input distribution. Parity not tested.

## Option (b): the CWFIS FWI archive (`cwfis_fwi2010sv3.0_ll.csv.bz2`)

What it is: the archive already used by the v1 panel; the downloads folder also lists other decade files and station lists.

Measured (2026-11-02, file downloaded to scratch, SHA-256 matches the pinned `67e70616...0329246`):
- 4,159,936 rows, 23 columns, `rep_date` 2010-01-01 to 2019-12-31, every row stamped 12:00.
- Ontario box (lat 41.5-57.5, lon -96 to -74), May-October, rows with a non-null `fwi`: distinct stations and mean stations per day:

| Year | distinct usable stations | mean per day | min per day | max per day |
| --- | ---: | ---: | ---: | ---: |
| 2010 | 131 | 123.2 | 69 | 131 |
| 2011 | 107 | 100.0 | 44 | 107 |
| 2012 | 107 | 99.9 | 68 | 107 |
| 2013 | 120 | 102.0 | 44 | 120 |
| 2014 | 127 | 117.0 | 51 | 127 |
| 2015 | 217 | 165.4 | 68 | 217 |
| 2016 | 221 | 211.0 | 148 | 221 |
| 2017 | 213 | 194.0 | 79 | 208 |
| 2018 | 208 | 192.9 | 112 | 208 |
| 2019 | 367 | 320.1 | 101 | 367 |

- 15 July sample, stations with a non-null FWI anywhere in Canada, and the share of the 9,673 cells with a usable station within 100 km / 200 km:
  2010 823 stations, 67.8% / 87.5%; 2011 697, 66.1% / 94.5%; 2012 725, 62.3% / 89.7%; 2013 756, 66.3% / 94.5%; 2014 767, 70.0% / 94.5%;
  2015 993, 69.8% / 94.5%; 2016 1,620, 70.2% / 94.5%; 2017 1,511, 67.8% / 94.5%; 2018 1,475, 69.7% / 94.5%; 2019 2,001, 87.8% / 98.4%.
  The share within 200 km is identical (94.5%) in 2011 and 2013-2018, and the maximum nearest-station distance is 304 km in every year from 2011 on; whether it is the same cells each year was not checked.
- How much of the gap it fills: this archive is the one behind both the 52.48% and the 7.17% figures in section 0. The correction that
  cut the missing share from 52.48% to 7.17% used only this archive, so, on current evidence, the archive plus correct joins fills about
  45 percentage points of the original gap. Whether other CWFIS decade files or `cwfis_fwi2010sopEC.csv` would fill more of the remaining
  7.17% was **not measured**.
- Station count grows from about 100 to about 320 per day between 2011 and 2019 (table above), so coverage is not homogeneous in time.

Tradeoffs:
- Coverage: best measured of the three for gap closure so far (7.17% missing after correction), but uneven by year (2018: 15.98%).
- Availability time and leakage: no per-record publication time (readme and repo registry say so), so every use is a retrospective
  hindcast; the CWFIS readme says 2010-2015 comes from a different CWFIS instance than 2016-2019. The t-1 rule is enforced by the existing builder
  and tests. Provincial station data are excluded by CWFIS (readme).
- Compute and size: 92.2 MB download; reading it with pandas took 22 s on the box (measured); FWI is already computed, so no new equations.
- Licensing: Open Government Licence - Canada, per `https://cwfis.cfs.nrcan.gc.ca/downloads/licence.txt` (tree-wide; fetched), with a requested
  citation to CWFIS.
- Consistency with live HRDPS: the live feed has no CWFIS station FWI; the cache-fallback contract records a CWFIS station feed seen at
  about 11 h old (v0.2.0 text, not re-measured here). Parity between the archive's noon station values and an HRDPS-derived value is **not measured**.

## Option (c): ERA5 as the historical wind source

What it is: hourly 10 m wind (and optionally the other weather variables) from ERA5 or ERA5-Land, either via the Open-Meteo archive
API (used by `capability_2_fire_spread`) or directly from the Copernicus CDS. This addresses wind, not the FWI code gap by itself.

Measured (2026-11-02):
- Open-Meteo archive API, one point (49.95 N, 95.07 W), 2018-05-01 to 2018-10-31, six hourly variables, `models=era5`: HTTP 200, 199,959
  bytes, 2.5 s, 4,416 hourly values per variable, 0 missing; the point snapped to 50.0, -95.0 (about 0.25 degree grid).
- Same request with `models=era5_land`: wind speed, wind direction, gusts and precipitation were all null (4,416 of 4,416), temperature and RH were present, grid
  snapped to 50.0, -95.1. So wind did not come back for ERA5-Land through that endpoint; direct CDS ERA5-Land wind was not tested (needs an account).
- The 9,673 cell centroids fall in 2,078 distinct 0.25 degree boxes and 8,865 distinct 0.1 degree boxes (arithmetic on the grid CSV).
- The repo's spread manifest records `era5_seamless` hours with `hours_missing_wind: 0` for the fires listed (first entries read).

Tradeoffs:
- Coverage: complete in time and space for the sample (0 missing); resolution about 0.25 degree on the API vs a 10 km cell and HRDPS's
  stated 2.5 km, so wind is smoother than the live feed.
- Availability time and leakage: reanalysis values are produced after the fact; hindcast only (repo already labels ERA5 this way). Use only
  values before the local-midnight issue time (t-1 and earlier) in features; ERA5 update latency was **not measured**.
- Compute and size: about 200 KB of JSON per point-season on the API. 2,078 points x 10 seasons would be about 4 GB by this scale factor
  (arithmetic, not downloaded); the free API limit is 10,000 calls/day, 5,000/hour (Open-Meteo terms, fetched). CDS has its own queue/limits, **not measured**.
- Licensing: ERA5 and ERA5-Land are under CC-BY 4.0 on the CDS (dataset page and licence page, fetched; attribution required).
  Open-Meteo's free tier is non-commercial only, with CC-BY 4.0 data; paid plans needed for commercial use.
- Consistency with live HRDPS: HRDPS wind on GeoMet is in m/s (layer title `[m/s]`, measured) and covers only a rolling window
  (time dimension 2026-11-02T18:00Z to 2026-11-04T18:00Z, measured), so GeoMet cannot supply 2010-2019 wind. A model trained on ERA5 wind
  and served with HRDPS wind sees a different resolution and bias; that shift was **not measured**. Wind direction convention in the
  contract is "blowing from"; Open-Meteo's `wind_direction_10m` is also "from" (repo manifest units).

## Side by side

| | (a) ECCC + own FWI | (b) CWFIS archive | (c) ERA5 wind |
| --- | --- | --- | --- |
| Fixes | FWI/weather gap | FWI/weather gap (already used) | wind only |
| Measured missing/coverage | 96-100% of cells within 200 km of complete noon station on 15 July samples | 7.17% rows missing after correction (repo audit) | 0 of 4,416 hours missing at one point |
| New code | FWI chain, station interpolation | none for FWI | grid/point sampling |
| Provenance time | observation valid_time known | no per-record publication time | reanalysis, hindcast |
| Licence | not verified | OGL-Canada (tree-wide) | CC-BY 4.0 (Open-Meteo free tier non-commercial) |
| Live HRDPS parity | observation vs forecast gap | station FWI not in live feed | resolution/bias gap, unmeasured |

The table is the pre-decision comparison; ATLAS recommended nothing in it. Open questions that remain after the decision: whether a hybrid (b) for FWI plus (c) for wind is acceptable, whether
2017-2018's residual gap needs its own diagnosis, and whether the contract's per-cell time zone item (north-west Ontario on Central time)
affects the LST noon definition for stations west of 90 W.
