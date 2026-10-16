"""Bounded full-row chronological development and a single frozen final test."""
import os
os.environ.setdefault("OMP_NUM_THREADS","2")
os.environ.setdefault("OPENBLAS_NUM_THREADS","2")

import argparse
import csv
import gc
import json
import platform
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from .contract import FEATURE_NAMES, ROOT, SEED
from .data import dates_for, load_cache, year_slice
from .evaluation import evaluate_predictions, save_curves, threshold_tradeoffs
from .splits import FOLD_YEARS
from .weather import sha256

LOGISTIC_CANDIDATES=[{"alpha":a,"class_weight":w,"epochs":3,"batch_size":50000}
                     for a in [1e-4,1e-3] for w in [None,"balanced"]]
HGB_CANDIDATES=[{"max_iter":n,"class_weight":w,"learning_rate":0.1,
                "max_leaf_nodes":15,"min_samples_leaf":100,"l2_regularization":1.0}
                for n in [20,40] for w in [None,"balanced"]]

def write_json(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(str(path)+".tmp")
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
    temporary.replace(path)

def write_csv(path,records):
    with Path(path).open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(records[0]))
        writer.writeheader();writer.writerows(records)

def predict_chunks(pipeline,X,batch_size=50000):
    p=np.empty(len(X))
    for start in range(0,len(X),batch_size):
        p[start:start+batch_size]=pipeline.predict_proba(X[start:start+batch_size])[:,1]
    if not np.isfinite(p).all():
        raise ValueError("Prediction contains nonfinite probabilities")
    return p

def logit(p):
    p=np.clip(p,1e-12,1-1e-12)
    return np.log(p)-np.log1p(-p)

def predict_bundle(bundle,X):
    if bundle["features"]!=list(FEATURE_NAMES):
        raise ValueError("Saved feature schema mismatch")
    p=predict_chunks(bundle["pipeline"],X)
    if bundle["calibrator"] is not None:
        p=bundle["calibrator"].predict_proba(logit(p).reshape(-1,1))[:,1]
    return p

def train_model(family,X,y,params,scratch,preprocessor=None):
    if family=="logistic":
        from .logistic import train_logistic
        return train_logistic(X,y,params,seed=SEED,preprocessor=preprocessor)
    from .histogram import train_hgb
    return train_hgb(X,y,params,scratch_dir=scratch,seed=SEED)

def metric_record(family,index,fold,params,metrics,diagnostics):
    return {"family":family,"candidate":index,"fold":fold,"params":params,
            "metrics":metrics,"diagnostics":diagnostics}

def check_gate(manifest):
    gate=json.loads((ROOT/"docs/modeling/training_gate.json").read_text())
    if gate["status"]!="PASS" or gate["cache_version"]!=manifest["cache_version"]:
        raise ValueError("Data/provenance/verification training gate has not passed")
    if gate["original_panel_sha256"]!=manifest["original_panel_sha256"]:
        raise ValueError("Gate refers to another original panel")

def execute(cache,output,smoke=False):
    from .preprocessing import NumericPreprocessor
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    X,y,manifest=load_cache(cache);check_gate(manifest)
    if (output/"final_test.json").exists():
        raise FileExistsError("Final test already executed; preserve this run and disclose any correctness-defect rerun in a new output directory")
    packages={n:version(n) for n in ["numpy","pandas","scipy","scikit-learn","joblib","threadpoolctl","psutil","matplotlib"]}
    configuration={"seed":SEED,"python":platform.python_version(),"packages":packages,
                   "cache_version":manifest["cache_version"],"features":list(FEATURE_NAMES),
                   "original_panel_sha256":manifest["original_panel_sha256"],
                   "cache_manifest_sha256":sha256(Path(cache)/"manifest.json"),
                   "fold_years":FOLD_YEARS,"logistic_candidates":LOGISTIC_CANDIDATES,
                   "hgb_candidates":HGB_CANDIDATES,"resampling":False,"threads":2,
                   "source_code_sha256":{p.name:sha256(p) for p in Path(__file__).parent.glob("*.py")}}
    config_path=output/"configuration.json"
    if config_path.exists():
        previous=json.loads(config_path.read_text())
        if json.dumps(previous,sort_keys=True)!=json.dumps(configuration,sort_keys=True):
            raise ValueError("Existing run configuration/code/cache differs; use a new output directory")
    write_json(config_path,configuration)
    if smoke:
        train=slice(manifest["date_slices"]["2010-05-01"][0],manifest["date_slices"]["2010-06-30"][1])
        valid=slice(manifest["date_slices"]["2011-05-01"][0],manifest["date_slices"]["2011-05-31"][1])
        if y[train].sum()<30 or y[valid].sum()<30:
            raise ValueError("Chronological smoke partitions have fewer than30positives")
        results=[]
        for family,params in [("logistic",{**LOGISTIC_CANDIDATES[0],"epochs":1}),
                              ("hgb",{**HGB_CANDIDATES[0],"max_iter":5})]:
            pipeline,diag=train_model(family,X[train],y[train],params,output/"scratch")
            p=predict_chunks(pipeline,X[valid])
            joblib.dump(pipeline,output/f"smoke_{family}.joblib")
            reloaded=joblib.load(output/f"smoke_{family}.joblib")
            np.testing.assert_allclose(p[:1000],predict_chunks(reloaded,X[valid][:1000]),rtol=0,atol=1e-14)
            results.append(metric_record(family,0,"smoke_2010_to_2011",params,evaluate_predictions(y[valid],p),diag))
        write_json(output/"smoke_results.json",results)
        print("Chronological smoke and artifact reload PASS",flush=True)
        return
    records=[]
    for first,last,validation in FOLD_YEARS:
        train=year_slice(manifest,first,last);valid=year_slice(manifest,validation)
        print(f"Development fold {first}-{last} -> {validation}; all {train.stop-train.start:,} training rows",flush=True)
        preprocessor=NumericPreprocessor().fit(X[train])
        for family,candidates in [("logistic",LOGISTIC_CANDIDATES),("hgb",HGB_CANDIDATES)]:
            for index,params in enumerate(candidates):
                path=output/f"fold_{validation}_{family}_{index}.json"
                if path.exists():
                    record=json.loads(path.read_text())
                    if record["params"]!=params or record["fold"]!=validation or not path.with_suffix(".npz").exists():
                        raise ValueError("Fold checkpoint or prediction artifact mismatch")
                else:
                    pipeline,diag=train_model(family,X[train],y[train],params,output/"scratch",preprocessor)
                    p=predict_chunks(pipeline,X[valid])
                    metrics=evaluate_predictions(y[valid],p,dates=dates_for(manifest,valid),
                        weather_missing=X[valid,FEATURE_NAMES.index("cwfis_missing")])
                    np.savez_compressed(output/f"fold_{validation}_{family}_{index}.npz",y=y[valid],p=p)
                    record=metric_record(family,index,validation,params,metrics,diag)
                    write_json(path,record)
                    print(f"{family} candidate{index}: AP={metrics['average_precision']:.6f}",flush=True)
                    del pipeline,p;gc.collect()
                records.append(record)
        del preprocessor;gc.collect()
    summaries=[]
    for family,candidates in [("logistic",LOGISTIC_CANDIDATES),("hgb",HGB_CANDIDATES)]:
        for index,params in enumerate(candidates):
            rows=[r for r in records if r["family"]==family and r["candidate"]==index]
            summaries.append({"family":family,"candidate":index,"params":params,
                              "mean_fold_average_precision":float(np.mean([r["metrics"]["average_precision"] for r in rows])),
                              "fold_average_precision":{str(r["fold"]):r["metrics"]["average_precision"] for r in rows}})
    winner=max(summaries,key=lambda r:r["mean_fold_average_precision"])
    write_json(output/"development_comparison.json",{"fold_results":records,"candidate_summary":summaries,"selected":winner,
        "selection_rule":"Highest unweighted mean AP across five development years; ties use configuration order"})
    final_train=year_slice(manifest,2010,2016)
    families={}
    for family in ["logistic","hgb"]:
        selected=max((r for r in summaries if r["family"]==family),key=lambda r:r["mean_fold_average_precision"])
        pipeline,diag=train_model(family,X[final_train],y[final_train],selected["params"],output/"scratch")
        joblib.dump(pipeline,output/f"final_{family}.joblib")
        write_json(output/f"final_{family}_training.json",diag)
        families[family]=pipeline
    pipeline=families[winner["family"]]
    calibration=year_slice(manifest,2017);threshold=year_slice(manifest,2018)
    raw_cal=predict_chunks(pipeline,X[calibration]);raw_threshold=predict_chunks(pipeline,X[threshold])
    calibrator=LogisticRegression(C=1e6,solver="lbfgs",max_iter=200,tol=1e-8).fit(logit(raw_cal).reshape(-1,1),y[calibration])
    cal_threshold=calibrator.predict_proba(logit(raw_threshold).reshape(-1,1))[:,1]
    raw_metrics=evaluate_predictions(y[threshold],raw_threshold)
    cal_metrics=evaluate_predictions(y[threshold],cal_threshold)
    use_calibration=bool(calibrator.coef_[0,0]>0 and cal_metrics["log_loss"]<raw_metrics["log_loss"])
    p=cal_threshold if use_calibration else raw_threshold
    thresholds=np.unique(np.quantile(p,[.5,.9,.95,.99,.995,.999,.9995,.9999])).tolist()
    threshold_rows=threshold_tradeoffs(y[threshold],p,thresholds,dates=dates_for(manifest,threshold))
    write_csv(output/"threshold_tradeoffs_2018.csv",threshold_rows)
    write_json(output/"calibration_decision.json",{"fit_year":2017,"decision_year":2018,"raw":raw_metrics,
        "sigmoid":cal_metrics,"coefficient":float(calibrator.coef_[0,0]),"used":use_calibration,
        "rule":"Use sigmoid only if monotone increasing and lower 2018 log loss"})
    bundle={"pipeline":pipeline,"calibrator":calibrator if use_calibration else None,"features":list(FEATURE_NAMES),
            "cache_version":manifest["cache_version"],"thresholds":thresholds}
    joblib.dump(bundle,output/"selected_system.joblib")
    freeze={"frozen_at_utc":datetime.now(timezone.utc).isoformat(),"selected":winner,"fit_years":[2010,2016],
            "calibration_fit_year":2017,"calibration_selection_year":2018,"calibration_used":use_calibration,
            "threshold_policy":"No operating threshold selected; evaluate frozen 2018 score-quantile tradeoffs only",
            "thresholds":thresholds,"test_year":2019,"system_sha256":sha256(output/"selected_system.joblib"),
            "configuration_sha256":sha256(output/"configuration.json")}
    write_json(output/"final_freeze.json",freeze)
    test=year_slice(manifest,2019)
    p=predict_bundle(bundle,X[test])
    results=evaluate_predictions(y[test],p,dates=dates_for(manifest,test),weather_missing=X[test,FEATURE_NAMES.index("cwfis_missing")])
    baseline=np.full(len(p),float(y[final_train].mean()))
    results["training_rate_baseline"]=evaluate_predictions(y[test],baseline)
    results["threshold_tradeoffs"]=threshold_tradeoffs(y[test],p,thresholds,dates=dates_for(manifest,test))
    results["final_system_freeze"]=freeze
    np.savez_compressed(output/"test_predictions_2019.npz",y=y[test],p=p,dates=dates_for(manifest,test),
                        weather_missing=X[test,FEATURE_NAMES.index("cwfis_missing")])
    write_json(output/"final_test.json",results)
    save_curves(y[test],p,output/"curves",prefix="test_2019")
    print(f"Final frozen2019 AP={results['average_precision']:.6f}; prevalence={results['prevalence']:.6f}",flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache",type=Path,default=ROOT/"data/interim/ignition_modeling_cache")
    parser.add_argument("--output",type=Path,default=ROOT/"artifacts/ignition_modeling/full_run")
    parser.add_argument("--smoke",action="store_true")
    args=parser.parse_args()
    with threadpool_limits(limits=2):
        execute(args.cache,args.output,args.smoke)
