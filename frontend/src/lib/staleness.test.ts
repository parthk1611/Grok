import { describe, expect, it } from 'vitest';
import { assessOutput, assessWeather, classifyAge, maxLevel } from './staleness';
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

describe('assessOutput (ignition / spread: no stale_level in the contract)', () => {
  const now = new Date('2026-07-15T16:00:00Z');
  it('fresh when flag false and input is recent; mode is unknown', () => {
    const a = assessOutput({ stale_flag: false, timestamp: '2026-07-15T15:50:00Z', ageBasis: 'data_timestamp', now });
    expect(a.level).toBe('fresh');
    expect(a.mode).toBe('unknown');
    expect(a.ageMinutes).toBe(10);
    expect(a.reasons).toEqual([]);
  });
  it('stale_flag true forces at least stale even if the timestamp looks fresh', () => {
    const a = assessOutput({ stale_flag: true, stale_reason: 'feed late', timestamp: '2026-07-15T15:55:00Z', ageBasis: 'data_timestamp', now });
    expect(a.level).toBe('stale');
    expect(a.reasons[0]).toContain('feed late');
  });
  it('flag true without a reason says so', () => {
    const a = assessOutput({ stale_flag: true, stale_reason: null, timestamp: '2026-07-15T15:55:00Z', ageBasis: 'data_timestamp', now });
    expect(a.reasons[0]).toContain('no stale_reason given');
  });
  it('old input becomes expired even when flag is false', () => {
    const a = assessOutput({ stale_flag: false, timestamp: '2026-07-15T08:00:00Z', ageBasis: 'weather_valid_time', now });
    expect(a.level).toBe('expired');
    expect(a.reasons[0]).toContain('weather_valid_time');
  });
  it('unparseable timestamp is expired', () => {
    const a = assessOutput({ stale_flag: false, timestamp: 'nope', ageBasis: 'data_timestamp', now });
    expect(a.level).toBe('expired');
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
