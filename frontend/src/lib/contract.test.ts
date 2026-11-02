import { describe, expect, it } from 'vitest';
import {
  DISCLAIMER,
  MOCK_MODEL_VERSION,
  validateIgnitionArray,
  validateIgnitionRecord,
  validateSpreadOutput,
  validateWeatherRecord,
} from './contract';
import ignitionExample from '../../../contracts/examples/ignition_output.example.json';
import spreadExample from '../../../contracts/examples/spread_output.example.json';
import weatherExample from '../../../contracts/examples/live_weather_record.example.json';
import badIgnitionProbability from '../../../contracts/examples/bad_ignition_probability.json';
import badIgnitionNoDisclaimer from '../../../contracts/examples/bad_ignition_no_disclaimer.json';
import badSpreadHorizon from '../../../contracts/examples/bad_spread_horizon.json';
import badWeatherLevel from '../../../contracts/examples/bad_weather_stale_level.json';

describe('contract validation with Ajv (draft 2020-12)', () => {
  it('good contracts/examples pass', () => {
    expect(validateIgnitionRecord(ignitionExample).valid).toBe(true);
    expect(validateSpreadOutput(spreadExample).valid).toBe(true);
    expect(validateWeatherRecord(weatherExample).valid).toBe(true);
  });

  it('contracts/examples/bad_* fail, with readable errors', () => {
    const cases: Array<[string, ReturnType<typeof validateIgnitionRecord>, RegExp]> = [
      ['bad_ignition_probability', validateIgnitionRecord(badIgnitionProbability), /probability/],
      ['bad_ignition_no_disclaimer', validateIgnitionRecord(badIgnitionNoDisclaimer), /disclaimer/],
      ['bad_spread_horizon', validateSpreadOutput(badSpreadHorizon) as never, /horizon_h/],
      ['bad_weather_stale_level', validateWeatherRecord(badWeatherLevel) as never, /stale_level/],
    ];
    for (const [name, res, re] of cases) {
      expect(res.valid, name).toBe(false);
      if (!res.valid) expect(res.errors.join('\n'), name).toMatch(re);
    }
  });

  it('example payloads carry the mock model version and the contract disclaimer', () => {
    expect(ignitionExample.model_version).toBe(MOCK_MODEL_VERSION);
    expect(spreadExample.model_version).toBe(MOCK_MODEL_VERSION);
    expect(ignitionExample.disclaimer).toBe(DISCLAIMER);
    expect(spreadExample.disclaimer).toBe(DISCLAIMER);
    expect(DISCLAIMER).toMatch(/does not replace official fire alerts/);
  });

  it('a wrong disclaimer string is rejected (exact const)', () => {
    expect(validateIgnitionRecord({ ...ignitionExample, disclaimer: 'Research estimate.' }).valid).toBe(false);
  });

  it('array validator rejects non-arrays, empty arrays, and any bad element', () => {
    expect(validateIgnitionArray({}).valid).toBe(false);
    expect(validateIgnitionArray([]).valid).toBe(false);
    const mixed = validateIgnitionArray([ignitionExample, badIgnitionProbability]);
    expect(mixed.valid).toBe(false);
    if (!mixed.valid) expect(mixed.errors[0]).toMatch(/^\[1\]/);
    expect(validateIgnitionArray([ignitionExample]).valid).toBe(true);
  });
});
