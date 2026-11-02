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

  describe('v0.2.0 rules', () => {
    const wrongFlag = { ...ignitionExample, stale_level: 'stale' as const, stale_flag: false };
    it('requires mode, stale_level and age_minutes on ignition', () => {
      for (const k of ['mode', 'stale_level', 'age_minutes'] as const) {
        const r: Record<string, unknown> = { ...ignitionExample };
        delete r[k];
        expect(validateIgnitionRecord(r).valid, k).toBe(false);
      }
    });
    it('rejects an unknown mode', () => {
      expect(validateIgnitionRecord({ ...ignitionExample, mode: 'live' }).valid).toBe(false);
    });
    it('stale_flag must agree with stale_level (ignition and spread)', () => {
      expect(validateIgnitionRecord(wrongFlag).valid).toBe(false);
      expect(validateIgnitionRecord({ ...ignitionExample, stale_level: 'expired', stale_flag: true }).valid).toBe(true);
      const sp = JSON.parse(JSON.stringify(spreadExample));
      sp.stale_level = 'stale';
      expect(validateSpreadOutput(sp).valid).toBe(false);
      sp.inputs.stale_flag = true;
      expect(validateSpreadOutput(sp).valid).toBe(true);
    });
    it('optional calibration / feature_set_version are accepted', () => {
      expect(validateIgnitionRecord({ ...ignitionExample, calibration: null, feature_set_version: 'x' }).valid).toBe(true);
    });
    it('spread requires validated_horizons_h, uncertainty_status, perturbation, fuel_source, grid_resolution_m', () => {
      for (const k of ['validated_horizons_h', 'uncertainty_status', 'perturbation', 'grid_resolution_m', 'mode']) {
        const sp: Record<string, unknown> = JSON.parse(JSON.stringify(spreadExample));
        delete sp[k];
        expect(validateSpreadOutput(sp).valid, k).toBe(false);
      }
      const sp = JSON.parse(JSON.stringify(spreadExample));
      delete sp.inputs.fuel_source;
      expect(validateSpreadOutput(sp).valid).toBe(false);
    });
    it('an empty band (MultiPolygon [], area 0) is valid', () => {
      const sp = JSON.parse(JSON.stringify(spreadExample));
      sp.features[0].geometry = { type: 'MultiPolygon', coordinates: [] };
      sp.features[0].properties.area_ha = 0;
      expect(validateSpreadOutput(sp).valid).toBe(true);
    });
  });
});
