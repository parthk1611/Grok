"""Render saved execution artifacts; never train or select using test results."""
import argparse
import base64
import html
import json
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .contract import FEATURE_NAMES, ROOT

def render(run,output):
    run,output=Path(run),Path(output);output.mkdir(parents=True,exist_ok=True)
    comparison=json.loads((run/"development_comparison.json").read_text())
    test=json.loads((run/"final_test.json").read_text())
    freeze=json.loads((run/"final_freeze.json").read_text())
    quality=json.loads((ROOT/"docs/modeling/data_quality_report.json").read_text())
    weather=json.loads((ROOT/"docs/modeling/corrected_weather_audit.json").read_text())
    thresholds=pd.read_csv(run/"threshold_tradeoffs_2018.csv")
    selected=freeze["selected"]
    summary=pd.DataFrame([{k:v for k,v in r.items() if k!="fold_average_precision"}
                          for r in comparison["candidate_summary"]])
    summary["params"]=summary.params.map(lambda p:json.dumps(p,sort_keys=True))
    summary.to_csv(output/"development_comparison.csv",index=False)
    final_tradeoffs=pd.DataFrame(test["threshold_tradeoffs"])
    final_tradeoffs.to_csv(output/"test_threshold_tradeoffs.csv",index=False)
    thresholds.to_csv(output/"threshold_tradeoffs_2018.csv",index=False)
    for name in ["configuration.json","development_comparison.json","final_freeze.json",
                 "calibration_decision.json","final_test.json","final_logistic_training.json","final_hgb_training.json"]:
        shutil.copyfile(run/name,output/name)
    curves=run/"curves"
    pr=pd.read_csv(curves/"test_2019_precision_recall.csv")
    calibration=pd.DataFrame(test["quantile_reliability_bins"])
    figure,axes=plt.subplots(1,3,figsize=(16,4.5),layout="constrained")
    labels=[f"{r['family']} {r['candidate']}" for r in comparison["candidate_summary"]]
    axes[0].barh(labels,summary.mean_fold_average_precision,color=["#2166ac" if x.startswith("logistic") else "#238b45" for x in labels])
    axes[0].set(xlabel="Mean AP across 2012–2016 validation years",title="Development comparison")
    axes[1].plot(pr.recall,pr.precision,color="#238b45")
    axes[1].axhline(test["prevalence"],color="#666",linestyle="--",label="Test prevalence")
    axes[1].set(xlabel="Recall",ylabel="Precision",title="Frozen selected system: 2019",ylim=(0,1),xlim=(0,1))
    axes[1].legend()
    axes[2].plot(calibration.mean_probability,calibration.observed_prevalence,"o-",color="#2166ac")
    limit=max(calibration.mean_probability.max(),calibration.observed_prevalence.max())*1.05
    axes[2].plot([0,limit],[0,limit],"--",color="#666")
    axes[2].set(xlabel="Mean predicted probability",ylabel="Observed ignition rate",title="Calibration: score-quantile bins")
    figure.savefig(output/"model_results.png",dpi=160);plt.close(figure)
    n=weather["rows"]
    corrected_missing=sum(r["weather_missing"] for r in weather["annual"].values())/n
    rain_missing=sum(r["rain_7d_missing"] for r in weather["annual"].values())/n
    best={family:max((r for r in comparison["candidate_summary"] if r["family"]==family),key=lambda r:r["mean_fold_average_precision"])
          for family in ["logistic","hgb"]}
    md=["# Executed ignition model comparison", "",
        f"Selected **{selected['family']}**, candidate {selected['candidate']}, by mean development AP **{selected['mean_fold_average_precision']:.6f}**. Frozen 2019 test AP **{test['average_precision']:.6f}**, against test prevalence **{test['prevalence']:.6f}**.","",
        "## What was executed", "",
        "Nine roles completed discovery, source auditing, split design, preprocessing, two model implementations, evaluation and independent verification. Full original-panel validation passed 277 checks. Both model families were fitted on every training row across five expanding chronological folds; no resampling or validation/test rebalancing was used. Logistic regression uses averaged SGD with logistic loss and L2 regularization to keep memory bounded. Three epochs are a declared optimization budget and do not prove convergence. HGB uses native NaN, class weights once and external chronological iteration selection; internal early stopping is disabled.","",
        "## Data and feature contract", "",
        f"The original panel contains {n:,} rows and {quality['totals']['positive_cell_days']:,} positive cell-days. Its daily weather missingness was52.48%; the model cache corrects future-station selection and unsorted archive joins. Corrected daily weather missingness is **{corrected_missing:.2%}**, and prior-seven-day rain missingness **{rain_missing:.2%}**. All original rows/labels/calendar/terrain/history are preserved. Cache version: `{weather['cache_version']}`.","",
        "Inputs originate from exactly36 approved predictors. Logistic preprocessing adds21 fixed missing/censor indicators (57 transformed columns); HGB adds only the history-censor indicator (37 columns). IDs/dates/source metadata and all three label fields are excluded. Training-fold medians/scales are fitted inside each fold. Entirely missing nullable training columns use0 as a numerical fallback with an explicit indicator, never as a statement that censored fire history equals zero days.","",
        "## Frozen partitions", "",
        "| Partition | Local dates | Rows | Positive cell-days |", "|---|---|---:|---:|",
        "| Development/final base-model fit |2010-05-01–2016-10-31 (May–October)|12,458,824|5,290|",
        "| Calibration fit |2017-05-01–2017-10-31|1,779,832|736|",
        "| Calibration choice/threshold tradeoffs |2018-05-01–2018-10-31|1,779,832|1,250|",
        "| Locked final test |2019-05-01–2019-10-31|1,779,832|509|","",
        "Validation years2012–2016 each train on2010 through the preceding year. Whole dates remain together. The target horizon is one local day beginning at00:00 America/Toronto; weather/history are prior-date inputs, and label windows do not overlap, so purge gap is0.","",
        "## Development results", "",
        "| Family | Best mean validation AP | Configuration |", "|---|---:|---|",
        *[f"|{family}|{r['mean_fold_average_precision']:.6f}|`{json.dumps(r['params'],sort_keys=True)}`|" for family,r in best.items()],"",
        "AP, the ranking criterion, uses the full natural-prevalence validation sets; prevalence is the no-feature ranking reference. Model choices use development data only.","",
        "## Single frozen final evaluation", "",
        f"AP={test['average_precision']:.6f}; ROC-AUC={test['roc_auc']:.6f}; Brier={test['brier_score']:.8f}; log loss={test['log_loss']:.8f}. Baseline AP={test['training_rate_baseline']['average_precision']:.6f}. Sigmoid calibration used: **{freeze['calibration_used']}**; fitted only on2017 and compared against raw scores on2018. Final selected system was frozen before2019 predictions.","",
        "No operating threshold is selected because no alert budget/error costs were specified. The saved2018 score-quantile thresholds define tradeoffs; the same thresholds were evaluated unchanged on2019. False alerts are negative cell-days flagged, reported per10,000 evaluated cell-days and per distinct local date. Counts and precision/recall are in `test_threshold_tradeoffs.csv`.","",
        "![Development comparison, precision–recall and calibration](model_results.png)","",
        "## Artifacts and reproduction", "",
        "Tracked here: feature/source/split/gate/verification reports, execution configuration, development comparison, calibration decision, final freeze/results and charts. Generated cache/model/prediction binaries remain in ignored `data/interim/ignition_modeling_cache/` and `artifacts/ignition_modeling/full_run/`. The saved `selected_system.joblib` contains the complete fitted pipeline, optional calibrator, exact schema and frozen thresholds. Reload with joblib and predict using `scripts.ignition_modeling.run.predict_bundle`; inputs must follow the approved36-column order and the corrected past-only feature rules.","",
        "```powershell", "python -m pip install -r requirements-modeling.txt",
        "python -m scripts.ignition_modeling.validation --input '<original audited CSV>' --chunk-size 50000",
        "python -m scripts.ignition_modeling.data --input '<original audited CSV>'",
        "python -m unittest discover -s tests -v",
        "python -m scripts.ignition_modeling.run --smoke --output artifacts/ignition_modeling/smoke",
        "python -m scripts.ignition_modeling.run",
        "python -m scripts.ignition_modeling.report", "```","",
        "Recover the manifest-pinned CWFIS archive before rebuilding the cache. The training gate requires the full data, leakage and corrected-cache verification described in the accompanying reports. Existing final-test output is never overwritten; a correctness-defect rerun requires a new output directory and disclosure. Exact versions/seeds/code fingerprints and pipeline checksums are saved in configuration/freeze files.","",
        "## Limits", "",
        "This is a retrospective hindcast on the frozen v1 grid, whose edge-cell selection uses study-period events. Archive publication/revision times are unknown, and static terrain is a retrospective approximation. Results do not establish operational forecasts, unbiased performance outside this grid, future climate/station coverage performance, or HRDPS readiness. Censored history begins in2010. Spatial-block generalization and date-dependent uncertainty estimates were not executed. Optimization is bounded, and the small candidate search is not exhaustive. No test-driven tuning was performed."]
    (output/"final_report.md").write_text("\n".join(md)+"\n",encoding="utf-8")
    image=base64.b64encode((output/"model_results.png").read_bytes()).decode()
    body="<html><head><meta charset='utf-8'><title>Ignition model comparison</title><style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:0 20px;color:#172b3a}img{width:100%}pre{white-space:pre-wrap;line-height:1.5}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd}</style></head><body>"
    body+="<h1>Executed ignition comparison</h1><img src='data:image/png;base64,"+image+"'><pre>"+html.escape("\n".join(md))+"</pre></body></html>"
    (output/"report.html").write_text(body,encoding="utf-8")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",type=Path,default=ROOT/"artifacts/ignition_modeling/full_run")
    parser.add_argument("--output",type=Path,default=ROOT/"docs/modeling")
    args=parser.parse_args();render(args.run,args.output)
