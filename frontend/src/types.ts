// Hand-written TypeScript views of the contract v0.1.0 payloads.
// The JSON Schemas in /contracts are the source of truth; every payload is
// validated with Ajv (see lib/contract.ts) before it is cast to these types.
import type { Feature, Polygon, MultiPolygon } from 'geojson';

export type StaleLevel = 'fresh' | 'stale' | 'expired';
export type OutputMode = 'retrospective' | 'hindcast' | 'forecast_like' | 'forecast';
export type UncertaintyStatus = 'estimated' | 'not_estimated';

export interface IgnitionRecord {
  cell_id: string;
  date: string;
  probability: number;
  model_version: string;
  data_timestamp: string;
  stale_flag: boolean;
  disclaimer: string;
  mode: OutputMode;
  stale_level: StaleLevel;
  age_minutes: number;
  stale_reason?: string | null;
  calibration?: string | null;
  feature_set_version?: string;
  rank_in_day?: number;
  grid_version?: string;
  generated_at?: string;
}

export interface SpreadFeatureProperties {
  horizon_h: 1 | 3 | 6;
  band: 'p10' | 'p50' | 'p90';
  burn_probability_level: 0.1 | 0.5 | 0.9;
  area_ha: number;
  perimeter_km?: number;
  head_direction_deg?: number | null;
  validated?: boolean;
}

export type SpreadFeature = Feature<Polygon | MultiPolygon, SpreadFeatureProperties>;

export interface SpreadInputs {
  weather_source: string;
  weather_valid_time: string;
  weather_data_timestamp?: string;
  wind_speed_kmh: number;
  wind_direction_deg: number;
  temp_c?: number | null;
  rh_pct?: number | null;
  precip_mm?: number | null;
  ffmc?: number | null;
  bui?: number | null;
  fuel_type?: string | null;
  stale_flag: boolean;
  stale_reason?: string | null;
  fuel_source: string;
  weather_series?: WeatherSeriesEntry[];
}

export interface WeatherSeriesEntry {
  valid_time: string;
  wind_speed_kmh: number;
  wind_direction_deg: number;
  temp_c?: number | null;
  rh_pct?: number | null;
  ffmc?: number | null;
}

export interface PerturbationParameter {
  name: string;
  distribution: 'normal' | 'uniform' | 'fixed';
  sigma?: number;
  low?: number;
  high?: number;
  value?: number;
}

export interface SpreadOutput {
  type: 'FeatureCollection';
  features: SpreadFeature[];
  run_timestamp: string;
  model_version: string;
  n_runs: number;
  seed?: number;
  ignition_point: { longitude: number; latitude: number };
  start_time?: string;
  inputs: SpreadInputs;
  validated_horizons_h: Array<1 | 3 | 6>;
  disclaimer: string;
  mode: OutputMode;
  stale_level: StaleLevel;
  age_minutes: number;
  uncertainty_status: UncertaintyStatus;
  grid_resolution_m: number;
  perturbation: { parameters: PerturbationParameter[] };
}

export interface WeatherRecord {
  record_id: string;
  source: string;
  source_layer?: string | null;
  kind: 'observation' | 'forecast';
  source_run_time?: string;
  forecast_lead_h?: number | null;
  latitude: number;
  longitude: number;
  station_id?: string | null;
  cell_id?: string | null;
  valid_time: string;
  retrieved_at: string;
  temp_c: number | null;
  rh_pct: number | null;
  wind_speed_kmh: number | null;
  wind_direction_deg: number | null;
  wind_gust_kmh?: number | null;
  wind_u_kmh?: number | null;
  wind_v_kmh?: number | null;
  precip_mm: number | null;
  precip_period_h?: number | null;
  /** v0.2.1: DATA age (now - valid_time for observations, now - source_run_time for forecasts). */
  age_minutes: number;
  /** v0.2.1: optional fetch health (now - retrieved_at). */
  fetch_age_minutes?: number;
  stale_level: StaleLevel;
  from_cache: boolean;
  quality_flags?: string[];
}

/** Optional manual weather for a spread run (SpreadRequest.weather_override, Team Lead ruling 2026-11-09). */
export interface WeatherOverride {
  /** 10 m wind speed, km/h */
  wind_speed_kmh: number;
  /** degrees the wind blows FROM (0-360) */
  wind_direction_deg: number;
  ffmc?: number;
  bui?: number;
  temp_c?: number;
  rh_pct?: number;
  precip_mm?: number;
}

/** POST /api/v1/spread body (RELAY openapi draft + Team Lead rulings). */
export interface SpreadRequestBody {
  longitude: number;
  latitude: number;
  /** ISO 8601 UTC with Z */
  start_time: string;
  n_runs?: number;
  seed?: number;
  /** Explicit user confirmation to run on expired weather. Only ever sent as true. */
  confirm_degraded?: true;
  weather_override?: WeatherOverride;
}

/** Per-response metadata the client learned beyond the payload itself. */
export interface ResponseMeta {
  /** From the envelope `mock` flag or the X-Mock-Data header; undefined when the server did not say. */
  mock?: boolean;
  requestId?: string;
  /** Things the server did that deviate from the contract/rulings (shown, never patched). */
  warnings: string[];
}

export interface ApiErrorInfo {
  status: number;
  error: string;
  reason: string;
  last_cache_time: string | null;
  request_id: string;
  disclaimer: string;
  details?: unknown[];
}

/** Result of loading + validating one payload. Never silent on failure. */
export type Loaded<T> =
  | { ok: true; data: T; meta?: ResponseMeta }
  | {
      ok: false;
      kind: 'invalid' | 'unavailable' | 'api_error';
      title: string;
      errors: string[];
      /** HTTP status for api_error. */
      httpStatus?: number;
      /** Parsed error body (api_error); null when the body did not even have a reason. */
      apiError?: ApiErrorInfo | null;
      meta?: ResponseMeta;
    };
