"""Past-only station geometry and globally indexed retrospective CWFIS records.

The original archive is not globally date sorted. Its first valid FWI record
for each date/station is deterministic; historical publication time is unknown.
"""
import hashlib
import json
import sqlite3
from collections import deque
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.build_ignition_feature_panel import CWFIS_NUMERIC_FIELDS, CWFIS_OUTPUT_FIELDS
from .contract import ROOT

QUALITY_NAMES = ("cwfis_station_count", "cwfis_nearest_station_km",
                 "cwfis_observation_coverage_fraction", "cwfis_imputed_station_fraction",
                 "cwfis_calcstatus_nonzero_fraction", "cwfis_missing")
WEATHER_NAMES = (*QUALITY_NAMES, *CWFIS_OUTPUT_FIELDS.values(),
                 "cwfis_precip_7d_mm", "cwfis_precip_7d_coverage_days")

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()

def index_archive(archive, db_path, grid, chunk_size=50000):
    expected = json.loads((ROOT / "data/raw/cwfis/manifest.json").read_text())["archive_sha256"]
    actual = sha256(archive)
    if actual != expected:
        raise ValueError("CWFIS archive differs from the frozen source checksum")
    audit_path = Path(db_path).with_suffix(".json")
    if Path(db_path).exists() and audit_path.exists():
        audit = json.loads(audit_path.read_text())
        if audit["source_sha256"] == actual:
            return audit
        raise ValueError("Existing index belongs to another source")
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA cache_size=-32768")
    fields = (*CWFIS_NUMERIC_FIELDS, "imputed", "calcstatus")
    db.execute("CREATE TABLE records(day TEXT, key TEXT, lon REAL, lat REAL, " +
               ",".join(f"{n} REAL" for n in fields) +
               ",duplicates INTEGER DEFAULT 0,conflicts INTEGER DEFAULT 0,PRIMARY KEY(day,key)) WITHOUT ROWID")
    columns = ("day", "key", "lon", "lat", *fields)
    replace = ",".join(f"{n}=CASE WHEN records.fwi IS NULL AND excluded.fwi IS NOT NULL THEN excluded.{n} ELSE records.{n} END" for n in fields)
    sql = "INSERT INTO records(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ") ON CONFLICT(day,key) DO UPDATE SET " + replace + ",duplicates=records.duplicates+1,conflicts=records.conflicts+CASE WHEN NOT(records.fwi IS excluded.fwi) THEN 1 ELSE 0 END"
    bounds = (grid.centroid_longitude.min()-5, grid.centroid_longitude.max()+5,
              grid.centroid_latitude.min()-5, grid.centroid_latitude.max()+5)
    rows = retained = reversals = 0
    examples, previous = [], None
    usecols = ["rep_date", "aes", "wmo", "lon", "lat", "opts", "calcstatus", *CWFIS_NUMERIC_FIELDS]
    for chunk in pd.read_csv(archive, compression="bz2", usecols=usecols, dtype=str,
                             keep_default_na=False, chunksize=chunk_size):
        days = chunk.rep_date.str.slice(0, 10).to_numpy()
        for before, after in zip([previous] + days[:-1].tolist(), days):
            if before and after < before:
                reversals += 1
                if len(examples) < 8:
                    examples.append([before, after])
        previous = days[-1]
        rows += len(chunk)
        for n in ["lon", "lat", "calcstatus", *CWFIS_NUMERIC_FIELDS]:
            chunk[n] = pd.to_numeric(chunk[n], errors="raise")
        chunk = chunk[chunk.lon.between(bounds[0],bounds[1]) & chunk.lat.between(bounds[2],bounds[3])].copy()
        chunk["day"] = chunk.rep_date.str.slice(0, 10)
        chunk["key"] = [json.dumps((a,w,f"{lo:.5f}",f"{la:.5f}"),separators=(",",":"))
                        for a,w,lo,la in zip(chunk.aes,chunk.wmo,chunk.lon,chunk.lat)]
        chunk["lon"] = chunk.lon.round(5)
        chunk["lat"] = chunk.lat.round(5)
        chunk["imputed"] = chunk.opts.str.contains("IDW|M=",regex=True).astype(int)
        values = chunk[list(columns)].astype(object).where(pd.notna(chunk[list(columns)]), None)
        db.executemany(sql, values.itertuples(index=False,name=None))
        db.commit()
        retained += len(chunk)
        print(f"CWFIS index {rows:,} source rows", flush=True)
    db.execute("CREATE INDEX station_key ON records(key)")
    unique, duplicates, conflicts = db.execute("SELECT COUNT(*),SUM(duplicates),SUM(conflicts) FROM records").fetchone()
    stations = db.execute("SELECT key,lon,lat,MIN(day) FROM records GROUP BY key ORDER BY MIN(day),key").fetchall()
    audit = {"source_sha256": actual, "source_rows": rows, "retained_raw_rows": retained,
             "unique_date_station_records":unique,"duplicate_records":duplicates or 0,
             "duplicate_conflicting_fwi_records":conflicts or 0,"backward_date_transitions":reversals,
             "backward_date_examples":examples,"stations":len(stations),
             "record_resolution":"First record per date/key; replace only when existing FWI is null and later FWI is valid",
             "availability":"Retrospective archive; per-record historical publication time unknown",
             "station_rule":"nearest min(4,eligible_count) after first_seen<=observation_date; never future stations"}
    db.commit(); db.close()
    audit_path.write_text(json.dumps(audit,indent=2)+"\n")
    return audit

def distance_km(longitude, latitude, station_lon, station_lat):
    lat = np.radians(latitude)
    slat = np.radians(station_lat)
    a = np.sin((slat-lat)/2)**2 + np.cos(lat)*np.cos(slat)*np.sin(np.radians(station_lon-longitude)/2)**2
    return 6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(a,0,1)))

class AsOfStations:
    def __init__(self, grid, stations, count=4):
        self.longitude = grid.centroid_longitude.to_numpy()
        self.latitude = grid.centroid_latitude.to_numpy()
        self.stations, self.count, self.next = stations, count, 0
        self.ids = np.full((len(grid),count),-1,dtype=np.int32)
        self.distances = np.full((len(grid),count),np.inf)

    def advance(self, day):
        while self.next < len(self.stations) and self.stations[self.next][3] <= day:
            _,lo,la,_ = self.stations[self.next]
            distances = np.column_stack((self.distances,distance_km(self.longitude,self.latitude,lo,la)))
            ids = np.column_stack((self.ids,np.full(len(self.longitude),self.next)))
            order = np.argsort(distances,axis=1,kind="stable")[:,:self.count]
            self.distances = np.take_along_axis(distances,order,axis=1)
            self.ids = np.take_along_axis(ids,order,axis=1)
            self.next += 1
        return self.ids, self.distances

def daily_weather(db, stations, choices, day):
    ids, distances = choices.advance(day)
    lookup = {s[0]:i for i,s in enumerate(stations)}
    source = np.full((len(stations)+1,len(CWFIS_NUMERIC_FIELDS)+2),np.nan)
    rows = db.execute("SELECT key,"+",".join((*CWFIS_NUMERIC_FIELDS,"imputed","calcstatus"))+" FROM records WHERE day=?",(day,))
    for key,*values in rows:
        source[lookup[key]] = [np.nan if v is None else v for v in values]
    values = source[ids]
    usable = (ids >= 0) & np.isfinite(values[:,:,CWFIS_NUMERIC_FIELDS.index("fwi")])
    count = usable.sum(axis=1)
    weights = np.where(usable,1/np.maximum(distances,1)**2,0)
    result = np.full((len(ids),len(QUALITY_NAMES)+len(CWFIS_NUMERIC_FIELDS)),np.nan)
    result[:,0] = count
    result[:,1] = np.where(count>0,np.min(np.where(usable,distances,np.inf),axis=1),np.nan)
    denominator = (ids>=0).sum(axis=1)
    result[:,2] = np.divide(count,denominator,out=np.zeros(len(ids)),where=denominator>0)
    for out_idx,source_idx in [(3,-2),(4,-1)]:
        valid = usable & np.isfinite(values[:,:,source_idx])
        flag = values[:,:,source_idx] if source_idx == -2 else (values[:,:,source_idx]!=0)
        result[:,out_idx] = np.divide(np.where(valid,flag,0).sum(axis=1),valid.sum(axis=1),
                                       out=np.full(len(ids),np.nan),where=valid.sum(axis=1)>0)
    result[:,5] = count==0
    for j in range(len(CWFIS_NUMERIC_FIELDS)):
        valid = usable & np.isfinite(values[:,:,j])
        denominator = np.where(valid,weights,0).sum(axis=1)
        result[:,6+j] = np.divide(np.where(valid,values[:,:,j]*weights,0).sum(axis=1),denominator,
                                  out=np.full(len(ids),np.nan),where=denominator>0)
    return np.round(result,6)

def corrected_weather_days(db_path, grid, dates):
    if any(a>=b for a,b in zip(dates,dates[1:])):
        raise ValueError("Corrected weather requires strictly increasing issue dates")
    db = sqlite3.connect(db_path)
    stations = db.execute("SELECT key,lon,lat,MIN(day) FROM records GROUP BY key ORDER BY MIN(day),key").fetchall()
    choices = AsOfStations(grid,stations)
    window = deque(maxlen=7)
    previous = None
    rain_index = 6+CWFIS_NUMERIC_FIELDS.index("precip")
    for issue in dates:
        start,end = date.fromisoformat(issue)-timedelta(days=7), date.fromisoformat(issue)-timedelta(days=1)
        if previous is None or start > previous+timedelta(days=1):
            window.clear(); previous=start-timedelta(days=1)
        while previous < end:
            previous += timedelta(days=1)
            weather = daily_weather(db,stations,choices,previous.isoformat())
            window.append(weather[:,rain_index])
        rain = np.array(window)
        coverage = np.isfinite(rain).sum(axis=0)
        total = np.where(coverage==7,np.round(np.nansum(rain,axis=0),6),np.nan)
        yield issue, np.column_stack((weather,total,coverage))
    db.close()
