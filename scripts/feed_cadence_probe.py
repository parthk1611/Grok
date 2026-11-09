#!/usr/bin/env python3
"""Feed cadence probe (RELAY evidence for cache_fallback.md thresholds).

Every --interval seconds (default 900 = 15 min) for --iterations (default 96 =
24 h) it polls three public MSC / CWFIS feeds and appends ONE JSON line per
iteration to --out (default /workspace/relay-evidence/feed_cadence.jsonl, kept
outside the repo). It records only what the feeds actually return: fetch time,
HTTP status, latency and source times. Failures are recorded as errors, never
filled in. Standard library only; no credentials are used or needed.

  python3 scripts/feed_cadence_probe.py --once          # one iteration, print it
  nohup python3 scripts/feed_cadence_probe.py &         # 24 h background run
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

TIMEOUT_S = 30
USER_AGENT = "COMP-385-feed-cadence-probe/1.0 (research; contact: repo owner)"

HRDPS_URL = ("https://geo.weather.gc.ca/geomet?service=WMS&version=1.3.0"
             "&request=GetCapabilities&layer=HRDPS.CONTINENTAL_TT")
CWFIS_URL = ("https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows?service=WFS&version=2.0.0"
             "&request=GetFeature&typeNames=public:firewx_stns_current"
             "&outputFormat=application/json&propertyName=rep_date,prov")
SWOB_BASE = "https://api.weather.gc.ca/collections/swob-realtime/items"
SWOB_MIN_PLAUSIBLE = "2000-01-01T00:00:00Z"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str) -> tuple[int | None, bytes | None, float, str | None]:
    """Return (status, body, latency_s, error). Never raises."""
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            body = r.read()
            return r.status, body, time.monotonic() - t0, None
    except urllib.error.HTTPError as e:
        return e.code, None, time.monotonic() - t0, f"HTTPError {e.code}"
    except Exception as e:  # timeout, DNS, TLS ...
        return None, None, time.monotonic() - t0, f"{type(e).__name__}: {e}"


def probe_hrdps() -> dict:
    fetched = iso(utc_now())
    status, body, lat, err = http_get(HRDPS_URL)
    out = {"fetched_at": fetched, "http_status": status, "latency_s": round(lat, 3), "error": err}
    if body is None:
        return out
    try:
        xml = body.decode("utf-8", "replace")
        i = xml.index("<Name>HRDPS.CONTINENTAL_TT</Name>")
        blk = xml[i:i + 8000]
        dims = {}
        for m in re.finditer(r'<Dimension name="(time|reference_time)"[^>]*?(?:default="([^"]*)")?[^>]*>([^<]*)</Dimension>', blk):
            dims[m.group(1)] = {"default": m.group(2), "extent": m.group(3).strip()}
        # 'default' attr may come before other attrs; re-parse defensively
        for name in ("time", "reference_time"):
            m = re.search(r'<Dimension name="%s"[^>]*default="([^"]*)"' % name, blk)
            if m and name in dims:
                dims[name]["default"] = m.group(1)
        ref = dims.get("reference_time", {})
        tim = dims.get("time", {})
        ref_ext = ref.get("extent", "")
        out.update({
            "reference_time_extent": ref_ext,
            "reference_time_default": ref.get("default"),
            "latest_reference_time": ref_ext.split("/")[1] if ref_ext.count("/") >= 2 else ref.get("default"),
            "time_extent": tim.get("extent"),
            "time_default": tim.get("default"),
        })
        lr = out["latest_reference_time"]
        if lr:
            age = (utc_now() - datetime.strptime(lr, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
            out["latest_run_age_min"] = round(age.total_seconds() / 60, 1)
    except Exception as e:
        out["error"] = f"parse: {type(e).__name__}: {e}"
    return out


def probe_cwfis() -> dict:
    fetched = iso(utc_now())
    status, body, lat, err = http_get(CWFIS_URL)
    out = {"fetched_at": fetched, "http_status": status, "latency_s": round(lat, 3), "error": err}
    if body is None:
        return out
    try:
        d = json.loads(body)
        feats = d.get("features", [])
        dates = [f["properties"].get("rep_date") for f in feats]
        valid = [x for x in dates if x]
        on = [f["properties"].get("rep_date") for f in feats
              if (f["properties"].get("prov") or "").strip() == "ON" and f["properties"].get("rep_date")]
        now = utc_now()

        def age_min(s):
            return round((now - datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 60, 1)

        out.update({
            "n_features": len(feats),
            "n_null_rep_date": len(dates) - len(valid),
            "max_rep_date": max(valid) if valid else None,
            "max_rep_date_age_min": age_min(max(valid)) if valid else None,
            "rep_date_distribution": dict(sorted(Counter(valid).items(), reverse=True)[:12]),
            "ontario_n": len(on),
            "ontario_max_rep_date": max(on) if on else None,
            "ontario_max_rep_date_age_min": age_min(max(on)) if on else None,
            "ontario_rep_date_distribution": dict(sorted(Counter(on).items(), reverse=True)[:12]),
        })
    except Exception as e:
        out["error"] = f"parse: {type(e).__name__}: {e}"
    return out


def swob_latest(upper: str | None) -> tuple[dict, int | None, float, str | None]:
    q = {"f": "json", "limit": "1", "sortby": "-date_tm-value", "properties": "date_tm-value,stn_id-value"}
    if upper:
        q["datetime"] = f"{SWOB_MIN_PLAUSIBLE}/{upper}"
    status, body, lat, err = http_get(SWOB_BASE + "?" + urllib.parse.urlencode(q))
    res = {}
    if body is not None:
        try:
            f = json.loads(body)["features"][0]
            res = {"time": f["properties"].get("date_tm-value"), "stn": f["properties"].get("stn_id-value")}
        except Exception as e:
            err = f"parse: {type(e).__name__}: {e}"
    return res, status, lat, err


def probe_swob() -> dict:
    now = utc_now()
    out = {"fetched_at": iso(now)}
    plaus, st, lat, err = swob_latest(iso(now))  # plausible = not after now
    out.update({"http_status": st, "latency_s": round(lat, 3), "error": err})
    if plaus.get("time"):
        t = datetime.strptime(plaus["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        out["latest_plausible_obs_time"] = plaus["time"]
        out["latest_plausible_obs_station"] = plaus.get("stn")
        out["latest_plausible_obs_age_min"] = round((now - t).total_seconds() / 60, 1)
    # Unfiltered newest record: shows whether bogus future timestamps (e.g. 2101) are present.
    raw, st2, lat2, err2 = swob_latest(None)
    out["unfiltered_top"] = {"http_status": st2, "latency_s": round(lat2, 3), "error": err2,
                             "obs_time": raw.get("time"), "station": raw.get("stn")}
    return out


def one_iteration(n: int) -> dict:
    t0 = utc_now()
    rec = {"iteration": n, "iteration_started_at": iso(t0)}
    for name, fn in (("hrdps_wms", probe_hrdps), ("cwfis_firewx_stns_current", probe_cwfis),
                     ("swob_realtime", probe_swob)):
        try:
            rec[name] = fn()
        except Exception as e:  # last-resort guard so the loop never dies
            rec[name] = {"error": f"unhandled {type(e).__name__}: {e}"}
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=int, default=900)
    ap.add_argument("--iterations", type=int, default=96)
    ap.add_argument("--out", default="/workspace/relay-evidence/feed_cadence.jsonl")
    ap.add_argument("--once", action="store_true", help="run one iteration, append and print it")
    a = ap.parse_args()
    n_total = 1 if a.once else a.iterations
    start = time.monotonic()
    for n in range(n_total):
        rec = one_iteration(n)
        line = json.dumps(rec, sort_keys=True)
        with open(a.out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        if a.once:
            print(json.dumps(rec, indent=2, sort_keys=True))
        if n < n_total - 1:
            time.sleep(max(0, start + (n + 1) * a.interval - time.monotonic()))


if __name__ == "__main__":
    main()
