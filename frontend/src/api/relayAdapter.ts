// HTTP adapter for RELAY's API (contracts/openapi.yaml draft on RELAY's branch + Team Lead rulings).
// Optional: selected with VITE_API_ADAPTER=relay and VITE_API_BASE_URL. The default stays the in-browser MOCK.
// The adapter only moves bytes: status, JSON body and headers go to the client unchanged, which validates them.
import type { AdapterResponse, ApiAdapter } from './client';
import type { LonLat } from '../geo/cell';
import type { SpreadRequestBody } from '../types';

export interface RelayAdapterOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  /** Per-request timeout. Spread is synchronous on the server with a 60 s limit, so allow a little more. */
  timeoutMs?: number;
}

async function toAdapterResponse(res: Response): Promise<AdapterResponse> {
  const headers: Record<string, string> = {};
  res.headers.forEach((v, k) => {
    headers[k.toLowerCase()] = v;
  });
  const text = await res.text();
  let body: unknown = text;
  if (text.length > 0) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text; // non-JSON body: the client reports it as an off-model error/payload
    }
  } else body = null;
  return { status: res.status, body, headers };
}

export function createRelayAdapter(opts: RelayAdapterOptions): ApiAdapter {
  const base = opts.baseUrl.replace(/\/+$/, '');
  const f = opts.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  const timeoutMs = opts.timeoutMs ?? 70_000;
  const call = async (path: string, init?: RequestInit): Promise<AdapterResponse> => {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await f(`${base}${path}`, { ...init, signal: ctl.signal, headers: { Accept: 'application/json', ...(init?.headers ?? {}) } });
      return await toAdapterResponse(res);
    } finally {
      clearTimeout(t);
    }
  };
  return {
    name: `RELAY HTTP adapter (${base})`,
    // The adapter cannot know; MOCK labelling comes from X-Mock-Data / envelope `mock` per response,
    // and the app keeps the MOCK banner until a response explicitly says it is not mock.
    isMock: false,
    getIgnition: ({ date, envelope }) => {
      const q = new URLSearchParams({ date });
      if (envelope) q.set('envelope', 'true');
      return call(`/api/v1/ignition?${q.toString()}`);
    },
    getWeather: (point: LonLat | null) => {
      if (!point) return Promise.reject(new Error('RELAY weather needs a point (lat/lon) or a cell_id'));
      const q = new URLSearchParams({ lat: String(point[1]), lon: String(point[0]) });
      return call(`/api/v1/weather/latest?${q.toString()}`);
    },
    runSpread: (body: SpreadRequestBody) => call('/api/v1/spread', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  };
}
