import { describe, expect, it } from 'vitest';
import { createApiClient, createMockAdapter, type ApiAdapter } from './client';
import type { Scenario } from '../mocks/fixtures';

const mk = (scenario: Scenario) => createApiClient(createMockAdapter({ getScenario: () => scenario, latencyMs: 0, getNow: () => new Date('2026-11-02T20:00:00Z') }));

describe('typed API client + mock adapter', () => {
  it('fresh scenario loads all three payload types as ok', async () => {
    const c = mk('fresh');
    expect((await c.loadIgnition()).ok).toBe(true);
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
    const adapter: ApiAdapter = {
      name: 'boom',
      isMock: true,
      getIgnition: () => Promise.reject(new Error('network down')),
      getWeather: () => Promise.resolve(null),
      runSpread: () => Promise.resolve({ type: 'FeatureCollection' }),
    };
    const c = createApiClient(adapter);
    const a = await c.loadIgnition();
    expect(a.ok).toBe(false);
    if (!a.ok) {
      expect(a.kind).toBe('unavailable');
      expect(a.errors[0]).toBe('network down');
    }
    const w = await c.loadWeather();
    expect(w.ok).toBe(false);
    const s = await c.runSpread([0, 0]);
    expect(s.ok).toBe(false);
  });
});
