import { describe, expect, it } from 'vitest';
import { createApiClient, createMockAdapter, type AdapterResponse, type ApiAdapter } from './client';
import type { DevScenario } from '../mocks/fixtures';
import { DISCLAIMER } from '../lib/contract';
import { MANUAL_STALE_REASON } from '../lib/manual';
import { torontoDate } from '../lib/time';
import ignitionExample from '../../../contracts/examples/ignition_output.example.json';
import spreadExample from '../../../contracts/examples/spread_output.example.json';

const NOW = new Date('2026-11-02T20:00:00Z');
const TODAY = torontoDate(NOW); // 2026-11-02
const YESTERDAY = '2026-11-01';
const mk = (scenario: DevScenario, envelope = false) =>
  createApiClient(createMockAdapter({ getScenario: () => scenario, latencyMs: 0, getNow: () => NOW }), { envelope });

/** Adapter stub that answers every call with one fixed response. */
const fixed = (res: AdapterResponse | Error): ApiAdapter => {
  const f = () => (res instanceof Error ? Promise.reject(res) : Promise.resolve(res));
  return { name: 'stub', isMock: true, getIgnition: f, getWeather: f, runSpread: f };
};
const errBody = (over: Record<string, unknown> = {}) => ({
  status: 503,
  error: 'x',
  reason: 'no_cached_data',
  last_cache_time: null,
  request_id: 'req-12345678',
  disclaimer: DISCLAIMER,
  ...over,
});

describe('typed API client + mock adapter', () => {
  it('fresh scenario loads all three payload types as ok', async () => {
    const c = mk('fresh');
    const ig = await c.loadIgnition(TODAY);
    expect(ig.ok).toBe(true);
    if (ig.ok) expect(ig.meta?.mock).toBe(true);
    expect((await c.loadWeather(null)).ok).toBe(true);
    const sp = await c.runSpread([-90, 50]);
    expect(sp.ok).toBe(true);
    if (sp.ok) expect(sp.data.features).toHaveLength(9);
  });

  it('invalid scenario returns explicit error results with messages, never data', async () => {
    const c = mk('invalid');
    for (const r of [await c.loadIgnition(), await c.loadWeather(null), await c.runSpread([-90, 50])]) {
      expect(r.ok).toBe(false);
      if (!r.ok) {
        expect(r.kind).toBe('invalid');
        expect(r.errors.length).toBeGreaterThan(0);
      }
    }
  });

  it('adapter exceptions become "unavailable" results', async () => {
    const c = createApiClient(fixed(new Error('network down')));
    for (const r of [await c.loadIgnition(), await c.loadWeather(), await c.runSpread([0, 0])]) {
      expect(r.ok).toBe(false);
      if (!r.ok) {
        expect(r.kind).toBe('unavailable');
        expect(r.errors[0]).toBe('network down');
      }
    }
  });
});

describe('ruling 1: spread request, 409 confirmation_required, manual inputs (mock adapter)', () => {
  it('expired weather without confirm/override => 409 confirmation_required with last_cache_time and request_id', async () => {
    const r = await mk('expired').runSpread([-86, 50]);
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.kind).toBe('api_error');
    expect(r.httpStatus).toBe(409);
    expect(r.apiError?.reason).toBe('confirmation_required');
    expect(r.apiError?.last_cache_time).toMatch(/Z$/);
    expect(r.apiError?.request_id).toMatch(/^mock-/);
    expect(r.errors).toEqual([]); // body conforms to the error model
  });
  it('expired + confirm_degraded => 200, expired output', async () => {
    const r = await mk('expired').runSpread([-86, 50], { confirmDegraded: true });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.data.stale_level).toBe('expired');
  });
  it('expired + weather_override => 200 manual output: weather_source manual, stale, stale_flag, exact reason, echoed values', async () => {
    const override = { wind_speed_kmh: 22, wind_direction_deg: 225, ffmc: 88, rh_pct: 35 };
    const r = await mk('expired').runSpread([-86, 50], { override });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.data.inputs.weather_source).toBe('manual');
    expect(r.data.stale_level).toBe('stale');
    expect(r.data.inputs.stale_flag).toBe(true);
    expect(r.data.inputs.stale_reason).toBe(MANUAL_STALE_REASON);
    expect(r.data.inputs.wind_speed_kmh).toBe(22);
    expect(r.data.inputs.ffmc).toBe(88);
    expect(r.data.inputs.bui).toBeNull();
    expect(r.meta?.warnings).toEqual([]);
  });
  it('the request body carries confirm_degraded / weather_override only when set', async () => {
    const seen: unknown[] = [];
    const a: ApiAdapter = { ...fixed({ status: 200, body: spreadExample }), runSpread: (b) => (seen.push(b), Promise.resolve({ status: 200, body: spreadExample })) };
    const c = createApiClient(a);
    await c.runSpread([-85, 50], { startTime: '2026-11-02T20:00:00Z' });
    await c.runSpread([-85, 50], { confirmDegraded: true, startTime: '2026-11-02T20:00:00Z' });
    await c.runSpread([-85, 50], { override: { wind_speed_kmh: 10, wind_direction_deg: 90 }, startTime: '2026-11-02T20:00:00Z' });
    expect(seen[0]).toEqual({ longitude: -85, latitude: 50, start_time: '2026-11-02T20:00:00Z' });
    expect(seen[1]).toMatchObject({ confirm_degraded: true });
    expect(seen[2]).toMatchObject({ weather_override: { wind_speed_kmh: 10, wind_direction_deg: 90 } });
    expect(seen[2]).not.toHaveProperty('confirm_degraded');
  });
  it('a server that ignores the override (weather_source not manual) is reported, not patched', async () => {
    const c = createApiClient(fixed({ status: 200, body: spreadExample }));
    const r = await c.runSpread([-90, 50], { override: { wind_speed_kmh: 10, wind_direction_deg: 90 } });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.inputs.weather_source).toBe('example-placeholder');
      expect(r.meta?.warnings.join(' ')).toMatch(/weather_override was sent/);
    }
  });
});

describe('ruling 2: ignition stale day, date=latest, 404 only when nothing at or before D', () => {
  it('fresh: requesting today returns today, no substitution', async () => {
    const r = await mk('fresh').loadIgnition(TODAY);
    expect(r.ok && r.data.dates.substituted).toBe(false);
    if (r.ok) expect(r.data.dates.shownDates).toEqual([TODAY]);
  });
  it('stale_day: requesting today returns yesterday\'s records with their own date + stale_flag, and a banner', async () => {
    const r = await mk('stale_day').loadIgnition(TODAY);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.data.records.every((x) => x.date === YESTERDAY && x.stale_flag)).toBe(true);
    expect(r.data.dates.banner).toBe(`Showing ${YESTERDAY}; requested ${TODAY} unavailable`);
    expect(r.meta?.warnings).toEqual([]);
  });
  it('date=latest returns the newest map without a substitution banner', async () => {
    const r = await mk('stale_day').loadIgnition('latest');
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.dates.shownDates).toEqual([YESTERDAY]);
      expect(r.data.dates.banner).toBeNull();
    }
  });
  it('a date before the earliest map => 404 not_found', async () => {
    const r = await mk('stale_day').loadIgnition('2026-10-01');
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.httpStatus).toBe(404);
      expect(r.apiError?.reason).toBe('not_found');
    }
  });
  it('a future date returns the latest map flagged stale (records keep their date)', async () => {
    const r = await mk('fresh').loadIgnition('2026-12-25');
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.records.every((x) => x.date === TODAY && x.stale_flag && x.stale_level === 'stale')).toBe(true);
      expect(r.data.dates.substituted).toBe(true);
    }
  });
  it('server returning an earlier date WITHOUT stale_flag is reported as a deviation', async () => {
    const rec = { ...ignitionExample, date: '2026-07-14', stale_flag: false, stale_level: 'fresh' };
    const r = await createApiClient(fixed({ status: 200, body: [rec] })).loadIgnition('2026-07-15');
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.dates.banner).toBe('Showing 2026-07-14; requested 2026-07-15 unavailable');
      expect(r.meta?.warnings.join(' ')).toMatch(/stale_flag false/);
    }
  });
});

describe('ruling 3: error bodies', () => {
  it('no_cache: 503 no_cached_data with last_cache_time null for every endpoint', async () => {
    const c = mk('no_cache');
    for (const r of [await c.loadIgnition(TODAY), await c.loadWeather([-86, 50]), await c.runSpread([-86, 50])]) {
      expect(r.ok).toBe(false);
      if (r.ok) continue;
      expect(r.httpStatus).toBe(503);
      expect(r.apiError?.reason).toBe('no_cached_data');
      expect(r.apiError?.last_cache_time).toBeNull();
      expect(r.errors).toEqual([]);
    }
  });
  it('no_cache + manual override still runs (manual inputs need no cache)', async () => {
    const r = await mk('no_cache').runSpread([-86, 50], { override: { wind_speed_kmh: 5, wind_direction_deg: 0 } });
    expect(r.ok).toBe(true);
  });
  it('off-model bodies are reported field by field (unknown reason, missing request_id, wrong disclaimer, status mismatch)', async () => {
    const body = { status: 500, error: 'x', reason: 'http_error', last_cache_time: null, disclaimer: 'nope' };
    const r = await createApiClient(fixed({ status: 503, body })).loadWeather();
    expect(r.ok).toBe(false);
    if (r.ok) return;
    const all = r.errors.join(' | ');
    expect(all).toMatch(/not in the closed enum/);
    expect(all).toMatch(/missing required field "request_id"/);
    expect(all).toMatch(/disclaimer is not the exact contract constant/);
    expect(all).toMatch(/does not match HTTP status 503/);
    expect(r.apiError?.reason).toBe('http_error');
  });
  it('a non-JSON-object error body still yields an api_error result', async () => {
    const r = await createApiClient(fixed({ status: 502, body: '<html>bad gateway</html>' })).loadWeather();
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.kind).toBe('api_error');
      expect(r.apiError).toBeNull();
      expect(r.title).toMatch(/HTTP 502/);
    }
  });
  it('conforming body: title carries the per-reason message', async () => {
    const r = await createApiClient(fixed({ status: 503, body: errBody() })).loadWeather();
    expect(!r.ok && r.title).toMatch(/No cached data exists yet/);
  });
});

describe('ruling 4: plain array or opt-in envelope', () => {
  it('mock adapter serves the envelope when asked, and the client unwraps it', async () => {
    const r = await mk('stale_day', true).loadIgnition(TODAY);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.data.envelope).toMatchObject({ requested_date: TODAY, stale_level: 'stale', mock: true });
    expect(r.data.records.length).toBeGreaterThan(1);
    expect(r.meta?.mock).toBe(true);
  });
  it('envelope with one bad record fails, naming that record', async () => {
    const env = {
      mode: 'retrospective',
      stale_level: 'fresh',
      age_minutes: 5,
      data_timestamp: '2026-07-15T10:00:00Z',
      requested_date: '2026-07-15',
      records: [ignitionExample, { ...ignitionExample, probability: 2 }],
    };
    const r = await createApiClient(fixed({ status: 200, body: env })).loadIgnition('2026-07-15');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors.join(' ')).toMatch(/records\[1\].*probability/);
  });
  it('envelope with a bad envelope field fails', async () => {
    const env = { mode: 'live', stale_level: 'fresh', age_minutes: -1, data_timestamp: 'x', requested_date: '2026-07-15', records: [ignitionExample] };
    const r = await createApiClient(fixed({ status: 200, body: env })).loadIgnition('2026-07-15');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors.join(' ')).toMatch(/mode.*age_minutes.*data_timestamp/s);
  });
  it('envelope requested_date that does not echo the request is a warning', async () => {
    const env = { mode: 'retrospective', stale_level: 'fresh', age_minutes: 5, data_timestamp: '2026-07-15T10:00:00Z', requested_date: '2026-07-10', records: [ignitionExample] };
    const r = await createApiClient(fixed({ status: 200, body: env })).loadIgnition('2026-07-15');
    expect(r.ok && r.meta?.warnings.join(' ')).toMatch(/does not echo/);
  });
  it('X-Mock-Data header and envelope mock flag both mark the response MOCK', async () => {
    const r = await createApiClient(fixed({ status: 200, body: [ignitionExample], headers: { 'x-mock-data': 'true' } })).loadIgnition('2026-07-15');
    expect(r.ok && r.meta?.mock).toBe(true);
  });
});
