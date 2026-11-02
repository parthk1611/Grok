// Staleness classification per contracts/cache_fallback.md (thresholds PROPOSED,
// kept in config.ts).
import { STALENESS_THRESHOLDS } from '../config';
import type { StaleLevel } from '../types';
import { ageMinutes } from './time';

const ORDER: Record<StaleLevel, number> = { fresh: 0, stale: 1, expired: 2 };

export const maxLevel = (...levels: StaleLevel[]): StaleLevel =>
  levels.reduce((a, b) => (ORDER[b] > ORDER[a] ? b : a), 'fresh' as StaleLevel);

/** fresh: age <= 30 min; stale: <= 6 h; expired: > 6 h. Unknown/NaN age is treated as expired (never shown as current). */
export function classifyAge(minutes: number, t = STALENESS_THRESHOLDS): StaleLevel {
  if (!Number.isFinite(minutes)) return 'expired';
  if (minutes <= t.freshMaxMinutes) return 'fresh';
  if (minutes <= t.staleMaxMinutes) return 'stale';
  return 'expired';
}

export interface StalenessAssessment {
  level: StaleLevel;
  /** Age in minutes used for the assessment (NaN when no usable timestamp). */
  ageMinutes: number;
  /** Which input the age was measured from, for display. */
  ageBasis: string;
  /** Human-readable reasons, in plain words. */
  reasons: string[];
  /** The payload's own boolean flag (if any). */
  stale_flag?: boolean;
  stale_reason?: string | null;
  /** Declared level, only for weather records (ignition/spread have none today -> "mode: unknown"). */
  declaredLevel?: StaleLevel;
  /** Weather only: declared level differs from the level recomputed from age_minutes. */
  levelMismatch?: boolean;
  mode: string;
}

interface AssessInput {
  stale_flag?: boolean;
  stale_reason?: string | null;
  timestamp: string;
  ageBasis: string;
  now: Date;
}

/**
 * Ignition / spread outputs. The contract has no stale_level or mode field, so
 * the level is derived: worst of (stale_flag => at least stale) and the level of
 * the age of the newest input timestamp. Mode is always "unknown" today.
 */
export function assessOutput(i: AssessInput): StalenessAssessment {
  const age = ageMinutes(i.timestamp, i.now);
  const ageLevel = classifyAge(age);
  const flagLevel: StaleLevel = i.stale_flag ? 'stale' : 'fresh';
  const reasons: string[] = [];
  if (i.stale_flag) reasons.push(`stale_flag is true${i.stale_reason ? `: ${i.stale_reason}` : ' (no stale_reason given)'}`);
  if (ageLevel !== 'fresh') reasons.push(`input age is ${ageLevel} by the proposed thresholds (measured from ${i.ageBasis})`);
  return {
    level: maxLevel(flagLevel, ageLevel),
    ageMinutes: age,
    ageBasis: i.ageBasis,
    reasons,
    stale_flag: i.stale_flag,
    stale_reason: i.stale_reason,
    mode: 'unknown',
  };
}

/** Weather record: honour the declared stale_level, cross-check against age_minutes. */
export function assessWeather(rec: { stale_level: StaleLevel; age_minutes: number; from_cache: boolean; kind: string }): StalenessAssessment {
  const computed = classifyAge(rec.age_minutes);
  const level = maxLevel(rec.stale_level, computed);
  const reasons: string[] = [];
  if (rec.stale_level !== 'fresh') reasons.push(`stale_level is "${rec.stale_level}"`);
  if (rec.from_cache) reasons.push('served from cache');
  const levelMismatch = computed !== rec.stale_level;
  if (levelMismatch) reasons.push(`declared "${rec.stale_level}" but age ${Math.round(rec.age_minutes)} min classifies as "${computed}" - showing the worse level`);
  return {
    level,
    ageMinutes: rec.age_minutes,
    ageBasis: 'age_minutes',
    reasons,
    declaredLevel: rec.stale_level,
    levelMismatch,
    mode: `${rec.kind}${rec.from_cache ? ', from cache' : ', live'}`,
  };
}
