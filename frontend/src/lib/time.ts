// Time formatting. Payload timestamps are ISO 8601 UTC with a Z suffix
// (contracts/grid_spec.md); the display zone is America/Toronto (DST-aware via Intl).
import { TIME_ZONE } from '../config';

function parts(date: Date, timeZone: string) {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZoneName: 'short',
  });
  const out: Record<string, string> = {};
  for (const p of fmt.formatToParts(date)) out[p.type] = p.value;
  return out;
}

/** "2026-07-15 12:00 EDT" in America/Toronto. Returns "invalid time" for unparseable input. */
export function formatToronto(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'invalid time';
  const p = parts(d, TIME_ZONE);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} ${p.timeZoneName}`;
}

/** "2026-07-15 16:00 UTC" */
export function formatUtc(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'invalid time';
  const p = parts(d, 'UTC');
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} UTC`;
}

/** Local America/Toronto calendar date, YYYY-MM-DD (the contract's `date` convention). */
export function torontoDate(d: Date): string {
  const p = parts(d, TIME_ZONE);
  return `${p.year}-${p.month}-${p.day}`;
}

/** Whole minutes between an ISO timestamp and `now`; clamped at 0. NaN if unparseable. */
export function ageMinutes(iso: string, now: Date): number {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return NaN;
  return Math.max(0, (now.getTime() - t) / 60_000);
}

export function formatAge(minutes: number): string {
  if (!Number.isFinite(minutes)) return 'unknown age';
  if (minutes < 1) return '<1 min';
  if (minutes < 120) return `${Math.round(minutes)} min`;
  const h = minutes / 60;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} d`;
}
