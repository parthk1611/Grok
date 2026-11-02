import { describe, expect, it } from 'vitest';
import { SCENARIOS, mockIgnition, mockSpread, mockWeather } from './fixtures';
import { MOCK_MODEL_VERSION, validateIgnitionArray, validateSpreadOutput, validateWeatherRecord } from '../lib/contract';
import { assessOutput, assessWeather } from '../lib/staleness';
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

describe('scenario timestamps are consistent with their label', () => {
  it('fresh is fresh, stale is stale, expired is expired (ignition, weather, spread)', () => {
    for (const s of ['fresh', 'stale', 'expired'] as const) {
      const ign = mockIgnition(s, NOW) as Array<{ data_timestamp: string; stale_flag: boolean; stale_reason?: string }>;
      const a = assessOutput({ stale_flag: ign[0].stale_flag, stale_reason: ign[0].stale_reason, timestamp: ign[0].data_timestamp, ageBasis: 'data_timestamp', now: NOW });
      expect(a.level).toBe(s);
      const wx = mockWeather(s, NOW, null) as Parameters<typeof assessWeather>[0];
      expect(assessWeather(wx).level).toBe(s);
      expect(assessWeather(wx).levelMismatch).toBe(false);
      const sp = mockSpread(s, NOW, POINT) as { inputs: { weather_valid_time: string; stale_flag: boolean } };
      expect(assessOutput({ stale_flag: sp.inputs.stale_flag, timestamp: sp.inputs.weather_valid_time, ageBasis: 'weather_valid_time', now: NOW }).level).toBe(s);
    }
  });
  it('mock spread is translated to the clicked point and stays a closed ring', () => {
    const sp = mockSpread('fresh', NOW, POINT) as { ignition_point: { longitude: number; latitude: number }; features: Array<{ geometry: { coordinates: number[][][] } }> };
    expect(sp.ignition_point).toEqual({ longitude: -85.5, latitude: 49.2 });
    const ring = sp.features[0].geometry.coordinates[0];
    expect(ring[0]).toEqual(ring[ring.length - 1]);
    expect(ring[0]).toEqual([-85.5, 49.2]);
  });
});
