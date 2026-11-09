// Expired-weather flow for a spread run (Team Lead ruling 2026-11-09). Opened either by the client-side
// pre-check (weather record expired / unusable) or by a 409 `confirmation_required` from the API.
// Two ways forward: "Run anyway (degraded)" (sends confirm_degraded: true) or
// "Enter wind/moisture by hand" (validated form, sends weather_override). Cancel sends nothing.
import { useState, type FormEvent } from 'react';
import { OVERRIDE_FIELDS, parseOverride, type OverrideDraft } from '../lib/manual';
import { formatToronto, formatUtc } from '../lib/time';
import type { ApiErrorInfo, WeatherOverride } from '../types';

export interface DegradedRunDialogProps {
  reasons: string[];
  /** The 409 body when the server asked for confirmation. */
  serverError?: ApiErrorInfo | null;
  onCancel: () => void;
  onRunDegraded: () => void;
  onManual: (o: WeatherOverride) => void;
  /** Open straight on the manual form. */
  initialStep?: 'choose' | 'manual';
}

export function DegradedRunDialog({ reasons, serverError, onCancel, onRunDegraded, onManual, initialStep = 'choose' }: DegradedRunDialogProps) {
  const [step, setStep] = useState<'choose' | 'manual'>(initialStep);
  const [draft, setDraft] = useState<OverrideDraft>({});
  const [errors, setErrors] = useState<Partial<Record<keyof WeatherOverride, string>>>({});

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const r = parseOverride(draft);
    if (!r.ok) {
      setErrors(r.errors);
      return;
    }
    onManual(r.value);
  };

  return (
    <div className="modal-backdrop" data-testid="degraded-dialog">
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="degraded-title">
        <h3 id="degraded-title">{step === 'choose' ? 'Weather for this spread run is expired or unavailable' : 'Enter wind and moisture by hand'}</h3>
        {reasons.map((r, i) => (
          <p key={i}>{r}</p>
        ))}
        {serverError && (
          <p className="small" data-testid="degraded-server-409">
            The API answered {serverError.status} <code>{serverError.reason}</code>
            {serverError.last_cache_time ? (
              <>
                ; newest cached weather {formatUtc(serverError.last_cache_time)} ({formatToronto(serverError.last_cache_time)} Toronto)
              </>
            ) : (
              '; no weather was ever cached'
            )}
            ; request <code>{serverError.request_id || 'not provided'}</code>.
          </p>
        )}
        {step === 'choose' ? (
          <>
            <p>A degraded run must not be treated as current. Manual inputs are labelled &ldquo;Manual inputs&rdquo; and are never shown as fresh.</p>
            <div className="modal-actions">
              <button type="button" onClick={onCancel} data-testid="degraded-cancel">
                Cancel
              </button>
              <button type="button" onClick={() => setStep('manual')} data-testid="degraded-manual">
                Enter wind/moisture by hand
              </button>
              <button type="button" className="danger" onClick={onRunDegraded} data-testid="degraded-run">
                Run anyway (degraded)
              </button>
            </div>
          </>
        ) : (
          <form onSubmit={submit} noValidate data-testid="manual-form">
            <div className="manual-form">
              {OVERRIDE_FIELDS.map((f) => (
                <FieldRow
                  key={f.key}
                  id={`ov-${f.key}`}
                  label={`${f.label} (${f.unit})${f.required ? ' *' : ''}`}
                  help={f.help ? `${f.help}; ${f.min} to ${f.max}` : `${f.min} to ${f.max}`}
                  value={draft[f.key] ?? ''}
                  error={errors[f.key]}
                  onChange={(v) => setDraft((d) => ({ ...d, [f.key]: v }))}
                />
              ))}
            </div>
            <p className="small muted">* required. Ranges are UI sanity limits, not contract limits. Empty optional fields are not sent.</p>
            <div className="modal-actions">
              <button type="button" onClick={onCancel} data-testid="manual-cancel">
                Cancel
              </button>
              {initialStep === 'choose' && (
                <button type="button" onClick={() => setStep('choose')} data-testid="manual-back">
                  Back
                </button>
              )}
              <button type="submit" className="danger" data-testid="manual-submit">
                Run with manual inputs
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function FieldRow({ id, label, help, value, error, onChange }: { id: string; label: string; help: string; value: string; error?: string; onChange: (v: string) => void }) {
  return (
    <>
      <label htmlFor={id}>{label}</label>
      <span>
        <input id={id} inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={!!error} data-testid={id} />{' '}
        <span className="help">{help}</span>
      </span>
      {error && (
        <span className="field-error" role="alert" data-testid={`${id}-error`}>
          {error}
        </span>
      )}
    </>
  );
}
