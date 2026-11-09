// Ignition list responses: either the plain contract array, or the opt-in envelope
// (`?envelope=true`, Team Lead ruling 2026-11-09):
//   {mode, stale_level, age_minutes, data_timestamp, requested_date, mock?, records}
// There is no schema for the envelope in /contracts yet, so it is checked structurally here.
// PROVISIONAL (until Team Lead approves contract v0.3.0): optional `mock` (CR-4, boolean) and `stale_sources`
// (CR-6, array of {source, stale_level, age_minutes, fetch_age_minutes?}) follow RELAY's proposal
// contracts/proposals/v0.3.0-cr4-cr6.patch (RELAY commit 71583ce). Revisit when v0.3.0 lands.
// Every record is validated on its own against ignition_output.schema.json.
import type { IgnitionRecord, OutputMode, StaleLevel } from '../types';
import { validateIgnitionArray, type ValidationResult } from './contract';

export interface IgnitionEnvelopeMeta {
  mode: OutputMode;
  stale_level: StaleLevel;
  age_minutes: number;
  data_timestamp: string;
  requested_date: string;
  mock?: boolean;
  /** PROVISIONAL v0.3.0 (CR-6). */
  stale_sources?: StaleSource[];
}

/** PROVISIONAL v0.3.0 (CR-6) per-feed staleness entry, item shape from RELAY's proposal patch. */
export interface StaleSource {
  source: StaleSourceName;
  stale_level: StaleLevel;
  age_minutes: number;
  fetch_age_minutes?: number;
}
export const STALE_SOURCE_NAMES = ['hrdps', 'cwfis_firewx_stns', 'swob_realtime'] as const;
export type StaleSourceName = (typeof STALE_SOURCE_NAMES)[number];

const SOURCE_KEYS = new Set(['source', 'stale_level', 'age_minutes', 'fetch_age_minutes']);

function checkStaleSources(v: unknown, errors: string[]): void {
  if (!Array.isArray(v)) {
    errors.push('(envelope)/stale_sources: must be an array');
    return;
  }
  v.forEach((s, i) => {
    const at = `(envelope)/stale_sources[${i}]`;
    if (typeof s !== 'object' || s === null || Array.isArray(s)) {
      errors.push(`${at}: must be an object`);
      return;
    }
    const o = s as Record<string, unknown>;
    for (const k of Object.keys(o)) if (!SOURCE_KEYS.has(k)) errors.push(`${at}: unexpected field "${k}"`);
    if (!(STALE_SOURCE_NAMES as readonly string[]).includes(o.source as string)) errors.push(`${at}/source: must be one of ${STALE_SOURCE_NAMES.join(', ')}`);
    if (!LEVELS.includes(o.stale_level as string)) errors.push(`${at}/stale_level: must be one of ${LEVELS.join(', ')}`);
    if (typeof o.age_minutes !== 'number' || !(o.age_minutes >= 0)) errors.push(`${at}/age_minutes: must be a number >= 0`);
    if ('fetch_age_minutes' in o && (typeof o.fetch_age_minutes !== 'number' || !(o.fetch_age_minutes >= 0))) errors.push(`${at}/fetch_age_minutes: must be a number >= 0`);
  });
}

export interface IgnitionPayload {
  records: IgnitionRecord[];
  /** null when the server sent the plain array. */
  envelope: IgnitionEnvelopeMeta | null;
}

const MODES = ['retrospective', 'hindcast', 'forecast_like', 'forecast'];
const LEVELS = ['fresh', 'stale', 'expired'];
const ISO_Z = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;
const DATE_OR_LATEST = /^(\d{4}-\d{2}-\d{2}|latest)$/;
// Every other key is still rejected. `mock` and `stale_sources` are optional (stale_sources provisional, v0.3.0).
const ENVELOPE_KEYS = new Set(['mode', 'stale_level', 'age_minutes', 'data_timestamp', 'requested_date', 'mock', 'stale_sources', 'records']);

export function parseIgnitionPayload(raw: unknown): ValidationResult<IgnitionPayload> {
  if (Array.isArray(raw)) {
    const v = validateIgnitionArray(raw);
    return v.valid ? { valid: true, data: { records: v.data, envelope: null } } : v;
  }
  if (typeof raw !== 'object' || raw === null) {
    return { valid: false, errors: ['(root): expected an array of ignition records or an envelope object'] };
  }
  const e = raw as Record<string, unknown>;
  const errors: string[] = [];
  for (const k of Object.keys(e)) if (!ENVELOPE_KEYS.has(k)) errors.push(`(envelope): unexpected field "${k}"`);
  if (!MODES.includes(e.mode as string)) errors.push(`(envelope)/mode: must be one of ${MODES.join(', ')}`);
  if (!LEVELS.includes(e.stale_level as string)) errors.push(`(envelope)/stale_level: must be one of ${LEVELS.join(', ')}`);
  if (typeof e.age_minutes !== 'number' || !(e.age_minutes >= 0)) errors.push('(envelope)/age_minutes: must be a number >= 0');
  if (typeof e.data_timestamp !== 'string' || !ISO_Z.test(e.data_timestamp)) errors.push('(envelope)/data_timestamp: must be ISO 8601 UTC with Z');
  if (typeof e.requested_date !== 'string' || !DATE_OR_LATEST.test(e.requested_date)) errors.push('(envelope)/requested_date: must be YYYY-MM-DD or "latest"');
  if ('mock' in e && typeof e.mock !== 'boolean') errors.push('(envelope)/mock: must be a boolean');
  if ('stale_sources' in e) checkStaleSources(e.stale_sources, errors);
  if (!('records' in e)) errors.push('(envelope): missing "records"');
  const v = 'records' in e ? validateIgnitionArray(e.records) : null;
  if (v && !v.valid) v.errors.forEach((x) => errors.push(`records${x.startsWith('[') ? '' : ' '}${x}`));
  if (errors.length) return { valid: false, errors: errors.slice(0, 14) };
  return {
    valid: true,
    data: {
      records: (v as { data: IgnitionRecord[] }).data,
      envelope: {
        mode: e.mode as OutputMode,
        stale_level: e.stale_level as StaleLevel,
        age_minutes: e.age_minutes as number,
        data_timestamp: e.data_timestamp as string,
        requested_date: e.requested_date as string,
        ...(typeof e.mock === 'boolean' ? { mock: e.mock } : {}),
        ...(Array.isArray(e.stale_sources) ? { stale_sources: e.stale_sources as StaleSource[] } : {}),
      },
    },
  };
}
