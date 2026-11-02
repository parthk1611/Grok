// Hand-written TypeScript views of the contract v0.1.0 payloads.
// The JSON Schemas in /contracts are the source of truth; every payload is
// validated with Ajv (see lib/contract.ts) before it is cast to these types.
import type { Feature, Polygon, MultiPolygon } from 'geojson';

export type StaleLevel = 'fresh' | 'stale' | 'expired';

export interface IgnitionRecord {
  cell_id: string;
  date: string;
  probability: number;
  model_version: string;
  data_timestamp: string;
  stale_flag: boolean;
  disclaimer: string;
  stale_reason?: string | null;
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
  validated_horizons_h?: Array<1 | 3 | 6>;
  disclaimer: string;
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
  age_minutes: number;
  stale_level: StaleLevel;
  from_cache: boolean;
  quality_flags?: string[];
}

/** Result of loading + validating one payload. Never silent on failure. */
export type Loaded<T> =
  | { ok: true; data: T }
  | { ok: false; kind: 'invalid' | 'unavailable'; title: string; errors: string[] };
