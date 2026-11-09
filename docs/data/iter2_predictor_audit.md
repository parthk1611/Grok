# Iteration #2: candidate predictor audit (ATLAS, draft for Team Lead approval)

Status: **desk audit only. Nothing has been ingested.** Prepared 2026-11-09 (America/Toronto) on `main` @ `d07482f`.
Scope: free sources only, inputs must be known before the issue instant (local midnight America/Toronto of the target day),
and dev 2010-2016 / calibration 2017 / threshold 2018 / locked test 2019 must all be coverable. Every fact below cites the
page it came from; anything not confirmed from the source itself is marked **unverified**.

As AGENTS.md already says, the historical CWFIS/NFDB archives have no per-record publication timestamps, so any feature
built from a retrospective archive is a **hindcast**, not an operational replay. The same caveat applies to every candidate here.

## Summary

| # | Candidate | Source | Licence | History starts | Publication delay | At-issue rule | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | Recent burn history (area burned in a cell in the last 1-10 fire seasons) | CWFIS NFDB fire polygons, `NFDB_poly.zip` | Open Government Licence - Canada (unverified on the download page itself; NFDB points already used under it) | Decades before 2010 (NFDB) | Seasonal compilation; no per-record timestamp | Only polygons with fire year strictly < target year | **Defensible, cheapest. Recommended for the one Thu test.** |
| 2 | Vegetation greenness (NDVI/EVI, and anomaly vs a training-only climatology) | NASA MODIS MOD13A1 v6.1, 500 m, 16-day | NASA open data, no restrictions on use or redistribution | 2000-02-18 | 16-day composites generally appear about 16 days after observation (LAADS) | Composite whose end date + 16 days < issue | Defensible but moderate cost (HDF tiles, QA masking, 9,673-cell aggregation). Candidate for after iteration #2. |
| 3 | Fuel / forest attributes | FBP 2014b (fires <= 2014), SCANFI v2 5-year epochs strictly before the fire year | OGL - Canada | FBP 2014b; SCANFI epochs 1985-2025 | All SCANFI v2 maps published 2026 | Rule already in the ledger: `5*floor((year-1)/5)` | Defensible, **blocked on the SCANFI-to-FBP crosswalk** I still owe. Not for iteration #2. |
| 4 | Human access: road density / distance to road | StatCan Road Network File vintages (2009, 2011, 2016 ...) or NRN Ontario | OGL - Canada | RNF 2009 vintage listed | 2016 RNF published 2016-11-16 (portal); other vintages' dates unverified | Latest vintage published before issue date | Defensible only with dated vintages; **current NRN must not be backfilled**. Resource/forest roads in the north are likely under-covered (unverified), which is itself a fairness issue. Moderate cost. |
| 5 | Population / settlement | StatCan Census (2006, 2011, 2016) population counts + dissemination block boundaries | Statistics Canada Open Licence / OGL - Canada | 2006 census | 2011 counts released 2012-02-08; 2016 counts 2017-02-08 | Latest census whose counts were released before the issue date (2006 census for dates before 2012-02-08) | Defensible, moderate cost. |
| 6 | Lightning | CLDN (ECCC/Vaisala) | **Not free**: archive is quote-based via ec.rcdf-cldn.ec@canada.ca | 1998 | n/a | n/a | **Not usable.** |
| 6a | Lightning (free) | MSC lightning flash density, 10 min, ~2.5 km (Datamart/GeoMet) | ECCC open data licence | Real-time only; ECCC has no online archive (retrieval is cost-recovered at $118/h) | ~10 min | n/a for 2010-2019 | **Not usable for training**; possible future live-only feature. |
| 6b | Lightning (free) | NOAA GOES GLM L2 | US Government work, public domain | Public from 2017-07-05 (NCEI); first public GOES-16 file found 2018-02-13 by one third-party listing | Near-real-time | n/a | **Not usable**: no 2010-2016 dev history, and GLM does not see above ~55 deg N, so Ontario's Far North is excluded (a coverage bias). |

Sources: [NFDB polygons](https://cwfis.cfs.nrcan.gc.ca/downloads/nfdb/fire_poly/current_version/NFDB_poly.zip) (HEAD 200, 778,498,701 bytes on 2026-11-09);
[MOD13A1 v6.1](https://www.earthdata.nasa.gov/data/catalog/lpcloud-mod13a1-061); [LAADS latency and data policy](https://ladsweb.nascom.nasa.gov/learn/);
[SCANFI ledger](../../data/metadata/source_availability_ledger_v1.json); [2016 Census RNF](https://open.canada.ca/data/en/dataset/57d5ffae-3048-4a19-9b4c-eab12f6322c5);
[RNF 2009 Ontario](https://open.canada.ca/data/en/dataset/084b8d63-88e2-4de4-b744-32f82b68bf34); [NRN](https://open.canada.ca/data/en/dataset/3d282116-e556-400c-9306-ca1a3cada77f) (Ontario GPKG HEAD 200, 507,430,394 bytes);
[2011 Census dissemination](https://www12.statcan.gc.ca/census-recensement/2011/ref/overview-apercu/pop10-eng.cfm); [2016 Census dissemination](https://www12.statcan.gc.ca/census-recensement/2016/ref/98-304/chap11-eng.cfm);
[MSC lightning readme](https://eccc-msc.github.io/open-data/msc-data/lightning/readme_lightning_en/); [MSC FAQ (CLDN purchase, archive retrieval)](https://eccc-msc.github.io/open-data/faq/readme_en/);
[GOES ABI/GLM at NCEI](https://www.ncei.noaa.gov/products/goes-terrestrial-weather-abi-glm). The ~55 deg N GLM limit and the 2018-02-13 first-file date come from my earlier search notes and are **unverified** against a primary page.
The StatCan RNF zip URLs returned HTTP 403 to a HEAD request from my computer, which looks like a bot block; their availability is from the portal listing only.

## Notes per candidate

1. **Recent burn history.** Prior-season burned area acts as a fuel break and a proxy for young stands. It uses the same NFDB
   family we already depend on, it's dated by fire year, and it needs no new time alignment beyond "strictly earlier fire year".
   Polygon dates and mapping completeness vary by agency and year (to be quantified in the coverage analysis). Known risk: it
   is correlated with the existing `fire_history_*` features, so EMBER should report the gain over the v1 panel, not raw importance.
2. **NDVI/EVI.** The v6.1 collection was reprocessed (published 2021-02-16), so historical values are retrospective, not the
   values available at the time. Cloud/snow QA masking is required; early May is snow-affected in the north. A climatology for
   anomalies must be fitted on the training partition only.
3. **Fuel.** No change to the approved rule. The fires >= 2015 have no FBP fuel type until the crosswalk exists.
4. **Roads.** Using today's NRN for 2010 would leak roads built later; only dated vintages are allowed. Whether RNF/NRN include
   Ontario forest-access roads is **unverified** and must be checked before use.
5. **Population.** Census counts are point-in-time; applying the 2016 census before 2017-02-08 is leakage. Dissemination-block
   counts would be areal-weighted to the 10 km grid.
6. **Lightning.** NFDB's cause field (lightning vs human) is already in the label data and can be used to split evaluation by
   cause; that is an analysis, not a new predictor.

## Recommendation

- If Team Lead approves one cheap test for Thursday (dev data only): **#1 recent burn history from NFDB polygons**.
- Document #2, #4 and #5 in the report as the next step, with the at-issue rules above.
- #3 waits for the crosswalk. #6 is out: no free history covering 2010-2019.
- Approval needed from Team Lead before any download or ingestion.
