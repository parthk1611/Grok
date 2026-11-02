// Staleness handling per contracts/cache_fallback.md (v0.2.1, two ages). Thresholds are
// PROPOSED and live in config.ts.
//
// Two ages:
//   * fetch health  = now - retrieved_at   (weather records: optional `fetch_age_minutes`)
//   * data age      = now - valid_time / source_run_time   (`age_minutes`)
// and the payload's `stale_level` is the worse of the two. Per-source DATA-age limits are still
// TBD in the contract, so we must not apply generic limits to the data age. We therefore:
//   * trust the declared stale_level as the baseline;
//   * cross-check fetch health (when `fetch_age_minutes` is present) against the proposed
//     30 min / 6 h limits, and display the worse level if that disagrees;
//   * add an informational note (no level change) when the data age exceeds the generic
//     proposed limits but the declared level is better (it may be legitimate for that source).
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
  /** Level to display: worse of declared stale_level and (if present) the fetch-health level. */
  level: StaleLevel;
  /** Data age in minutes (payload age_minutes). */
  ageMinutes: number;
  /** Where the data age comes from, for display. */
  ageBasis: string;
  /** Fetch-health age in minutes (weather records with fetch_age_minutes), else undefined. */
  fetchAgeMinutes?: number;
  /** Level of the fetch age under the proposed fetch-health limits. */
  fetchLevel?: StaleLevel;
  reasons: string[];
  stale_flag?: boolean;
  stale_reason?: string | null;
  declaredLevel: StaleLevel;
  /** Fetch-health level is worse than the declared level (so the displayed level was raised). */
  levelMismatch: boolean;
  /** Data age exceeds the generic proposed limits while the declared level is better (informational only). */
  dataAgeExceedsGenericLimits: boolean;
  /** Mode text shown in banners (payload `mode` for outputs; kind + cache state for weather). */
  mode: string;
}

interface Declared {
  stale_level: StaleLevel;
  age_minutes: number;
  fetch_age_minutes?: number;
  stale_flag?: boolean;
  stale_reason?: string | null;
  mode: string;
  ageBasis?: string;
}

function assess(i: Declared): StalenessAssessment {
  const fetchLevel = i.fetch_age_minutes === undefined ? undefined : classifyAge(i.fetch_age_minutes);
  const level = maxLevel(i.stale_level, fetchLevel ?? 'fresh');
  const levelMismatch = fetchLevel !== undefined && fetchLevel !== i.stale_level && level !== i.stale_level;
  const genericDataLevel = classifyAge(i.age_minutes);
  const dataAgeExceedsGenericLimits = maxLevel(i.stale_level, genericDataLevel) !== i.stale_level;
  const reasons: string[] = [];
  if (i.stale_flag) reasons.push(`stale_flag is true${i.stale_reason ? `: ${i.stale_reason}` : ' (no stale_reason given)'}`);
  if (i.stale_level !== 'fresh') reasons.push(`stale_level is "${i.stale_level}" (worse of fetch health and data age)`);
  if (levelMismatch) {
    reasons.push(`declared "${i.stale_level}" but fetch age ${Math.round(i.fetch_age_minutes!)} min classifies as "${fetchLevel}" - showing the worse level`);
  }
  if (dataAgeExceedsGenericLimits) {
    reasons.push(
      `data age ${Math.round(i.age_minutes)} min exceeds the generic proposed limits, but per-source data-age limits are TBD so the declared level is kept`,
    );
  }
  return {
    level,
    ageMinutes: i.age_minutes,
    ageBasis: i.ageBasis ?? 'age_minutes',
    fetchAgeMinutes: i.fetch_age_minutes,
    fetchLevel,
    reasons,
    stale_flag: i.stale_flag,
    stale_reason: i.stale_reason,
    declaredLevel: i.stale_level,
    levelMismatch,
    dataAgeExceedsGenericLimits,
    mode: i.mode,
  };
}

/** Ignition output record (stale_flag / stale_reason / stale_level / age_minutes / mode at top level). */
export function assessIgnition(r: { stale_level: StaleLevel; age_minutes: number; stale_flag: boolean; stale_reason?: string | null; mode: string }): StalenessAssessment {
  return assess({ ...r, ageBasis: 'age_minutes (oldest input)' });
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

/** Weather record: age_minutes is DATA age; optional fetch_age_minutes is fetch health. */
export function assessWeather(rec: {
  stale_level: StaleLevel;
  age_minutes: number;
  fetch_age_minutes?: number;
  from_cache: boolean;
  kind: string;
}): StalenessAssessment {
  const a = assess({
    stale_level: rec.stale_level,
    age_minutes: rec.age_minutes,
    fetch_age_minutes: rec.fetch_age_minutes,
    mode: `${rec.kind}${rec.from_cache ? ', from cache' : ', live'}`,
    ageBasis: rec.kind === 'forecast' ? 'age_minutes (now - source_run_time)' : 'age_minutes (now - valid_time)',
  });
  if (rec.from_cache) a.reasons.unshift('served from cache');
  return a;
}
