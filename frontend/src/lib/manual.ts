// Manual weather inputs on a spread run (Team Lead ruling 2026-11-09): a response to a request with
// `weather_override` carries inputs.weather_source "manual", stale_level "stale", inputs.stale_flag true and
// inputs.stale_reason exactly MANUAL_STALE_REASON. Manual inputs are never presented as fresh.
import type { SpreadOutput, WeatherOverride } from '../types';

export const MANUAL_WEATHER_SOURCE = 'manual';
export const MANUAL_STALE_REASON = 'manual weather override; not live data';

export const isManualRun = (out: SpreadOutput) => out.inputs.weather_source === MANUAL_WEATHER_SOURCE;

/** Deviations from the manual-input ruling. Shown as warnings; the payload is never altered. */
export function manualRunWarnings(out: SpreadOutput, sent: WeatherOverride | undefined): string[] {
  const w: string[] = [];
  const manual = isManualRun(out);
  if (sent && !manual) w.push(`weather_override was sent but inputs.weather_source is "${out.inputs.weather_source}", not "manual"`);
  if (!sent && manual) w.push('inputs.weather_source is "manual" although no weather_override was sent');
  if (!manual) return w;
  if (out.stale_level !== 'stale') w.push(`manual run has stale_level "${out.stale_level}" (ruling: "stale")`);
  if (out.inputs.stale_flag !== true) w.push('manual run has inputs.stale_flag false (ruling: true)');
  if (out.inputs.stale_reason !== MANUAL_STALE_REASON) {
    w.push(`manual run stale_reason is ${JSON.stringify(out.inputs.stale_reason ?? null)} (ruling: "${MANUAL_STALE_REASON}")`);
  }
  if (sent) {
    if (out.inputs.wind_speed_kmh !== sent.wind_speed_kmh) w.push(`inputs.wind_speed_kmh ${out.inputs.wind_speed_kmh} does not echo the entered ${sent.wind_speed_kmh}`);
    if (out.inputs.wind_direction_deg !== sent.wind_direction_deg) w.push(`inputs.wind_direction_deg ${out.inputs.wind_direction_deg} does not echo the entered ${sent.wind_direction_deg}`);
  }
  return w;
}

/** UI-side bounds for the manual form, approved by Team Lead (2026-11-09) as sanity limits, NOT contract limits (the contract only bounds direction 0-360, RH 0-100, speed/precip >= 0). */
export const OVERRIDE_FIELDS = [
  { key: 'wind_speed_kmh', label: 'Wind speed', unit: 'km/h', required: true, min: 0, max: 200, help: '10 m wind speed at start_time' },
  { key: 'wind_direction_deg', label: 'Wind direction', unit: '° (from)', required: true, min: 0, max: 360, help: 'degrees the wind blows FROM (0/360 = north)' },
  { key: 'ffmc', label: 'FFMC', unit: 'index', required: false, min: 0, max: 101, help: 'Fine Fuel Moisture Code; 0-101 is the real FWI scale' },
  { key: 'bui', label: 'BUI', unit: 'index', required: false, min: 0, max: 500, help: 'Buildup Index (>= 0)' },
  { key: 'temp_c', label: 'Temperature', unit: '°C', required: false, min: -50, max: 50, help: '' },
  { key: 'rh_pct', label: 'Relative humidity', unit: '%', required: false, min: 0, max: 100, help: '' },
  { key: 'precip_mm', label: 'Precipitation', unit: 'mm', required: false, min: 0, max: 500, help: 'precip over the last interval' },
] as const satisfies ReadonlyArray<{ key: keyof WeatherOverride; label: string; unit: string; required: boolean; min: number; max: number; help: string }>;

export type OverrideDraft = Partial<Record<keyof WeatherOverride, string>>;

/** Parse the form strings. Returns the override or per-field error messages. Empty optional fields are omitted. */
export function parseOverride(draft: OverrideDraft): { ok: true; value: WeatherOverride } | { ok: false; errors: Partial<Record<keyof WeatherOverride, string>> } {
  const errors: Partial<Record<keyof WeatherOverride, string>> = {};
  const value: Partial<WeatherOverride> = {};
  for (const f of OVERRIDE_FIELDS) {
    const raw = (draft[f.key] ?? '').trim();
    if (raw === '') {
      if (f.required) errors[f.key] = `${f.label} is required`;
      continue;
    }
    const n = Number(raw);
    if (!/^-?\d+(\.\d+)?$/.test(raw) || !Number.isFinite(n)) {
      errors[f.key] = `${f.label} must be a number (${f.unit})`;
      continue;
    }
    if (n < f.min || n > f.max) {
      errors[f.key] = `${f.label} must be between ${f.min} and ${f.max} ${f.unit}`;
      continue;
    }
    value[f.key] = n;
  }
  return Object.keys(errors).length ? { ok: false, errors } : { ok: true, value: value as WeatherOverride };
}
