// Team Lead ruling (2026-11-09): relaxing Ajv strictTypes/strictRequired is fine only if the app's
// validators still reject EVERY contracts/examples/bad_*.json. This test discovers the files on disk,
// so a new bad example added to /contracts is covered automatically (or fails loudly if unmapped).
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { AJV_OPTIONS, validateIgnitionArray, validateIgnitionRecord, validateSpreadOutput, validateWeatherRecord } from './contract';

const examplesDir = path.resolve(import.meta.dirname, '../../../contracts/examples');
const badFiles = readdirSync(examplesDir).filter((f) => /^bad_.*\.json$/.test(f)).sort();

const validatorFor = (file: string) =>
  file.startsWith('bad_ignition_') ? validateIgnitionRecord
  : file.startsWith('bad_spread_') ? validateSpreadOutput
  : file.startsWith('bad_weather_') ? validateWeatherRecord
  : null;

describe('every contracts/examples/bad_*.json is rejected by the app validators', () => {
  it('runs with strictTypes and strictRequired relaxed (and everything else strict)', () => {
    expect(AJV_OPTIONS.strictTypes).toBe(false);
    expect(AJV_OPTIONS.strictRequired).toBe(false);
    expect(AJV_OPTIONS.strict).toBe(true);
  });
  it('finds the bad examples on disk', () => {
    expect(badFiles.length).toBeGreaterThanOrEqual(5);
  });
  it.each(badFiles)('%s is rejected', (file) => {
    const validate = validatorFor(file);
    expect(validate, `no validator mapped for ${file}; add its prefix here`).not.toBeNull();
    const data: unknown = JSON.parse(readFileSync(path.join(examplesDir, file), 'utf8'));
    const r = validate!(data);
    expect(r.valid).toBe(false);
    if (!r.valid) expect(r.errors.length).toBeGreaterThan(0);
  });
  it.each(badFiles.filter((f) => f.startsWith('bad_ignition_')))('%s also poisons a whole ignition array', (file) => {
    const data: unknown = JSON.parse(readFileSync(path.join(examplesDir, file), 'utf8'));
    expect(validateIgnitionArray([data]).valid).toBe(false);
  });
});
