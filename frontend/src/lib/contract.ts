// Contract schemas (read-only, imported by relative path from /contracts) and
// Ajv validators. Never modify the schemas from here.
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import type { ErrorObject } from 'ajv';
import ignitionSchema from '../../../contracts/ignition_output.schema.json';
import spreadSchema from '../../../contracts/spread_output.schema.json';
import weatherSchema from '../../../contracts/live_weather_record.schema.json';
import type { IgnitionRecord, SpreadOutput, WeatherRecord } from '../types';

export const CONTRACT_VERSION = '0.1.0';

/** Fallback disclaimer constant, read from the schema's `const` (not retyped here). */
export const DISCLAIMER: string = (
  ignitionSchema.properties.disclaimer as { const: string }
).const;

export const MOCK_MODEL_VERSION = 'example-not-a-real-model';

// strictTypes is off because contract v0.2.0's stale_flag if/then/else branches use `properties`
// without `type: object` (we must not edit the schemas). Unknown keywords/formats still throw.
const ajv = new Ajv2020({ allErrors: true, strict: true, strictTypes: false });
addFormats(ajv);

const validateIgnitionFn = ajv.compile<IgnitionRecord>(ignitionSchema);
const validateSpreadFn = ajv.compile<SpreadOutput>(spreadSchema);
const validateWeatherFn = ajv.compile<WeatherRecord>(weatherSchema);

export type ValidationResult<T> = { valid: true; data: T } | { valid: false; errors: string[] };

export function formatAjvErrors(errors: ErrorObject[] | null | undefined, max = 8): string[] {
  if (!errors || errors.length === 0) return ['unknown validation error'];
  const lines = errors.slice(0, max).map((e) => {
    const where = e.instancePath === '' ? '(root)' : e.instancePath;
    const extra =
      e.keyword === 'additionalProperties'
        ? ` "${(e.params as { additionalProperty: string }).additionalProperty}"`
        : e.keyword === 'const' || e.keyword === 'enum'
          ? ` (${JSON.stringify((e.params as Record<string, unknown>).allowedValue ?? (e.params as Record<string, unknown>).allowedValues)})`
          : '';
    return `${where}: ${e.message}${extra}`;
  });
  if (errors.length > max) lines.push(`... and ${errors.length - max} more`);
  return lines;
}

function run<T>(fn: { (d: unknown): boolean; errors?: ErrorObject[] | null }, data: unknown): ValidationResult<T> {
  if (fn(data)) return { valid: true, data: data as T };
  return { valid: false, errors: formatAjvErrors(fn.errors) };
}

export const validateIgnitionRecord = (d: unknown) => run<IgnitionRecord>(validateIgnitionFn, d);
export const validateSpreadOutput = (d: unknown) => run<SpreadOutput>(validateSpreadFn, d);
export const validateWeatherRecord = (d: unknown) => run<WeatherRecord>(validateWeatherFn, d);

/** Validate an array of ignition records (the API returns an array). Any bad record fails the whole payload. */
export function validateIgnitionArray(d: unknown): ValidationResult<IgnitionRecord[]> {
  if (!Array.isArray(d)) return { valid: false, errors: ['(root): expected an array of ignition records'] };
  if (d.length === 0) return { valid: false, errors: ['(root): ignition payload is empty'] };
  const errors: string[] = [];
  d.forEach((rec, i) => {
    const r = validateIgnitionRecord(rec);
    if (!r.valid) r.errors.forEach((e) => errors.push(`[${i}] ${e}`));
  });
  return errors.length ? { valid: false, errors: errors.slice(0, 12) } : { valid: true, data: d as IgnitionRecord[] };
}

export { ignitionSchema, spreadSchema, weatherSchema };
