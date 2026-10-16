# Ignition data quality report

Status: **PASS**. Full scan: **True**. No rows dropped, changed, imputed or fitted.

Source: `ontario_fire_region_10km_daily_ignition_features_v1.csv`; 6,542,580,992 bytes; SHA-256 `8791c96e460e6294791bbbf1ad0d6aad68e31a98be28dbcc6060463f94ccfc9d`.

Scanned 17,798,320 rows, 48 columns, 7,785 positive cell-days, 8,109 event records and 318 large-fire labels. Ignition prevalence: 0.043740%.

| Year | Rows | Ignitions | Weather missing | Rain 7-day missing | History censored |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2010 | 1,779,832 | 661 | 482,517 (27.1103%) | 536,694 | 1,709,665 |
| 2011 | 1,779,832 | 1,245 | 555,552 (31.2137%) | 663,609 | 1,602,331 |
| 2012 | 1,779,832 | 1,407 | 653,789 (36.7332%) | 721,554 | 1,456,567 |
| 2013 | 1,779,832 | 527 | 654,991 (36.8007%) | 728,274 | 1,378,835 |
| 2014 | 1,779,832 | 293 | 518,114 (29.1103%) | 622,198 | 1,353,829 |
| 2015 | 1,779,832 | 584 | 1,779,816 (99.9991%) | 1,779,832 | 1,325,567 |
| 2016 | 1,779,832 | 573 | 1,753,712 (98.5324%) | 1,779,832 | 1,294,810 |
| 2017 | 1,779,832 | 736 | 1,297,588 (72.9051%) | 1,722,246 | 1,257,565 |
| 2018 | 1,779,832 | 1,250 | 1,199,346 (67.3853%) | 1,769,369 | 1,191,572 |
| 2019 | 1,779,832 | 509 | 445,491 (25.0299%) | 820,070 | 1,152,851 |

Weather missing means no usable selected prior-day station. Seven-day precipitation is blank unless all seven completed observations are available. Blank days-since-fire means no earlier observed same-cell event in the available 2010+ history; it is censoring, not zero days or weather missingness.

Validation: 277 listed checks; 0 total check violations (a cell may trigger multiple checks). Exact counts and all-column null counts are in the JSON.

Additional checks beyond the EDA include explicit numeric token conversion failures, float32 overflow and integer precision, required-field nulls, all quality fractions, terrain ranges/static consistency, calendar cycles/weekend/day length, and cross-field history ordering.

Memory: chunk limit 50,000; peak process working set 249.8 MiB. The full 36-predictor float32 matrix alone needs 2.39 GiB, before copies, indicators or model workspace. Use bounded training samples or suitably provisioned infrastructure.

Audit limits:

- Checks validate the downloaded panel and internal contracts; NFDB labels/history, raw station selection, and precipitation sums are not independently rebuilt.
- Archives lack per-record historical available_at timestamps. This is a retrospective hindcast, not an operational forecast replay.
- The station candidate set and study-period grid universe may introduce selection effects; full source-level sensitivity analysis remains outstanding.
- Numeric ranges are reported after float32 conversion; token conversion, infinities, integer validity and overflow were checked at original float64 precision first. Continuous-value float32 rounding is expected.
- No rows are deleted, no imputation/scaling or model fitting is performed; invalid data cause FAIL. Source parsing/schema exceptions terminate the CLI with a nonzero exit.
- Peak working set covers this process only, including imports/parser/transient arrays. Model matrix estimates exclude copies, missingness indicators, transformed arrays and fitting workspace.
