import { STALENESS_THRESHOLDS } from '../config';
import { formatAge } from '../lib/time';
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

export function ErrorState({ title, errors, kind }: { title: string; errors: string[]; kind?: 'invalid' | 'unavailable' }) {
  return (
    <div className="banner banner-error" role="alert" data-testid="error-state">
      <strong>{title}</strong>
      <div className="banner-detail">
        {kind === 'invalid'
          ? 'The payload does not match the contract, so it is not displayed.'
          : 'No data could be loaded. Nothing is shown in its place.'}
        <ul>
          {errors.map((e, i) => (
            <li key={i}>
              <code>{e}</code>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
