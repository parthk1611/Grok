// Stale-day handling for GET /ignition?date=D (Team Lead ruling 2026-11-09):
// the server may answer with the latest available map at or before D; those records keep their
// own (earlier) `date` and carry stale_flag true. 404 only when nothing exists at or before D.
// `date=latest` asks for the newest map. The UI always shows the records' own date.
import type { IgnitionRecord } from '../types';

export type RequestedDate = string; // 'YYYY-MM-DD' (America/Toronto day) or 'latest'

export interface DateCheck {
  /** Distinct record dates, newest first. */
  shownDates: string[];
  /** Requested a specific day and got a different day (=> banner). */
  substituted: boolean;
  /** Banner text, e.g. "Showing 2026-11-08; requested 2026-11-09 unavailable", or null. */
  banner: string | null;
  /** Server-side contract problems (records after D, earlier records without stale_flag). */
  warnings: string[];
}

export function checkIgnitionDates(records: IgnitionRecord[], requested: RequestedDate, envelopeRequestedDate?: string | null): DateCheck {
  const shownDates = [...new Set(records.map((r) => r.date))].sort().reverse();
  const warnings: string[] = [];
  const isLatest = requested === 'latest';
  const substituted = !isLatest && shownDates.some((d) => d !== requested);
  if (!isLatest) {
    const after = shownDates.filter((d) => d > requested);
    if (after.length) warnings.push(`server returned records dated ${after.join(', ')}, after the requested ${requested} (should be at or before it)`);
    const unflagged = records.filter((r) => r.date !== requested && !r.stale_flag).length;
    if (unflagged) warnings.push(`${unflagged} record(s) dated before ${requested} have stale_flag false (should be true when the requested day is unavailable)`);
  }
  if (shownDates.length > 1) warnings.push(`payload mixes ${shownDates.length} dates (${shownDates.join(', ')}); one map per response expected`);
  if (envelopeRequestedDate != null && envelopeRequestedDate !== requested) {
    warnings.push(`envelope requested_date "${envelopeRequestedDate}" does not echo the requested "${requested}"`);
  }
  const banner = substituted ? `Showing ${shownDates.join(', ')}; requested ${requested} unavailable` : null;
  return { shownDates, substituted, banner, warnings };
}
