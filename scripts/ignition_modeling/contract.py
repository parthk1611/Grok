"""Shared frozen feature schema; labels never enter numeric model matrices."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELECTION_PATH = ROOT / "data/metadata/ignition_feature_selection_v1.json"
FEATURE_NAMES = tuple(json.loads(SELECTION_PATH.read_text())["predictors"])
assert len(FEATURE_NAMES) == 36 and len(set(FEATURE_NAMES)) == 36
WEATHER_FEATURES = tuple(n for n in FEATURE_NAMES if n in {
    "cwfis_temp_c", "cwfis_rh_pct", "cwfis_wind_speed_kmh", "cwfis_precip_mm",
    "cwfis_ffmc", "cwfis_dmc", "cwfis_dc", "cwfis_isi", "cwfis_bui",
    "cwfis_fwi", "cwfis_dsr", "cwfis_precip_7d_mm"})
HISTORY_FEATURE = "fire_history_days_since_same_cell"
INDICATOR_FEATURES = tuple(n for n in FEATURE_NAMES if n in set(WEATHER_FEATURES) | {
    "terrain_elevation_mean_m", "terrain_slope_deg", "terrain_aspect_sin",
    "terrain_aspect_cos", "terrain_ruggedness_m", "cwfis_nearest_station_km",
    "cwfis_imputed_station_fraction", "cwfis_calcstatus_nonzero_fraction", HISTORY_FEATURE})
SEED = 385
CACHE_VERSION = "ignition-v1-weather-asof-global-date-v2"

def check_matrix(X):
    if getattr(X, "ndim", None) != 2 or X.shape[1] != len(FEATURE_NAMES):
        raise ValueError("Expected numeric inputs in the exact 36-feature manifest order")
