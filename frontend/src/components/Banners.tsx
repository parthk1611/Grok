import { STALENESS_THRESHOLDS } from '../config';
import { formatAge, formatToronto, formatUtc } from '../lib/time';
import { isKnownReason, reasonMessage } from '../lib/apiError';
import { MANUAL_STALE_REASON } from '../lib/manual';
import type { ApiErrorInfo } from '../types';
import type { StalenessAssessment } from '../lib/staleness';

/** Persistent page-level banner: all data in this build is MOCK. */
export function MockBanner() {
  return (
    <div className="banner banner-mock" role="alert" data-testid="mock-banner">
      <strong>MOCK DATA.</strong> Every value on this page is a placeholder from <code>contracts/examples</code> or a synthetic fixture
      (<code>model_version: example-not-a-real-model</code>). Nothing shown is a model result, a real weather observation, or a fire forecast.
    </div>
  );
}

export function MockBadge({ title = 'This panel shows MOCK data' }: { title?: string }) {
  return (
    <span className="badge badge-mock" title={title} data-testid="mock-badge">
      MOCK
    </span>
  );
}

export function UnvalidatedBadge({ label = 'unvalidated' }: { label?: string }) {
  return (
    <span className="badge badge-unvalidated" title="Not validated against observed fire growth" data-testid="unvalidated-badge">
      {label}
    </span>
  );
}

/**
 * Staleness banner. Fresh: no banner (a small chip is rendered by the panel instead).
 * Stale: amber. Expired: distinct red banner, never presented as current.
 */
export function StaleBanner({ assessment, subject }: { assessment: StalenessAssessment; subject: string }) {
  if (assessment.level === 'fresh') return null;
  const expired = assessment.level === 'expired';
  return (
    <div
      className={`banner ${expired ? 'banner-expired' : 'banner-stale'}`}
      role="alert"
      data-testid={expired ? 'expired-banner' : 'stale-banner'}
    >
      <strong>{expired ? 'EXPIRED DATA' : 'STALE DATA'}</strong> - {subject}{' '}
      {expired ? 'is expired and must not be treated as current.' : 'is older than expected; cached or late inputs were used.'}
      <div className="banner-detail">
        Data age: {formatAge(assessment.ageMinutes)} (from {assessment.ageBasis}) · fetch age:{' '}
        {assessment.fetchAgeMinutes === undefined ? 'not provided' : formatAge(assessment.fetchAgeMinutes)} · mode: {assessment.mode}
        {assessment.reasons.length > 0 && (
          <ul>
            {assessment.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        <span className="muted">
          Fetch-health thresholds ({STALENESS_THRESHOLDS.status}, cache_fallback.md): fresh ≤ {STALENESS_THRESHOLDS.freshMaxMinutes} min, stale ≤{' '}
          {STALENESS_THRESHOLDS.staleMaxMinutes / 60} h, expired beyond. Per-source data-age limits: TBD.
        </span>
      </div>
    </div>
  );
}

export function LevelChip({ assessment }: { assessment: StalenessAssessment }) {
  return (
    <span className={`chip chip-${assessment.level}`} data-testid="level-chip">
      {assessment.level}
    </span>
  );
}

export interface ErrorStateProps {
  title: string;
  errors: string[];
  kind?: 'invalid' | 'unavailable' | 'api_error';
  httpStatus?: number;
  apiError?: ApiErrorInfo | null;
}

export function ErrorState({ title, errors, kind, httpStatus, apiError }: ErrorStateProps) {
  return (
    <div className="banner banner-error" role="alert" data-testid="error-state" data-reason={apiError?.reason ?? ''}>
      <strong>{title}</strong>
      <div className="banner-detail">
        {kind === 'invalid'
          ? 'The payload does not match the contract, so it is not displayed.'
          : kind === 'api_error'
            ? 'The API answered with an error. Nothing is shown in place of the data.'
            : 'No data could be loaded. Nothing is shown in its place.'}
        {kind === 'api_error' && <ApiErrorDetails httpStatus={httpStatus} apiError={apiError ?? null} />}
        {errors.length > 0 && (
          <>
            {kind === 'api_error' && <div className="small">The error body does not match the error model:</div>}
            <ul>
              {errors.map((e, i) => (
                <li key={i}>
                  <code>{e}</code>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

/** Per-reason detail block: status, reason, the server's message, last_cache_time and request_id. */
export function ApiErrorDetails({ httpStatus, apiError }: { httpStatus?: number; apiError: ApiErrorInfo | null }) {
  if (!apiError) return <div data-testid="api-error-details">HTTP {httpStatus ?? '?'}; the error body has no usable reason.</div>;
  return (
    <dl className="api-error" data-testid="api-error-details">
      <dt>HTTP / reason</dt>
      <dd>
        {httpStatus ?? apiError.status} · <code data-testid="api-error-reason">{apiError.reason}</code>
        {!isKnownReason(apiError.reason) && <em> (not in the closed enum)</em>}
      </dd>
      <dt>What it means</dt>
      <dd>{reasonMessage(apiError.reason)}</dd>
      {apiError.error && (
        <>
          <dt>Server message</dt>
          <dd>{apiError.error}</dd>
        </>
      )}
      <dt>Last cached data</dt>
      <dd data-testid="api-error-last-cache">
        {apiError.last_cache_time === null ? (
          'never cached (last_cache_time is null)'
        ) : (
          <>
            {formatUtc(apiError.last_cache_time)} <span className="muted">({formatToronto(apiError.last_cache_time)} Toronto)</span>
          </>
        )}
      </dd>
      <dt>Request ID</dt>
      <dd>
        <code data-testid="api-error-request-id">{apiError.request_id || 'not provided'}</code>
      </dd>
    </dl>
  );
}

/** Things the server did that deviate from the contract or the Team Lead rulings. Shown, never patched. */
export function ContractWarnings({ warnings }: { warnings?: string[] }) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <div className="banner banner-warn" role="status" data-testid="contract-warnings">
      <strong>Server response deviates from the contract/rulings.</strong>
      <ul>
        {warnings.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
    </div>
  );
}

/** "Manual inputs" badge: weather was typed in by the user, not live data. Rendered above the stale banner. */
export function ManualInputsBadge() {
  return (
    <div className="banner banner-manual" data-testid="manual-inputs-badge">
      <span className="badge badge-manual">Manual inputs</span> Wind and moisture were entered by hand ({MANUAL_STALE_REASON}). This run is never shown as current.
    </div>
  );
}
