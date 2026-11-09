import { describe, expect, it } from 'vitest';
import { ERROR_REASONS, parseApiError, reasonMessage } from './apiError';
import { DISCLAIMER } from './contract';
import { checkIgnitionDates } from './ignitionDate';
import { manualRunWarnings, MANUAL_STALE_REASON, parseOverride } from './manual';
import { mockIgnition, mockSpread } from '../mocks/fixtures';
import type { IgnitionRecord, SpreadOutput } from '../types';

const NOW = new Date('2026-11-02T20:00:00Z');

describe('error model', () => {
  it('closed enum includes no_cached_data and confirmation_required, and every reason has a message', () => {
    expect(ERROR_REASONS).toContain('no_cached_data');
    expect(ERROR_REASONS).toContain('confirmation_required');
    for (const r of ERROR_REASONS) expect(reasonMessage(r)).not.toMatch(/Unrecognised/);
    expect(reasonMessage('degraded_confirmation_required')).toMatch(/Unrecognised/);
  });
  it('a conforming body has no problems; last_cache_time may be a Z time or null', () => {
    for (const lct of [null, '2026-11-02T19:45:00Z']) {
      const p = parseApiError({ status: 409, error: 'e', reason: 'confirmation_required', last_cache_time: lct, request_id: 'r', disclaimer: DISCLAIMER }, 409);
      expect(p.problems).toEqual([]);
      expect(p.body?.last_cache_time).toBe(lct);
    }
  });
  it('last_cache_time that is not ISO-Z, extra fields and a status outside the list are problems', () => {
    const p = parseApiError({ status: 418, error: 'e', reason: 'internal_error', last_cache_time: '2026-11-02 19:45', request_id: 'r', disclaimer: DISCLAIMER, trace: 'x' }, 418);
    expect(p.problems.some((x) => x.includes('not in 400/404/405/409/500/503/504'))).toBe(true);
    expect(p.problems.some((x) => x.includes('unexpected field "trace"'))).toBe(true);
    expect(p.problems.some((x) => x.includes('last_cache_time'))).toBe(true);
  });
});

describe('ignition date check', () => {
  const recs = (date: string, stale: boolean) => (mockIgnition('fresh', NOW) as IgnitionRecord[]).map((r) => ({ ...r, date, stale_flag: stale, stale_level: stale ? ('stale' as const) : ('fresh' as const) }));
  it('same day => no banner', () => {
    expect(checkIgnitionDates(recs('2026-11-02', false), '2026-11-02').banner).toBeNull();
  });
  it('earlier day => banner with the records\' own date', () => {
    const c = checkIgnitionDates(recs('2026-11-01', true), '2026-11-02');
    expect(c.banner).toBe('Showing 2026-11-01; requested 2026-11-02 unavailable');
    expect(c.warnings).toEqual([]);
  });
  it('later day than requested => warning (server must answer at or before D)', () => {
    expect(checkIgnitionDates(recs('2026-11-03', true), '2026-11-02').warnings.join(' ')).toMatch(/after the requested/);
  });
  it('latest => no banner, no date warnings', () => {
    const c = checkIgnitionDates(recs('2026-10-30', true), 'latest');
    expect(c.banner).toBeNull();
    expect(c.warnings).toEqual([]);
  });
});

describe('manual override form + response checks', () => {
  it('requires wind speed and direction; omits empty optionals; enforces ranges and numbers', () => {
    expect(parseOverride({})).toMatchObject({ ok: false, errors: { wind_speed_kmh: expect.any(String), wind_direction_deg: expect.any(String) } });
    expect(parseOverride({ wind_speed_kmh: '12.5', wind_direction_deg: '360', ffmc: '' })).toEqual({ ok: true, value: { wind_speed_kmh: 12.5, wind_direction_deg: 360 } });
    const bad = parseOverride({ wind_speed_kmh: '-1', wind_direction_deg: '361', ffmc: '102', rh_pct: '1e2', temp_c: '-60' });
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(Object.keys(bad.errors).sort()).toEqual(['ffmc', 'rh_pct', 'temp_c', 'wind_direction_deg', 'wind_speed_kmh']);
  });
  it('mock manual response passes the ruling checks', () => {
    const o = { wind_speed_kmh: 30, wind_direction_deg: 180 };
    const out = mockSpread('fresh', NOW, [-86, 50], o) as SpreadOutput;
    expect(manualRunWarnings(out, o)).toEqual([]);
    expect(out.inputs.stale_reason).toBe(MANUAL_STALE_REASON);
  });
  it('a manual response labelled fresh / wrong reason / not echoing wind is flagged', () => {
    const o = { wind_speed_kmh: 30, wind_direction_deg: 180 };
    const out = mockSpread('fresh', NOW, [-86, 50], o) as SpreadOutput;
    out.stale_level = 'fresh';
    out.inputs.stale_flag = false;
    out.inputs.stale_reason = 'manual weather input; not from a live feed';
    out.inputs.wind_speed_kmh = 31;
    const w = manualRunWarnings(out, o).join(' | ');
    expect(w).toMatch(/stale_level "fresh"/);
    expect(w).toMatch(/stale_flag false/);
    expect(w).toMatch(/stale_reason/);
    expect(w).toMatch(/does not echo the entered 30/);
  });
});
