import { describe, expect, it } from 'vitest';
import { ageMinutes, formatAge, formatToronto, formatUtc, torontoDate } from './time';

describe('America/Toronto formatting (DST-aware)', () => {
  it('summer is EDT (UTC-4)', () => {
    expect(formatToronto('2026-07-15T16:00:00Z')).toBe('2026-07-15 12:00 EDT');
  });
  it('winter is EST (UTC-5)', () => {
    expect(formatToronto('2026-01-15T16:00:00Z')).toBe('2026-01-15 11:00 EST');
  });
  it('spring forward 2026-03-08: 06:59Z is EST, 07:00Z is EDT (02:xx local does not exist)', () => {
    expect(formatToronto('2026-03-08T06:59:00Z')).toBe('2026-03-08 01:59 EST');
    expect(formatToronto('2026-03-08T07:00:00Z')).toBe('2026-03-08 03:00 EDT');
  });
  it('fall back 2026-11-01: 01:30 local occurs twice (EDT then EST)', () => {
    expect(formatToronto('2026-11-01T05:30:00Z')).toBe('2026-11-01 01:30 EDT');
    expect(formatToronto('2026-11-01T06:30:00Z')).toBe('2026-11-01 01:30 EST');
  });
  it('crosses the local date line correctly', () => {
    expect(formatToronto('2026-07-15T03:30:00Z')).toBe('2026-07-14 23:30 EDT');
    expect(torontoDate(new Date('2026-07-15T03:30:00Z'))).toBe('2026-07-14');
    expect(torontoDate(new Date('2026-01-15T04:59:00Z'))).toBe('2026-01-14');
    expect(torontoDate(new Date('2026-01-15T05:00:00Z'))).toBe('2026-01-15');
  });
  it('formats UTC explicitly', () => {
    expect(formatUtc('2026-07-15T16:00:00Z')).toBe('2026-07-15 16:00 UTC');
  });
  it('does not throw on garbage', () => {
    expect(formatToronto('not a time')).toBe('invalid time');
    expect(formatUtc('')).toBe('invalid time');
  });
});

describe('age helpers', () => {
  const now = new Date('2026-07-15T16:30:00Z');
  it('computes minutes and clamps future timestamps to 0', () => {
    expect(ageMinutes('2026-07-15T16:00:00Z', now)).toBe(30);
    expect(ageMinutes('2026-07-15T17:00:00Z', now)).toBe(0);
    expect(ageMinutes('garbage', now)).toBeNaN();
  });
  it('formats ages', () => {
    expect(formatAge(0.2)).toBe('<1 min');
    expect(formatAge(45)).toBe('45 min');
    expect(formatAge(480)).toBe('8.0 h');
    expect(formatAge(NaN)).toBe('unknown age');
  });
});
