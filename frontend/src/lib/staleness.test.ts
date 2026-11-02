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
  it('never shows fresher than age_minutes says (worse of declared and recomputed), and notes the disagreement', () => {
    const a = assessIgnition({ stale_level: 'fresh', age_minutes: 500, stale_flag: false, mode: 'forecast' });
    expect(a.level).toBe('expired');
    expect(a.levelMismatch).toBe(true);
    expect(a.reasons.join(' ')).toContain('TBD');
  });
  it('spread reads stale_flag / stale_reason from inputs', () => {
    const a = assessSpread({ stale_level: 'stale', age_minutes: 90, mode: 'hindcast', inputs: { stale_flag: true, stale_reason: 'wx cached' } });
    expect(a.level).toBe('stale');
    expect(a.stale_flag).toBe(true);
    expect(a.reasons.join(' ')).toContain('wx cached');
    expect(a.mode).toBe('hindcast');
  });
  it('unknown age is treated as expired', () => {
    expect(assessSpread({ stale_level: 'fresh', age_minutes: NaN, mode: 'hindcast', inputs: { stale_flag: false } }).level).toBe('expired');
  });
});

describe('assessWeather (stale_level declared in the contract)', () => {
  it('honours a consistent declared level', () => {
    const a = assessWeather({ stale_level: 'stale', age_minutes: 120, from_cache: true, kind: 'observation' });
    expect(a.level).toBe('stale');
    expect(a.levelMismatch).toBe(false);
    expect(a.mode).toBe('observation, from cache');
  });
  it('shows the worse level when declared and recomputed disagree', () => {
    const a = assessWeather({ stale_level: 'fresh', age_minutes: 500, from_cache: false, kind: 'forecast' });
    expect(a.level).toBe('expired');
    expect(a.levelMismatch).toBe(true);
  });
  it('declared expired stays expired even with a small age', () => {
    expect(assessWeather({ stale_level: 'expired', age_minutes: 5, from_cache: true, kind: 'observation' }).level).toBe('expired');
  });
});
