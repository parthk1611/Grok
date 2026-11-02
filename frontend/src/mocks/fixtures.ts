// MOCK fixture builder. Base payloads are the unmodified contracts/examples files;
// extra synthetic fixtures live next to this file. Everything here is MOCK.
import ignitionExample from '../../../contracts/examples/ignition_output.example.json';
import spreadExample from '../../../contracts/examples/spread_output.example.json';
import weatherExample from '../../../contracts/examples/live_weather_record.example.json';
import badIgnitionProbability from '../../../contracts/examples/bad_ignition_probability.json';
import badIgnitionNoDisclaimer from '../../../contracts/examples/bad_ignition_no_disclaimer.json';
import badSpreadHorizon from '../../../contracts/examples/bad_spread_horizon.json';
import badWeatherLevel from '../../../contracts/examples/bad_weather_stale_level.json';
import ignitionExtra from './ignition_extra_cells.synthetic.json';
import ignitionSparse from './ignition_sparse.synthetic.json';
import weatherStale from './weather_stale.synthetic.json';
import weatherExpired from './weather_expired.synthetic.json';
import weatherSparse from './weather_sparse.synthetic.json';
import weatherForecast from './weather_forecast.synthetic.json';
import spreadSparse from './spread_sparse.synthetic.json';
import { torontoDate } from '../lib/time';
import type { LonLat } from '../geo/cell';
import type { IgnitionRecord, SpreadOutput, StaleLevel, WeatherRecord } from '../types';

export const SCENARIOS = ['fresh', 'stale', 'expired', 'invalid', 'sparse', 'rich'] as const;
export type Scenario = (typeof SCENARIOS)[number];

export const SCENARIO_HELP: Record<Scenario, string> = {
  fresh: 'Valid examples; ignition = retrospective, spread = hindcast; inputs ~10 min old (weather has no fetch_age_minutes)',
  stale: 'stale_level stale (+ stale_flag true), inputs ~2 h old',
  expired: 'stale_level expired (+ stale_flag true), inputs ~8 h old; spread needs confirmation',
  invalid: 'contracts/examples/bad_* payloads: must render explicit error states',
  sparse: 'Minimal: mode forecast, null values, no optional fields, spread n_runs=1 (uncertainty not estimated), validated_horizons_h empty, one empty band',
  rich: 'Everything optional present: calibration, feature_set_version, rank, weather_series, forecast_like spread, empty 1 h p90 band, forecast weather record (source_run_time, fetch age)',
};

const MIN = 60_000;
const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

const LEVEL: Record<Exclude<Scenario, 'invalid'>, { level: StaleLevel; age: number }> = {
  fresh: { level: 'fresh', age: 10 },
  stale: { level: 'stale', age: 120 },
  expired: { level: 'expired', age: 480 },
  sparse: { level: 'fresh', age: 10 },
  rich: { level: 'fresh', age: 10 },
};

export function mockIgnition(scenario: Scenario, now: Date): unknown {
  if (scenario === 'invalid') return [clone(ignitionExample), clone(badIgnitionProbability), clone(badIgnitionNoDisclaimer)];
  const { level, age } = LEVEL[scenario];
  const stale = level !== 'fresh';
  const base = scenario === 'sparse' ? ignitionSparse : [ignitionExample, ...ignitionExtra];
  return (clone(base) as IgnitionRecord[])
    .sort((a, b) => b.probability - a.probability)
    .map((r, i) => ({
      ...r,
      date: torontoDate(now),
      data_timestamp: iso(now.getTime() - age * MIN),
      stale_level: level,
      age_minutes: age,
      stale_flag: stale,
      ...(scenario === 'sparse'
        ? { stale_reason: null }
        : stale
          ? { stale_reason: `MOCK: synthetic input feed is ${age} min old (made-up age for UI testing)` }
          : {}),
      ...(scenario === 'rich' ? { calibration: 'synthetic-calibration-label', feature_set_version: 'synthetic-feature-set-v0', rank_in_day: i + 1 } : {}),
    }));
}

export function mockWeather(scenario: Scenario, now: Date, point: LonLat | null): unknown {
  if (scenario === 'invalid') return clone(badWeatherLevel);
  const base =
    scenario === 'stale'
      ? weatherStale
      : scenario === 'expired'
        ? weatherExpired
        : scenario === 'sparse'
          ? weatherSparse
          : scenario === 'rich'
            ? weatherForecast
            : weatherExample;
  const rec = clone(base) as unknown as WeatherRecord;
  // v0.2.1: age_minutes = DATA age (valid_time for observations, source_run_time for forecasts);
  // fetch_age_minutes (optional) = fetch health (retrieved_at).
  const anchor = now.getTime() - rec.age_minutes * MIN;
  if (rec.kind === 'forecast') {
    rec.source_run_time = iso(anchor);
    rec.valid_time = iso(anchor + (rec.forecast_lead_h ?? 0) * 60 * MIN);
  } else {
    rec.valid_time = iso(anchor);
  }
  rec.retrieved_at = iso(rec.fetch_age_minutes !== undefined ? now.getTime() - rec.fetch_age_minutes * MIN : anchor + 5 * MIN);
  if (point) {
    rec.longitude = Number(point[0].toFixed(5));
    rec.latitude = Number(point[1].toFixed(5));
  }
  return rec;
}

function translate(coords: unknown, dLon: number, dLat: number): unknown {
  if (Array.isArray(coords) && typeof coords[0] === 'number') {
    const c = coords as number[];
    return [Number((c[0] + dLon).toFixed(6)), Number((c[1] + dLat).toFixed(6))];
  }
  return (coords as unknown[]).map((c) => translate(c, dLon, dLat));
}

/** MOCK spread run: the example output rigidly translated to the clicked point. NOT a simulation. */
export function mockSpread(scenario: Scenario, now: Date, point: LonLat): unknown {
  if (scenario === 'invalid') return clone(badSpreadHorizon);
  const base = clone(scenario === 'sparse' ? spreadSparse : spreadExample) as unknown as SpreadOutput;
  const { level, age } = LEVEL[scenario];
  const stale = level !== 'fresh';
  const dLon = point[0] - base.ignition_point.longitude;
  const dLat = point[1] - base.ignition_point.latitude;
  base.ignition_point = { longitude: Number(point[0].toFixed(6)), latitude: Number(point[1].toFixed(6)) };
  base.run_timestamp = iso(now.getTime());
  base.start_time = iso(now.getTime());
  base.stale_level = level;
  base.age_minutes = age;
  base.inputs.weather_valid_time = iso(now.getTime() - age * MIN);
  base.inputs.stale_flag = stale;
  base.inputs.stale_reason = scenario === 'sparse' ? null : stale ? `MOCK: synthetic weather is ${age} min old (made-up age for UI testing)` : null;
  if (scenario === 'rich') {
    base.mode = 'forecast_like';
    // empty inner band: nothing reached >= 90% of runs at 1 h (empty MultiPolygon, area 0, still present)
    for (const f of base.features) {
      if (f.properties.horizon_h === 1 && f.properties.band === 'p90') {
        f.geometry = { type: 'MultiPolygon', coordinates: [] };
        f.properties.area_ha = 0;
      }
    }
    const t0 = now.getTime();
    base.inputs.weather_series = Array.from({ length: 7 }, (_, h) => ({
      valid_time: iso(t0 + h * 60 * MIN),
      wind_speed_kmh: 15 + h,
      wind_direction_deg: 270,
      temp_c: null,
      rh_pct: null,
      ffmc: null,
    }));
  }
  for (const f of base.features) {
    (f.geometry as { coordinates: unknown }).coordinates = translate(f.geometry.coordinates, dLon, dLat);
  }
  return base;
}
