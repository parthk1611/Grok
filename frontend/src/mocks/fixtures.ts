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
import spreadSparse from './spread_sparse.synthetic.json';
import { torontoDate } from '../lib/time';
import type { LonLat } from '../geo/cell';
import type { SpreadOutput } from '../types';

export const SCENARIOS = ['fresh', 'stale', 'expired', 'invalid', 'sparse'] as const;
export type Scenario = (typeof SCENARIOS)[number];

export const SCENARIO_HELP: Record<Scenario, string> = {
  fresh: 'Valid payloads, inputs ~10 min old',
  stale: 'Valid payloads, stale_flag true, inputs ~2 h old',
  expired: 'Valid payloads, stale_flag true, inputs ~8 h old (expired)',
  invalid: 'contracts/examples/bad_* payloads: must render explicit error states',
  sparse: 'Valid but sparse: null values, no validated_horizons_h, optional fields missing',
};

const MIN = 60_000;
const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');

/** Age in minutes of the newest input for each scenario. */
const AGE_MIN: Record<Exclude<Scenario, 'invalid'>, number> = { fresh: 10, stale: 120, expired: 480, sparse: 10 };

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

export function mockIgnition(scenario: Scenario, now: Date): unknown {
  if (scenario === 'invalid') return [clone(ignitionExample), clone(badIgnitionProbability), clone(badIgnitionNoDisclaimer)];
  const age = AGE_MIN[scenario];
  const stale = scenario === 'stale' || scenario === 'expired';
  const base = scenario === 'sparse' ? ignitionSparse : [ignitionExample, ...ignitionExtra];
  return clone(base).map((r) => ({
    ...r,
    date: torontoDate(now),
    data_timestamp: iso(now.getTime() - age * MIN),
    stale_flag: stale,
    ...(scenario === 'sparse'
      ? { stale_reason: null }
      : stale
        ? { stale_reason: `MOCK: synthetic input feed is ${age} min old (made-up age for UI testing)` }
        : {}),
  }));
}

export function mockWeather(scenario: Scenario, now: Date, point: LonLat | null): unknown {
  if (scenario === 'invalid') return clone(badWeatherLevel);
  const base =
    scenario === 'stale' ? weatherStale : scenario === 'expired' ? weatherExpired : scenario === 'sparse' ? weatherSparse : weatherExample;
  const rec = clone(base) as typeof weatherExample;
  const retrieved = now.getTime() - rec.age_minutes * MIN;
  rec.retrieved_at = iso(retrieved);
  rec.valid_time = iso(retrieved - 5 * MIN);
  if (point) {
    rec.longitude = Number(point[0].toFixed(5));
    rec.latitude = Number(point[1].toFixed(5));
  }
  return rec;
}

type Ring = number[][];
function translate(coords: unknown, dLon: number, dLat: number): unknown {
  if (typeof (coords as number[])[0] === 'number') {
    const c = coords as number[];
    return [Number((c[0] + dLon).toFixed(6)), Number((c[1] + dLat).toFixed(6))];
  }
  return (coords as unknown[]).map((c) => translate(c, dLon, dLat));
}

/** MOCK spread run: the example output rigidly translated to the clicked point. NOT a simulation. */
export function mockSpread(scenario: Scenario, now: Date, point: LonLat): unknown {
  if (scenario === 'invalid') return clone(badSpreadHorizon);
  const base = clone(scenario === 'sparse' ? spreadSparse : spreadExample) as unknown as SpreadOutput;
  const dLon = point[0] - base.ignition_point.longitude;
  const dLat = point[1] - base.ignition_point.latitude;
  const age = AGE_MIN[scenario];
  const stale = scenario === 'stale' || scenario === 'expired';
  base.ignition_point = { longitude: Number(point[0].toFixed(6)), latitude: Number(point[1].toFixed(6)) };
  base.run_timestamp = iso(now.getTime());
  base.start_time = iso(now.getTime());
  base.inputs.weather_valid_time = iso(now.getTime() - age * MIN);
  base.inputs.stale_flag = stale;
  base.inputs.stale_reason = stale ? `MOCK: synthetic weather is ${age} min old (made-up age for UI testing)` : null;
  if (scenario === 'sparse') base.inputs.stale_reason = null;
  for (const f of base.features) {
    (f.geometry as { coordinates: unknown }).coordinates = translate(f.geometry.coordinates as Ring[], dLon, dLat);
  }
  return base;
}
