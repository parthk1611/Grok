import { describe, expect, it } from 'vitest';
import { SCENARIOS, mockIgnition, mockSpread, mockWeather } from './fixtures';
import type { IgnitionRecord, SpreadOutput } from '../types';
import { MOCK_MODEL_VERSION, validateIgnitionArray, validateSpreadOutput, validateWeatherRecord } from '../lib/contract';
import { assessIgnition, assessSpread, assessWeather } from '../lib/staleness';
import ignitionExtra from './ignition_extra_cells.synthetic.json';
import ignitionSparse from './ignition_sparse.synthetic.json';
import weatherStale from './weather_stale.synthetic.json';
import weatherExpired from './weather_expired.synthetic.json';
import weatherSparse from './weather_sparse.synthetic.json';
import spreadSparse from './spread_sparse.synthetic.json';

const NOW = new Date('2026-11-02T20:00:00Z');
const POINT: [number, number] = [-85.5, 49.2];

describe('synthetic fixtures validate against the contract schemas', () => {
  it('static synthetic files', () => {
    expect(validateIgnitionArray(ignitionExtra).valid).toBe(true);
    expect(validateIgnitionArray(ignitionSparse).valid).toBe(true);
    for (const w of [weatherStale, weatherExpired, weatherSparse]) expect(validateWeatherRecord(w).valid).toBe(true);
    expect(validateSpreadOutput(spreadSparse).valid).toBe(true);
  });
  it('all synthetic outputs use model_version example-not-a-real-model', () => {
    for (const r of [...ignitionExtra, ...ignitionSparse]) expect(r.model_version).toBe(MOCK_MODEL_VERSION);
    expect(spreadSparse.model_version).toBe(MOCK_MODEL_VERSION);
  });
  it('every non-invalid scenario produces valid payloads; invalid produces invalid ones', () => {
    for (const s of SCENARIOS) {
      const ign = validateIgnitionArray(mockIgnition(s, NOW));
      const wx = validateWeatherRecord(mockWeather(s, NOW, POINT));
      const sp = validateSpreadOutput(mockSpread(s, NOW, POINT));
      const expected = s !== 'invalid';
      expect(ign.valid, `ignition ${s}`).toBe(expected);
      expect(wx.valid, `weather ${s}`).toBe(expected);
      expect(sp.valid, `spread ${s}`).toBe(expected);
    }
  });
});

describe('scenario levels are consistent with their label', () => {
  it('fresh/stale/expired produce that level for ignition, weather and spread, with no mismatch note', () => {
    for (const s of ['fresh', 'stale', 'expired'] as const) {
      const ign = mockIgnition(s, NOW) as IgnitionRecord[];
      const a = assessIgnition(ign[0]);
      expect(a.level, `ignition ${s}`).toBe(s);
      expect(a.levelMismatch).toBe(false);
      const wx = mockWeather(s, NOW, null) as Parameters<typeof assessWeather>[0];
      expect(assessWeather(wx).level).toBe(s);
      expect(assessWeather(wx).levelMismatch).toBe(false);
      const sp = mockSpread(s, NOW, POINT) as SpreadOutput;
      expect(assessSpread(sp).level, `spread ${s}`).toBe(s);
      expect(assessSpread(sp).levelMismatch).toBe(false);
    }
  });
  it('stale_flag always agrees with stale_level (schema rule)', () => {
    for (const s of SCENARIOS.filter((x) => x !== 'invalid')) {
      for (const r of mockIgnition(s, NOW) as IgnitionRecord[]) expect(r.stale_flag).toBe(r.stale_level !== 'fresh');
      const sp = mockSpread(s, NOW, POINT) as SpreadOutput;
      expect(sp.inputs.stale_flag).toBe(sp.stale_level !== 'fresh');
    }
  });
  it('modes: fresh = retrospective/hindcast, sparse = forecast, rich spread = forecast_like', () => {
    expect((mockIgnition('fresh', NOW) as IgnitionRecord[])[0].mode).toBe('retrospective');
    expect((mockSpread('fresh', NOW, POINT) as SpreadOutput).mode).toBe('hindcast');
    expect((mockIgnition('sparse', NOW) as IgnitionRecord[])[0].mode).toBe('forecast');
    expect((mockSpread('rich', NOW, POINT) as SpreadOutput).mode).toBe('forecast_like');
  });
  it('sparse spread: not_estimated, single run, no validated horizons, an empty band present', () => {
    const sp = mockSpread('sparse', NOW, POINT) as SpreadOutput;
    expect(sp.uncertainty_status).toBe('not_estimated');
    expect(sp.n_runs).toBe(1);
    expect(sp.validated_horizons_h).toEqual([]);
    expect(sp.features.some((f) => f.properties.area_ha === 0 && f.geometry.type === 'MultiPolygon')).toBe(true);
  });
  it('rich scenario carries the optional fields', () => {
    const ign = (mockIgnition('rich', NOW) as IgnitionRecord[])[0];
    expect(ign.calibration).toBeTruthy();
    expect(ign.feature_set_version).toBeTruthy();
    expect(ign.rank_in_day).toBe(1);
    expect((mockSpread('rich', NOW, POINT) as SpreadOutput).inputs.weather_series).toHaveLength(7);
  });
  it('mock spread is translated to the clicked point and stays a closed ring', () => {
    const sp = mockSpread('fresh', NOW, POINT) as unknown as { ignition_point: { longitude: number; latitude: number }; features: Array<{ geometry: { coordinates: number[][][] } }> };
    expect(sp.ignition_point).toEqual({ longitude: -85.5, latitude: 49.2 });
    const ring = sp.features[0].geometry.coordinates[0];
    expect(ring[0]).toEqual(ring[ring.length - 1]);
    expect(ring[0]).toEqual([-85.5, 49.2]);
  });
});
