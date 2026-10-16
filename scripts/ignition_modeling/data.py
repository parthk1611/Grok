"""Disk-backed, row-preserving numerical cache with explicit weather correction."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .contract import CACHE_VERSION, FEATURE_NAMES, ROOT
from .weather import WEATHER_NAMES, corrected_weather_days, index_archive, sha256

def load_cache(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["cache_version"] != CACHE_VERSION or manifest["features"] != list(FEATURE_NAMES):
        raise ValueError("Cache feature schema/version mismatch")
    n = manifest["rows"]
    X = np.memmap(directory / "X.f64",dtype="float64",mode="r",shape=(n,36))
    y = np.memmap(directory / "y.u8",dtype="uint8",mode="r",shape=(n,))
    return X,y,manifest

def year_slice(manifest, first, last=None):
    last = first if last is None else last
    positions = [v for d,v in manifest["date_slices"].items() if first<=int(d[:4])<=last]
    if not positions:
        raise ValueError("Requested years absent from cache")
    return slice(positions[0][0],positions[-1][1])

def dates_for(manifest, selection):
    return np.concatenate([np.repeat(np.datetime64(d,"D"),end-start) for d,(start,end) in manifest["date_slices"].items()
                           if start>=selection.start and end<=selection.stop])

def prepare_cache(input_path, directory, chunk_size=50000):
    directory = Path(directory); directory.mkdir(parents=True,exist_ok=True)
    if (directory / "manifest.json").exists():
        manifest=load_cache(directory)[2]
        if sha256(input_path)!=manifest["original_panel_sha256"]:
            raise ValueError("Existing cache belongs to another input panel")
        return manifest
    audit = json.loads((ROOT / "docs/modeling/data_quality_report.json").read_text())
    if audit["status"] != "PASS" or not audit["full_scan_completed"]:
        raise ValueError("Full original-panel integrity gate has not passed")
    if sha256(input_path) != audit["source"]["sha256"]:
        raise ValueError("Input differs from the audited panel")
    n = audit["totals"]["rows"]
    X = np.memmap(directory / "X.f64",dtype="float64",mode="w+",shape=(n,36))
    y = np.memmap(directory / "y.u8",dtype="uint8",mode="w+",shape=(n,))
    ids = np.memmap(directory / "grid.u16",dtype="uint16",mode="w+",shape=(n,))
    grid = pd.read_csv(ROOT / "data/interim/ontario_fire_region_10km_grid.csv")
    grid_lookup = {key:i for i,key in enumerate(grid.grid_id)}
    slices,offset,previous = {},0,None
    cols = [*FEATURE_NAMES,"ignition","ignition_date","grid_id"]
    dtypes = {**dict.fromkeys(FEATURE_NAMES,"float64"),"ignition":"uint8","grid_id":str,"ignition_date":str}
    for chunk in pd.read_csv(input_path,usecols=cols,dtype=dtypes,chunksize=chunk_size):
        end = offset+len(chunk)
        if end > n or not chunk.ignition_date.is_monotonic_increasing:
            raise ValueError("Cache requires audited chronological date order")
        X[offset:end] = chunk[list(FEATURE_NAMES)].to_numpy()
        y[offset:end] = chunk.ignition.to_numpy()
        mapped = chunk.grid_id.map(grid_lookup)
        if mapped.isna().any():
            raise ValueError("Unknown grid ID")
        ids[offset:end] = mapped.to_numpy(dtype=np.uint16)
        for day, positions in chunk.groupby("ignition_date",sort=False).indices.items():
            start,stop = offset+int(positions[0]),offset+int(positions[-1])+1
            if previous and day < previous:
                raise ValueError("Input dates go backwards")
            slices[day] = [slices.get(day,[start,stop])[0],stop]
            previous = day
        offset=end
        print(f"Numeric cache {offset:,}/{n:,}",flush=True)
    if offset!=n or len(slices)!=1840 or any(end-start!=len(grid) for start,end in slices.values()):
        raise ValueError("Full-panel cache universe mismatch")
    db_path = directory / "cwfis.sqlite"
    source_audit = index_archive(ROOT / "data/raw/cwfis/cwfis_fwi2010sv3.0_ll.csv.bz2",db_path,grid,chunk_size)
    changed = {name:0 for name in WEATHER_NAMES}
    annual = {}
    for i,(day,weather) in enumerate(corrected_weather_days(db_path,grid,list(slices))):
        start,end = slices[day]; selection=slice(start,end)
        day_ids = ids[selection]
        if len(np.unique(day_ids)) != len(grid):
            raise ValueError("Duplicate grid cells within a cache date")
        values = weather[day_ids]
        if np.isinf(values).any():
            raise ValueError("Corrected weather contains infinite values")
        for name in WEATHER_NAMES:
            v=values[:,WEATHER_NAMES.index(name)]
            finite=v[np.isfinite(v)]
            maximum=101 if name=="cwfis_ffmc" else 100
            if name in {"cwfis_rh_pct","cwfis_ffmc"} and (np.any(finite<0) or np.any(finite>maximum)):
                raise ValueError(f"Corrected source range invalid: {name}")
            if name not in {"cwfis_temp_c","cwfis_rh_pct","cwfis_ffmc"} and np.any(finite<0):
                raise ValueError(f"Corrected source nonnegative range invalid: {name}")
            if name.endswith("fraction") and np.any(finite>1):
                raise ValueError(f"Corrected fraction exceeds one: {name}")
        count=values[:,WEATHER_NAMES.index("cwfis_station_count")]
        missing=values[:,WEATHER_NAMES.index("cwfis_missing")]
        coverage=values[:,WEATHER_NAMES.index("cwfis_precip_7d_coverage_days")]
        rain=values[:,WEATHER_NAMES.index("cwfis_precip_7d_mm")]
        if np.any(count>4) or not np.array_equal(missing,count==0):
            raise ValueError("Corrected station/missing flag consistency failed")
        if np.any(coverage>7) or not np.array_equal(np.isnan(rain),coverage<7):
            raise ValueError("Corrected seven-day rain coverage consistency failed")
        for j,name in enumerate(WEATHER_NAMES):
            k=FEATURE_NAMES.index(name)
            old=np.array(X[selection,k]); new=values[:,j]
            changed[name] += int((~np.isclose(old,new,rtol=0,atol=1e-6,equal_nan=True)).sum())
            X[selection,k]=new
        stats=annual.setdefault(day[:4],{"rows":0,"positives":0,"weather_missing":0,"rain_7d_missing":0})
        stats["rows"]+=end-start; stats["positives"]+=int(y[selection].sum())
        stats["weather_missing"]+=int((values[:,WEATHER_NAMES.index("cwfis_missing")]==1).sum())
        stats["rain_7d_missing"]+=int(np.isnan(values[:,WEATHER_NAMES.index("cwfis_precip_7d_mm")]).sum())
        if i%100==0:
            print(f"Corrected weather {day} ({i+1}/1840)",flush=True)
    X.flush();y.flush();ids.flush()
    manifest={"cache_version":CACHE_VERSION,"features":list(FEATURE_NAMES),"rows":n,
              "original_panel_sha256":audit["source"]["sha256"],"date_slices":slices,
              "weather_source_audit":source_audit,"changed_cell_values":changed,"annual":annual,
              "scope":"Retrospective hindcast on frozen v1 grid; edge-cell universe uses study-period events; historical availability unverified",
              "numeric_storage":"float64 C-order memory mapped; all original rows and labels retained",
              "learned_preprocessing":"none; learned model transformations fit later within each training fold"}
    manifest["corrected_value_checks"]={"status":"PASS","infinities":0,"range_violations":0,
        "station_missing_consistency_violations":0,"rain_coverage_consistency_violations":0,
        "grid_duplicates":0,"rows_preserved":True,"labels_preserved":True}
    (directory / "manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    report = ROOT / "docs/modeling/corrected_weather_audit.json"
    compact={k:v for k,v in manifest.items() if k!="date_slices"}
    report.write_text(json.dumps(compact,indent=2)+"\n")
    return manifest

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--cache",type=Path,default=ROOT/"data/interim/ignition_modeling_cache")
    parser.add_argument("--chunk-size",type=int,default=50000)
    args=parser.parse_args()
    prepare_cache(args.input,args.cache,args.chunk_size)
