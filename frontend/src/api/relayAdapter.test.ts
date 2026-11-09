import { describe, expect, it } from 'vitest';
import { createRelayAdapter } from './relayAdapter';
import { createApiClient } from './client';
import { readApiConfig } from './index';
import ignitionExample from '../../../contracts/examples/ignition_output.example.json';

interface Call { url: string; init?: RequestInit }
function fakeFetch(respond: (c: Call) => Response) {
  const calls: Call[] = [];
  const impl = ((url: string, init?: RequestInit) => {
    const c = { url, init };
    calls.push(c);
    return Promise.resolve(respond(c));
  }) as unknown as typeof fetch;
  return { calls, impl };
}
const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });

describe('RELAY HTTP adapter', () => {
  it('builds the ignition URL with date and optional envelope; strips a trailing slash', async () => {
    const { calls, impl } = fakeFetch(() => json(200, [ignitionExample], { 'X-Mock-Data': 'true', 'X-Request-ID': 'abcdefgh1234' }));
    const a = createRelayAdapter({ baseUrl: 'http://127.0.0.1:8765/', fetchImpl: impl });
    const r = await a.getIgnition({ date: '2026-07-15', envelope: false });
    await a.getIgnition({ date: 'latest', envelope: true });
    expect(calls[0].url).toBe('http://127.0.0.1:8765/api/v1/ignition?date=2026-07-15');
    expect(calls[1].url).toBe('http://127.0.0.1:8765/api/v1/ignition?date=latest&envelope=true');
    expect(r.status).toBe(200);
    expect(r.headers?.['x-mock-data']).toBe('true');
    expect(r.headers?.['x-request-id']).toBe('abcdefgh1234');
  });
  it('weather uses lat/lon from the clicked point; no point => rejected (unavailable)', async () => {
    const { calls, impl } = fakeFetch(() => json(200, {}));
    const a = createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl: impl });
    await a.getWeather([-86.5, 50.25]);
    expect(calls[0].url).toBe('http://h:1/api/v1/weather/latest?lat=50.25&lon=-86.5');
    await expect(a.getWeather(null)).rejects.toThrow(/needs a point/);
  });
  it('spread is a JSON POST carrying confirm_degraded / weather_override unchanged', async () => {
    const { calls, impl } = fakeFetch(() => json(409, {}));
    const a = createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl: impl });
    const body = { longitude: -86, latitude: 50, start_time: '2026-11-02T20:00:00Z', weather_override: { wind_speed_kmh: 10, wind_direction_deg: 90 } };
    const r = await a.runSpread(body);
    expect(calls[0].url).toBe('http://h:1/api/v1/spread');
    expect(calls[0].init?.method).toBe('POST');
    expect(JSON.parse(calls[0].init?.body as string)).toEqual(body);
    expect(r.status).toBe(409);
  });
  it('non-JSON bodies are passed through as text for the client to report', async () => {
    const { impl } = fakeFetch(() => new Response('<html>502</html>', { status: 502 }));
    const c = createApiClient(createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl: impl }));
    const r = await c.loadIgnition('2026-07-15');
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.kind).toBe('api_error');
      expect(r.httpStatus).toBe(502);
    }
  });
  it('X-Mock-Data: true marks the response mock even though the adapter is not', async () => {
    const { impl } = fakeFetch(() => json(200, [ignitionExample], { 'X-Mock-Data': 'true' }));
    const c = createApiClient(createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl: impl }));
    const r = await c.loadIgnition('2026-07-15');
    expect(c.adapter.isMock).toBe(false);
    expect(r.ok && r.meta?.mock).toBe(true);
  });
});

describe('readApiConfig', () => {
  it('defaults to the in-browser MOCK', () => {
    expect(readApiConfig({})).toEqual({ adapter: 'mock', baseUrl: null, envelope: false, configError: null });
  });
  it('relay with a base URL', () => {
    expect(readApiConfig({ VITE_API_ADAPTER: 'relay', VITE_API_BASE_URL: 'http://127.0.0.1:8765', VITE_API_ENVELOPE: 'true' })).toEqual({
      adapter: 'relay',
      baseUrl: 'http://127.0.0.1:8765',
      envelope: true,
      configError: null,
    });
  });
  it('relay without a usable base URL falls back to MOCK and says why', () => {
    const c = readApiConfig({ VITE_API_ADAPTER: 'relay' });
    expect(c.adapter).toBe('mock');
    expect(c.configError).toMatch(/VITE_API_BASE_URL/);
    expect(readApiConfig({ VITE_API_ADAPTER: 'grpc' }).configError).toMatch(/not mock\|relay/);
  });
});
