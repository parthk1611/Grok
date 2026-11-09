# COMP-385 iteration #2 report: outline

Status: OUTLINE ONLY (BEACON draft for Team Lead review). This is not the report, and it contains no metric values.
Every result slot is a placeholder of the form `[TBD: <lane> <design> run <id>]`. Iteration #1 results are frozen
and are cited only as "frozen iteration #1 results (run ID TBD)". Iteration #2 results come from the new evaluation
design **E2** under new run IDs. The two are never mixed in one table or figure. By Team Lead constraint, the report
has no executive summary, conclusions or recommendations section, and makes no "completed Weeks X" claims.

Terminology follows the repo: Ontario Fire Region 10 km grid (`ofr10km-v1`, EPSG:3978), fire-season cell/day
`ignition` label, retrospective hindcast (not an operational forecast), contracts v0.2.1 (draft), and the
research-estimate disclaimer in the contract schemas.

## 1. Introduction and scope
- The two capabilities: cell/day ignition risk on `ofr10km-v1` and wind-driven fire spread (`capability_2_fire_spread/`).
- Mode labelling: outputs are `retrospective`/`hindcast`. Nothing here is an operational forecast or a replacement for official alerts.
- Scope of iteration #2: E2 evaluation, predictor audit, spread uncertainty bands, live ingestion, and dashboard labelling.
- Owner: Team Lead (BEACON drafts). Source: `README.md`, `AGENTS.md`, `contracts/README.md`.

## 2. Changes since iteration #1 (professor feedback)
- Feedback themes (our paraphrase), each mapped to the section that answers it:
  - Weakest areas in iteration #1 were model development, ethics and validation: sections 3, 4, 8.
  - No executive summary, conclusions, recommendations or week-completion claims at this stage: the structure of this report.
  - Express alert budgets as cells flagged per day and false-alert burden, and do not present a calibrated or mapped probability as actionable: sections 5, 7.
  - Stronger geographic testing, with several held-out regions and years kept as intact spatial blocks and resampling by fire, region or time block rather than by day: section 4.
  - Audit dated, issue-time-valid predictors (fuel/vegetation, lightning, human access) against the baselines before adding model complexity, and check stability across years, regions and negative-sampling seeds: sections 3, 5.
  - Assess geographic and data-coverage fairness and community impacts: section 8.
  - Keep iteration #1 results frozen and run new experiments under a new evaluation design: sections 2, 4, 10.
- Iteration #1 weaknesses that motivate E2 are cited from the frozen iteration #1 results (run ID TBD), not re-measured. `[TBD: Team Lead confirm which frozen figures to cite]`
- Iteration #1 results stay frozen and are cited as "frozen iteration #1 results (run ID TBD)", with no figures re-quoted.
- What E2 changes relative to the iteration #1 protocol: the split design, uncertainty estimation and operational budgets. Gaps already listed under "Limits" in `docs/modeling/final_report.md` are named here, without numbers.
- Owner: Team Lead. Source: feedback notes, `docs/modeling/evaluation_protocol.md`.

## 3. Data and predictor audit
- Existing inputs: CWFIS prior-day weather/FWI, static CDEM terrain, and past-only ignition history. Time rules: issue-time/t-1 cutoff and `America/Toronto` local date.
- Candidate predictors are audited for validity at issue time: fuel/vegetation condition, lightning, roads and population.
- The audit table has one row per candidate with columns for source, licence, history start, publication delay and defensibility. `[TBD: ATLAS audit table]`
- Candidates are not ingested until the Team Lead approves them. The status of each one is reported here.
- Owner: ATLAS. Source: `docs/ignition_features.md`, `docs/data/*` (alignment v1, T1.0, T1.1 notes, fuel ledger).

## 4. E2 evaluation design
- Spatial-block geographic testing that holds out several regions and several years. Block definitions: `[TBD: EMBER E2 block spec]`
- Group-resampling uncertainty by fire, by region and by time block, with the resampling scheme and replicate count. `[TBD: EMBER E2 spec]`
- At least 5 negative-sampling seeds, reporting the spread across seeds plus year-by-year and region-by-region stability.
- Operational alert budgets of 0.5/1/2/5% of cell-days, fixed before the test data is scored. Freeze rules are carried over from `evaluation_protocol.md`.
- Owner: EMBER. Source: `docs/modeling/evaluation_protocol.md`, E2 config/freeze files `[TBD: EMBER E2 run <id>]`

## 5. Ignition results (E2; placeholders only)
- AP, precision and recall, with alert-budget results at 0.5/1/2/5% shown alongside. `[TBD: EMBER E2 run <id>]`
- Per held-out block: regional and yearly results with group-resampled intervals. `[TBD: EMBER E2 run <id>]`
- Seed spread across at least 5 negative-sampling seeds, plus stability tables. `[TBD: EMBER E2 run <id>]`
- False-alert burden: false alerts per day and false alerts per true detection at each budget. `[TBD: EMBER E2 run <id>]`
- Owner: EMBER. Dependency: sections 3 and 4 frozen first. No comparison against iteration #1 figures.

## 6. Fire spread status
- A Rothermel-based Monte Carlo produces burn-probability bands at 1/3/6 h. The method and provisional input sigmas follow `spread_montecarlo.md`.
- Single run (`n_runs`=1): the unperturbed base footprint with `uncertainty_status` `not_estimated` (the dashboard draws p50 only).
- Replay metrics, all placeholders: IoU, area error, direction error and perimeter distance. `[TBD: BLAZE replay run <id>]`
- Replay scope for iteration #2: T2.5 replay covers fires up to 2014 only, because fuel data for 2015+ fires is missing and no 2024 fallback is used. The SCANFI crosswalk follows the predictor audit (no ETA).
- Known limits: EPSG:3978 area/perimeter scale error, grid wind-azimuth bias, and the contract gaps listed by BLAZE.
- Owner: BLAZE. Source: `capability_2_fire_spread/docs/*`, `scripts/run_spread_replay.py`.

## 7. System and dashboard
- RELAY:
  - 15-minute ingestion.
  - Stale-cache fallback using the two-age model (`fresh`/`stale`/`expired`).
  - Closed error bodies and the 409 confirmation flow.
  - Contracts v0.2.1, moving to v0.3.0 (CR-4 `mock`, CR-6 `stale_sources`; pending approval).
- BEACON dashboard (already built):
  - stale-data banner and MOCK labelling
  - timestamp and model-version labels
  - the disclaimer
  - the manual weather override marked "not live data".
- The scores on the map are relative rankings. The report must not present any calibrated probability as actionable.
- Shown next to the map: cells flagged per day and the false-alert burden (false alerts per day, false alerts per true detection at 0.5/1/2/5%). Values come only from EMBER's measured E2 back-test and are shown as **"not yet measured"** until they exist.
- Owners: RELAY (API/ingestion), BEACON (dashboard). Source: `contracts/`, `frontend/README.md`, `[TBD: EMBER E2 run <id>]`

## 8. Ethics, fairness and community impact
- Geographic and data-coverage fairness:
  - detection and record coverage in the far north vs the south
  - weather-station density
  - results by distance to communities, including First Nations communities.
  `[TBD: ATLAS coverage analysis]`
- Impacts on affected communities: false-alert burden, missed fires, over-reliance on a research estimate, and alert fatigue.
- Safeguards:
  - the research-estimate disclaimer ("does not replace official fire alerts, warnings or evacuation orders")
  - mode, stale and MOCK labels
  - no personal or evacuee data is collected or used.
- Stated limits: under-recorded areas can look "low risk", and coverage gaps are reported, not corrected away.
- Owners: ATLAS (coverage analysis), BEACON (draft), Team Lead (sign-off).

## 9. Limitations and threats to validity
- Retrospective hindcast. The edge-cell selection of the frozen grid uses study-period events. Archive publication/revision times are unknown.
- Label bias from uneven detection and reporting; censored history start.
- E2-specific threats: block leakage across borders, seed sensitivity, and budget choice. `[TBD: EMBER E2 run <id>]`
- Spread: replay limited to fires up to 2014 (no fuel for 2015+), provisional sigmas, gaps in fuel crosswalk and resolution, and replay-target quality.
- Owner: all lanes (BEACON compiles).

## 10. Reproducibility
- Run-ID registry with separate tables for frozen iteration #1 (run ID TBD) and E2 (new IDs). `[TBD: run IDs]`
- Seeds: model `SEED = 385`, plus the E2 negative-sampling seeds `[TBD: EMBER]` and the spread Monte Carlo seeds `[TBD: BLAZE]`.
- Commands, code fingerprints, pipeline checksums and manifest-pinned archives, as listed in the configuration/freeze files.
- Contract version and commit hash for each artifact.
- Owner: all lanes. Source: freeze/config files, `docs/modeling/final_report.md` (Artifacts and reproduction).

## Appendix
- A: Predictor audit table in full (ATLAS).
- B: E2 per-block, per-year and per-seed tables (EMBER).
- C: Spread replay case list and figures (BLAZE).
- D: API contract summary and changelog v0.2.1 to v0.3.0 (RELAY).
- E: Dashboard screenshots with MOCK/stale labels visible (BEACON).
