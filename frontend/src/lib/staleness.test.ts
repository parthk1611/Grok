import { describe, expect, it } from 'vitest';
import { assessIgnition, assessSpread, assessWeather, classifyAge, maxLevel } from './staleness';
import { STALENESS_THRESHOLDS } from '../config';

describe('classifyAge (proposed thresholds 30 min / 6 h from cache_fallback.md)', () => {
  it('uses the configured thresholds', () => {
    expect(STALENESS_THRESHOLDS.freshMaxMinutes).toBe(30);
    expect(STALENESS_THRESHOLDS.staleMaxMinutes).toBe(360);
    expect(STALENESS_THRESHOLDS.status).toBe('proposed');
  });
  it('boundaries are inclusive on the lower level', () => {
    expect(classifyAge(0)).toBe('fresh');
    expect(classifyAge(30)).toBe('fresh');
    expect(classifyAge(30.01)).toBe('stale');
    expect(classifyAge(360)).toBe('stale');
    expect(classifyAge(360.01)).toBe('expired');
    expect(classifyAge(10_000)).toBe('expired');
  });
  it('treats unknown age as expired, never as current', () => {
    expect(classifyAge(NaN)).toBe('expired');
  });
  it('maxLevel orders fresh < stale < expired', () => {
    expect(maxLevel('fresh', 'stale')).toBe('stale');
    expect(maxLevel('expired', 'stale', 'fresh')).toBe('expired');
    expect(maxLevel()).toBe('fresh');
  });
});

describe('assessIgnition / assessSpread (v0.2.0: stale_level + age_minutes + mode in the payload)', () => {
  it('fresh payload: fresh, no reasons, mode passed through', () => {
    const a = assessIgnition({ stale_level: 'fresh', age_minutes: 10, stale_flag: false, mode: 'retrospective' });
    expect(a.level).toBe('fresh');
    expect(a.mode).toBe('retrospective');
    expect(a.reasons).toEqual([]);
    expect(a.levelMismatch).toBe(false);
  });
  it('declared stale with a reason is shown as stale and cites the reason', () => {
    const a = assessIgnition({ stale_level: 'stale', age_minutes: 120, stale_flag: true, stale_reason: 'feed late', mode: 'forecast' });
    expect(a.level).toBe('stale');
    expect(a.reasons.join(' ')).toContain('feed late');
  });
  it('flag true without a reason says so', () => {
    const a = assessIgnition({ stale_level: 'stale', age_minutes: 60, stale_flag: true, stale_reason: null, mode: 'forecast' });
    expect(a.reasons[0]).toContain('no stale_reason given');
  });
  it('declared expired stays expired even if age_minutes is small', () => {
    expect(assessIgnition({ stale_level: 'expired', age_minutes: 5, stale_flag: true, mode: 'forecast' }).level).toBe('expired');
  });
  it('data age beyond the generic limits does NOT change the declared level (per-source limits are TBD) but is noted', () => {
    const a = assessIgnition({ stale_level: 'fresh', age_minutes: 500, stale_flag: false, mode: 'forecast' });
    expect(a.level).toBe('fresh');
    expect(a.dataAgeExceedsGenericLimits).toBe(true);
    expect(a.reasons.join(' ')).toContain('TBD');
  });
  it('spread reads stale_flag / stale_reason from inputs', () => {
    const a = assessSpread({ stale_level: 'stale', age_minutes: 90, mode: 'hindcast', inputs: { stale_flag: true, stale_reason: 'wx cached' } });
    expect(a.level).toBe('stale');
    expect(a.stale_flag).toBe(true);
    expect(a.reasons.join(' ')).toContain('wx cached');
    expect(a.mode).toBe('hindcast');
  });
  it('NaN data age is flagged as exceeding limits', () => {
    expect(assessSpread({ stale_level: 'expired', age_minutes: NaN, mode: 'hindcast', inputs: { stale_flag: true } }).level).toBe('expired');
  });
});

describe('assessWeather (v0.2.1: age_minutes = data age, fetch_age_minutes = fetch health)', () => {
  const base = { from_cache: true, kind: 'observation' } as const;
  it('honours a consistent declared level and reports both ages', () => {
    const a = assessWeather({ ...base, stale_level: 'stale', age_minutes: 120, fetch_age_minutes: 45 });
    expect(a.level).toBe('stale');
    expect(a.ageMinutes).toBe(120);
    expect(a.fetchAgeMinutes).toBe(45);
    expect(a.fetchLevel).toBe('stale');
    expect(a.levelMismatch).toBe(false);
    expect(a.mode).toBe('observation, from cache');
  });
  it('fetch age absent: undefined, no cross-check', () => {
    const a = assessWeather({ ...base, stale_level: 'fresh', age_minutes: 10 });
    expect(a.fetchAgeMinutes).toBeUndefined();
    expect(a.fetchLevel).toBeUndefined();
    expect(a.level).toBe('fresh');
  });
  it('unhealthy fetch raises the displayed level above a (wrongly) fresher declared one', () => {
    const a = assessWeather({ ...base, stale_level: 'fresh', age_minutes: 10, fetch_age_minutes: 500 });
    expect(a.level).toBe('expired');
    expect(a.levelMismatch).toBe(true);
    expect(a.reasons.join(' ')).toContain('fetch age');
  });
  it('declared expired stays expired even with small ages', () => {
    expect(assessWeather({ ...base, stale_level: 'expired', age_minutes: 5, fetch_age_minutes: 5 }).level).toBe('expired');
  });
  it('a 5.5 h old forecast run declared fresh stays fresh (HRDPS-style; data-age limit is per source)', () => {
    const a = assessWeather({ from_cache: false, kind: 'forecast', stale_level: 'fresh', age_minutes: 330, fetch_age_minutes: 5 });
    expect(a.level).toBe('fresh');
    expect(a.ageBasis).toContain('source_run_time');
    expect(a.dataAgeExceedsGenericLimits).toBe(true);
  });
});
