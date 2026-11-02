import { describe, expect, it } from 'vitest';
import { spreadConfirmationReasons } from './gate';
import { mockWeather } from '../mocks/fixtures';
import { createApiClient, createMockAdapter } from '../api/client';
import type { WeatherRecord } from '../types';

const NOW = new Date('2026-11-02T20:00:00Z');
const ok = (scenario: Parameters<typeof mockWeather>[0]) => ({ ok: true as const, data: mockWeather(scenario, NOW, null) as WeatherRecord });

describe('spread confirmation gate', () => {
  it('fresh and stale weather run without confirmation', () => {
    expect(spreadConfirmationReasons(ok('fresh'))).toEqual([]);
    expect(spreadConfirmationReasons(ok('stale'))).toEqual([]);
  });
  it('expired weather requires confirmation', () => {
    const r = spreadConfirmationReasons(ok('expired'));
    expect(r).toHaveLength(1);
    expect(r[0]).toContain('EXPIRED');
  });
  it('unusable weather requires confirmation', async () => {
    const c = createApiClient(createMockAdapter({ getScenario: () => 'invalid', latencyMs: 0, getNow: () => NOW }));
    const wx = await c.loadWeather(null);
    expect(wx.ok).toBe(false);
    expect(spreadConfirmationReasons(wx)).toHaveLength(1);
  });
});
