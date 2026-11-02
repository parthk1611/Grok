// Typed API client. Adapters return unknown JSON; the client validates every
// payload with Ajv against the /contracts schemas and returns an explicit error
// result (never throws, never passes unvalidated data on).
import type { LonLat } from '../geo/cell';
import type { IgnitionRecord, Loaded, SpreadOutput, WeatherRecord } from '../types';
import { validateIgnitionArray, validateSpreadOutput, validateWeatherRecord } from '../lib/contract';
import { mockIgnition, mockSpread, mockWeather, type Scenario } from '../mocks/fixtures';

export interface ApiAdapter {
  readonly name: string;
  /** True when the adapter serves MOCK data. The UI labels everything MOCK when true. */
  readonly isMock: boolean;
  getIgnition(): Promise<unknown>;
  getWeather(point: LonLat | null): Promise<unknown>;
  runSpread(point: LonLat): Promise<unknown>;
}

export interface MockAdapterOptions {
  getScenario: () => Scenario;
  getNow?: () => Date;
  latencyMs?: number;
}

export function createMockAdapter(opts: MockAdapterOptions): ApiAdapter {
  const now = opts.getNow ?? (() => new Date());
  const delay = () => new Promise<void>((r) => setTimeout(r, opts.latencyMs ?? 150));
  return {
    name: 'MOCK adapter (contracts/examples + synthetic fixtures)',
    isMock: true,
    async getIgnition() {
      await delay();
      return mockIgnition(opts.getScenario(), now());
    },
    async getWeather(point) {
      await delay();
      return mockWeather(opts.getScenario(), now(), point);
    },
    async runSpread(point) {
      await delay();
      return mockSpread(opts.getScenario(), now(), point);
    },
  };
}

async function load<T>(
  what: string,
  fetcher: () => Promise<unknown>,
  validate: (d: unknown) => { valid: true; data: T } | { valid: false; errors: string[] },
): Promise<Loaded<T>> {
  let raw: unknown;
  try {
    raw = await fetcher();
  } catch (e) {
    return { ok: false, kind: 'unavailable', title: `${what}: request failed`, errors: [e instanceof Error ? e.message : String(e)] };
  }
  const v = validate(raw);
  if (v.valid) return { ok: true, data: v.data };
  return { ok: false, kind: 'invalid', title: `${what}: payload failed contract validation`, errors: v.errors };
}

export function createApiClient(adapter: ApiAdapter) {
  return {
    adapter,
    loadIgnition: (): Promise<Loaded<IgnitionRecord[]>> => load('Ignition risk', () => adapter.getIgnition(), validateIgnitionArray),
    loadWeather: (point: LonLat | null = null): Promise<Loaded<WeatherRecord>> =>
      load('Live weather', () => adapter.getWeather(point), validateWeatherRecord),
    runSpread: (point: LonLat): Promise<Loaded<SpreadOutput>> =>
      load('Spread run', () => adapter.runSpread(point), validateSpreadOutput),
  };
}
export type ApiClient = ReturnType<typeof createApiClient>;
