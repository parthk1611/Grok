// Common API error body (RELAY's openapi.yaml ErrorResponse + Team Lead rulings of 2026-11-09):
//   {status, error, reason, last_cache_time, request_id, disclaimer, details?}
// The shape is CLOSED (Team Lead, 2026-11-09): any other field, e.g. `retryable`, is reported as unexpected.
// `reason` is a CLOSED enum (now including `confirmation_required`); `last_cache_time` is filled from
// the cache on every error and is null only when nothing was ever cached. There is no JSON Schema for
// this in /contracts yet (the openapi spec is still a draft), so this is a hand-written structural check.
// It never "fixes" a body: anything off-spec is reported as a contract problem next to the message.
import { DISCLAIMER } from './contract';
import type { ApiErrorInfo } from '../types';

export type ApiErrorBody = ApiErrorInfo;

export const ERROR_REASONS = [
  'invalid_request',
  'out_of_domain',
  'not_found',
  'route_not_found',
  'method_not_allowed',
  'confirmation_required',
  'no_cached_data',
  'provider_unavailable',
  'invalid_provider_payload',
  'spread_timeout',
  'internal_error',
] as const;
export type ErrorReason = (typeof ERROR_REASONS)[number];

/**
 * Reason for "nothing was ever cached" (Team Lead ruling 2026-11-09: also the answer to confirm_degraded on expired
 * weather with no cache). Kept in ONE place: if Team Lead renames it, change it here and in ERROR_REASONS.
 */
export const NO_CACHE_REASON = 'no_cached_data' satisfies ErrorReason;

export const ERROR_STATUSES = [400, 404, 405, 409, 500, 503, 504] as const;


/** Plain-language message per reason, shown above the server's own `error` text. */
export const REASON_MESSAGES: Record<ErrorReason, string> = {
  invalid_request: 'The request was rejected as invalid.',
  out_of_domain: 'That point is outside the Ontario coverage area.',
  not_found: 'No data exists for that request (nothing at or before the requested date).',
  route_not_found: 'The API route does not exist (the dashboard and the API may be on different versions).',
  method_not_allowed: 'The API does not allow that method on this route.',
  confirmation_required:
    'The live weather for this run is expired. Confirm a degraded run, or enter wind and moisture by hand, before the spread can run.',
  no_cached_data: 'No cached data exists yet: no successful ingestion has completed. Nothing is shown in its place.',
  provider_unavailable: 'The data provider failed. Nothing is shown in its place.',
  invalid_provider_payload: 'The server produced data that violated the contract, so it refused to serve it.',
  spread_timeout: 'The spread run did not finish within the server time limit. Nothing is shown.',
  internal_error: 'The server hit an internal error. Nothing is shown.',
};

export const isKnownReason = (r: string): r is ErrorReason => (ERROR_REASONS as readonly string[]).includes(r);

const ISO_Z = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;
const ALLOWED = new Set(['status', 'error', 'reason', 'last_cache_time', 'request_id', 'disclaimer', 'details']);

export interface ParsedApiError {
  /** The body, if it is at least an object with a string `reason`; otherwise null. */
  body: ApiErrorBody | null;
  /** Every way the body departs from the error model. Empty => fully conforming. */
  problems: string[];
}

/** Check an error body against the error model. `httpStatus` is the actual HTTP status of the response. */
export function parseApiError(raw: unknown, httpStatus: number): ParsedApiError {
  const problems: string[] = [];
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { body: null, problems: [`HTTP ${httpStatus}: error body is not a JSON object`] };
  }
  const b = raw as Record<string, unknown>;
  for (const k of Object.keys(b)) if (!ALLOWED.has(k)) problems.push(`unexpected field "${k}"`);
  for (const k of ['status', 'error', 'reason', 'last_cache_time', 'request_id', 'disclaimer']) {
    if (!(k in b)) problems.push(`missing required field "${k}"`);
  }
  if ('status' in b) {
    if (!Number.isInteger(b.status)) problems.push('status is not an integer');
    else {
      if (b.status !== httpStatus) problems.push(`status ${String(b.status)} does not match HTTP status ${httpStatus}`);
      if (!(ERROR_STATUSES as readonly number[]).includes(b.status as number)) problems.push(`status ${String(b.status)} is not in ${ERROR_STATUSES.join('/')}`);
    }
  }
  if ('error' in b && typeof b.error !== 'string') problems.push('error is not a string');
  if (typeof b.reason !== 'string') {
    if ('reason' in b) problems.push('reason is not a string');
  } else if (!isKnownReason(b.reason)) {
    problems.push(`reason "${b.reason}" is not in the closed enum`);
  }
  if ('last_cache_time' in b && b.last_cache_time !== null && !(typeof b.last_cache_time === 'string' && ISO_Z.test(b.last_cache_time))) {
    problems.push('last_cache_time is neither null nor an ISO 8601 UTC (Z) time');
  }
  if ('request_id' in b && typeof b.request_id !== 'string') problems.push('request_id is not a string');
  if ('disclaimer' in b && b.disclaimer !== DISCLAIMER) problems.push('disclaimer is not the exact contract constant');
  if ('details' in b && !Array.isArray(b.details)) problems.push('details is not an array');
  if (typeof b.reason !== 'string') return { body: null, problems };
  return {
    body: {
      status: typeof b.status === 'number' ? b.status : httpStatus,
      error: typeof b.error === 'string' ? b.error : '',
      reason: b.reason,
      last_cache_time: typeof b.last_cache_time === 'string' ? b.last_cache_time : null,
      request_id: typeof b.request_id === 'string' ? b.request_id : '',
      disclaimer: typeof b.disclaimer === 'string' ? b.disclaimer : '',
      ...(Array.isArray(b.details) ? { details: b.details } : {}),
    },
    problems,
  };
}

export function reasonMessage(reason: string): string {
  return isKnownReason(reason) ? REASON_MESSAGES[reason] : `Unrecognised error reason "${reason}" (not in the closed enum).`;
}
