// Staleness handling per contracts/cache_fallback.md (v0.2.0). Thresholds are
// PROPOSED and live in config.ts.
//
// v0.2.0: ignition and spread outputs carry stale_level + age_minutes themselves
// (stale_level = worse of fetch health and data age, per-source data-age thresholds
// still TBD). The declared level is therefore the baseline. As a conservative
// cross-check we also classify age_minutes with the proposed generic thresholds
// and display the WORSE of the two, noting any disagreement - so the UI never
// shows a record as fresher than either measure says.
import { STALENESS_THRESHOLDS } from '../config';
import type { StaleLevel } from '../types';

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
  /** Level to display: worse of declared stale_level and the level recomputed from age_minutes. */
  level: StaleLevel;
  /** age_minutes from the payload. */
  ageMinutes: number;
  /** Where the age comes from, for display. */
  ageBasis: string;
  /** Human-readable reasons. */
  reasons: string[];
  stale_flag?: boolean;
  stale_reason?: string | null;
  declaredLevel: StaleLevel;
  /** Declared stale_level differs from the level recomputed from age_minutes with the proposed generic thresholds. */
  levelMismatch: boolean;
  /** Mode text shown in banners (payload `mode` for outputs; kind + cache state for weather). */
  mode: string;
}

interface Declared {
  stale_level: StaleLevel;
  age_minutes: number;
  stale_flag?: boolean;
  stale_reason?: string | null;
  mode: string;
  ageBasis?: string;
}

function assess(i: Declared): StalenessAssessment {
  const computed = classifyAge(i.age_minutes);
  const level = maxLevel(i.stale_level, computed);
  const reasons: string[] = [];
  if (i.stale_flag) reasons.push(`stale_flag is true${i.stale_reason ? `: ${i.stale_reason}` : ' (no stale_reason given)'}`);
  if (i.stale_level !== 'fresh') reasons.push(`stale_level is "${i.stale_level}"`);
  const levelMismatch = computed !== i.stale_level;
  if (levelMismatch) {
    reasons.push(
      `declared "${i.stale_level}" but age ${Math.round(i.age_minutes)} min classifies as "${computed}" under the proposed generic thresholds` +
        ` (per-source data-age thresholds are TBD) - showing the worse level`,
    );
  }
  return {
    level,
    ageMinutes: i.age_minutes,
    ageBasis: i.ageBasis ?? 'age_minutes',
    reasons,
    stale_flag: i.stale_flag,
    stale_reason: i.stale_reason,
    declaredLevel: i.stale_level,
    levelMismatch,
    mode: i.mode,
  };
}

/** Ignition output record (stale_flag / stale_reason / stale_level / age_minutes / mode at top level). */
export function assessIgnition(r: { stale_level: StaleLevel; age_minutes: number; stale_flag: boolean; stale_reason?: string | null; mode: string }): StalenessAssessment {
  return assess(r);
}

/** Spread output: stale_level / age_minutes / mode top level; stale_flag and stale_reason live under inputs. */
export function assessSpread(o: {
  stale_level: StaleLevel;
  age_minutes: number;
  mode: string;
  inputs: { stale_flag: boolean; stale_reason?: string | null };
}): StalenessAssessment {
  return assess({ ...o, stale_flag: o.inputs.stale_flag, stale_reason: o.inputs.stale_reason, ageBasis: 'age_minutes (oldest input)' });
}

/** Weather record: honour the declared stale_level, cross-check against age_minutes. */
export function assessWeather(rec: { stale_level: StaleLevel; age_minutes: number; from_cache: boolean; kind: string }): StalenessAssessment {
  const a = assess({ stale_level: rec.stale_level, age_minutes: rec.age_minutes, mode: `${rec.kind}${rec.from_cache ? ', from cache' : ', live'}` });
  if (rec.from_cache) a.reasons.unshift('served from cache');
  return a;
}
