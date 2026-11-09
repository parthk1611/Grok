// Typed API client. Adapters return {status, body, headers}; the client validates every 2xx payload with
// Ajv against the /contracts schemas, parses every error body against the error model, and returns an
// explicit result (never throws, never passes unvalidated data on, never patches a deviation).
import type { LonLat } from '../geo/cell';
import type { IgnitionRecord, Loaded, ResponseMeta, SpreadOutput, SpreadRequestBody, WeatherOverride, WeatherRecord } from '../types';
import { validateSpreadOutput, validateWeatherRecord, type ValidationResult } from '../lib/contract';
import { parseIgnitionPayload, type IgnitionEnvelopeMeta } from '../lib/envelope';
import { checkIgnitionDates, type DateCheck, type RequestedDate } from '../lib/ignitionDate';
import { parseApiError, reasonMessage } from '../lib/apiError';
import { manualRunWarnings } from '../lib/manual';

export interface AdapterResponse {
  status: number;
  body: unknown;
  /** Lower-case header names. */
  headers?: Record<string, string>;
}

export interface IgnitionQuery {
  date: RequestedDate;
  /** Ask for the opt-in envelope (?envelope=true). The client accepts either shape regardless. */
  envelope: boolean;
}

export interface ApiAdapter {
  readonly name: string;
  /** True when the adapter itself serves MOCK data. The UI labels everything MOCK when true. */
  readonly isMock: boolean;
  getIgnition(q: IgnitionQuery): Promise<AdapterResponse>;
  getWeather(point: LonLat | null): Promise<AdapterResponse>;
  runSpread(body: SpreadRequestBody): Promise<AdapterResponse>;
}

export interface IgnitionResult {
  records: IgnitionRecord[];
  envelope: IgnitionEnvelopeMeta | null;
  requested: RequestedDate;
  dates: DateCheck;
}

export interface SpreadOptions {
  confirmDegraded?: boolean;
  override?: WeatherOverride;
  /** Defaults to now (UTC, whole seconds). */
  startTime?: string;
  nRuns?: number;
  seed?: number;
}

export const isoNowZ = (d = new Date()) => d.toISOString().replace(/\.\d{3}Z$/, 'Z');

function metaFrom(res: AdapterResponse, extra: Partial<ResponseMeta> = {}): ResponseMeta {
  const h = res.headers ?? {};
  const mockHeader = h['x-mock-data'];
  return {
    warnings: [],
    ...(mockHeader === 'true' ? { mock: true } : mockHeader === 'false' ? { mock: false } : {}),
    ...(h['x-request-id'] ? { requestId: h['x-request-id'] } : {}),
    ...extra,
  };
}

async function load<T>(
  what: string,
  fetcher: () => Promise<AdapterResponse>,
  validate: (d: unknown) => ValidationResult<T>,
  after?: (data: T, meta: ResponseMeta) => void,
): Promise<Loaded<T>> {
  let res: AdapterResponse;
  try {
    res = await fetcher();
  } catch (e) {
    return { ok: false, kind: 'unavailable', title: `${what}: request failed`, errors: [e instanceof Error ? e.message : String(e)] };
  }
  const meta = metaFrom(res);
  if (res.status < 200 || res.status >= 300) {
    const p = parseApiError(res.body, res.status);
    return {
      ok: false,
      kind: 'api_error',
      title: `${what}: ${p.body ? reasonMessage(p.body.reason) : `HTTP ${res.status}`}`,
      errors: p.problems.map((x) => `error body: ${x}`),
      httpStatus: res.status,
      apiError: p.body,
      meta,
    };
  }
  const v = validate(res.body);
  if (!v.valid) return { ok: false, kind: 'invalid', title: `${what}: payload failed contract validation`, errors: v.errors, meta };
  after?.(v.data, meta);
  return { ok: true, data: v.data, meta };
}

export function createApiClient(adapter: ApiAdapter, opts: { envelope?: boolean } = {}) {
  return {
    adapter,
    loadIgnition: (date: RequestedDate = 'latest'): Promise<Loaded<IgnitionResult>> =>
      load(
        'Ignition risk',
        () => adapter.getIgnition({ date, envelope: opts.envelope ?? false }),
        (raw) => {
          const p = parseIgnitionPayload(raw);
          if (!p.valid) return p;
          return {
            valid: true,
            data: { records: p.data.records, envelope: p.data.envelope, requested: date, dates: checkIgnitionDates(p.data.records, date, p.data.envelope?.requested_date) },
          };
        },
        (data, meta) => {
          meta.warnings.push(...data.dates.warnings);
          if (data.envelope?.mock !== undefined) {
            if (meta.mock !== undefined && meta.mock !== data.envelope.mock) meta.warnings.push(`envelope mock=${data.envelope.mock} disagrees with X-Mock-Data=${meta.mock}`);
            meta.mock = meta.mock || data.envelope.mock;
          }
        },
      ),
    loadWeather: (point: LonLat | null = null): Promise<Loaded<WeatherRecord>> => load('Live weather', () => adapter.getWeather(point), validateWeatherRecord),
    runSpread: (point: LonLat, o: SpreadOptions = {}): Promise<Loaded<SpreadOutput>> => {
      const body: SpreadRequestBody = {
        longitude: Number(point[0].toFixed(6)),
        latitude: Number(point[1].toFixed(6)),
        start_time: o.startTime ?? isoNowZ(),
        ...(o.nRuns !== undefined ? { n_runs: o.nRuns } : {}),
        ...(o.seed !== undefined ? { seed: o.seed } : {}),
        ...(o.confirmDegraded ? { confirm_degraded: true as const } : {}),
        ...(o.override ? { weather_override: o.override } : {}),
      };
      return load('Spread run', () => adapter.runSpread(body), validateSpreadOutput, (out, meta) => {
        meta.warnings.push(...manualRunWarnings(out, o.override));
      });
    },
  };
}
export type ApiClient = ReturnType<typeof createApiClient>;

export { createMockAdapter, type MockAdapterOptions } from '../mocks/mockServer';
