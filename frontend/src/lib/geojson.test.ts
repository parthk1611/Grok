import { describe, expect, it } from 'vitest';
import { ignitionPointGeoJSON, ignitionToGeoJSON, spreadHorizonGeoJSON } from './geojson';
import ignitionExample from '../../../contracts/examples/ignition_output.example.json';
import spreadExample from '../../../contracts/examples/spread_output.example.json';
import type { IgnitionRecord, SpreadOutput } from '../types';

describe('geojson builders', () => {
  it('ignition cells become closed polygons and report the max probability', () => {
    const recs = [ignitionExample, { ...ignitionExample, cell_id: 'ofr10km_0_100000', probability: 0.0009 }] as IgnitionRecord[];
    const { fc, skipped, maxProbability } = ignitionToGeoJSON(recs);
    expect(skipped).toBe(0);
    expect(fc.features).toHaveLength(2);
    expect(fc.features[0].geometry.coordinates[0]).toHaveLength(5);
    expect(maxProbability).toBe(0.0009);
  });
  it('counts (does not hide) unparseable cell ids', () => {
    const { fc, skipped } = ignitionToGeoJSON([{ ...ignitionExample, cell_id: 'nonsense' }] as IgnitionRecord[]);
    expect(fc.features).toHaveLength(0);
    expect(skipped).toBe(1);
  });
  it('spread horizon filter returns 3 bands ordered p10, p50, p90', () => {
    const fc = spreadHorizonGeoJSON(spreadExample as unknown as SpreadOutput, 3);
    expect(fc.features.map((f) => (f.properties as { band: string }).band)).toEqual(['p10', 'p50', 'p90']);
    expect(spreadHorizonGeoJSON(null, 3).features).toHaveLength(0);
  });
  it('ignition point feature', () => {
    const fc = ignitionPointGeoJSON(spreadExample as unknown as SpreadOutput);
    expect(fc.features[0].geometry).toEqual({ type: 'Point', coordinates: [-90, 50] });
  });
  it('not_estimated: no p10/p50/p90 bands are drawn, only one neutral single_run footprint', () => {
    const out = { ...(spreadExample as unknown as SpreadOutput), uncertainty_status: 'not_estimated' as const, n_runs: 1 };
    const fc = spreadHorizonGeoJSON(out, 6);
    expect(fc.features).toHaveLength(1);
    expect((fc.features[0].properties as { band: string }).band).toBe('single_run');
    expect(fc.features.some((f) => ['p10', 'p50', 'p90'].includes((f.properties as { band: string }).band))).toBe(false);
  });
  describe('not_estimated (decided from uncertainty_status only)', () => {
    const base = () => ({ ...(JSON.parse(JSON.stringify(spreadExample)) as SpreadOutput), uncertainty_status: 'not_estimated' as const, n_runs: 1 });
    const p50Of = (out: SpreadOutput, h: number) => out.features.find((f) => f.properties.horizon_h === h && f.properties.band === 'p50')!;
    it('variant A: p10/p90 repeat the p50 footprint -> one single_run feature, geometry of p50', () => {
      const out = base();
      for (const f of out.features) {
        if (f.properties.band !== 'p50') f.geometry = JSON.parse(JSON.stringify(p50Of(out, f.properties.horizon_h).geometry));
      }
      for (const h of [1, 3, 6]) {
        const fc = spreadHorizonGeoJSON(out, h);
        expect(fc.features).toHaveLength(1);
        expect(fc.features[0].geometry).toEqual(p50Of(out, h).geometry);
        expect((fc.features[0].properties as { band: string }).band).toBe('single_run');
      }
    });
    it('variant B: p10/p90 are empty MultiPolygons (area_ha 0) -> same single p50 footprint', () => {
      const out = base();
      for (const f of out.features) {
        if (f.properties.band !== 'p50') {
          f.geometry = { type: 'MultiPolygon', coordinates: [] };
          f.properties.area_ha = 0;
        }
      }
      const fc = spreadHorizonGeoJSON(out, 6);
      expect(fc.features).toHaveLength(1);
      expect(fc.features[0].geometry).toEqual(p50Of(out, 6).geometry);
    });
    it('never falls back to p10/p90 when p50 is empty', () => {
      const out = base();
      const p50 = p50Of(out, 6);
      p50.geometry = { type: 'MultiPolygon', coordinates: [] };
      p50.properties.area_ha = 0;
      expect(spreadHorizonGeoJSON(out, 6).features).toHaveLength(0);
    });
    it('estimated output with identical band footprints is still drawn as three bands (contents never decide)', () => {
      const out = JSON.parse(JSON.stringify(spreadExample)) as SpreadOutput;
      for (const f of out.features) f.geometry = JSON.parse(JSON.stringify(p50Of(out, f.properties.horizon_h).geometry));
      expect(spreadHorizonGeoJSON(out, 6).features.map((f) => (f.properties as { band: string }).band)).toEqual(['p10', 'p50', 'p90']);
    });
  });
  it('empty bands are not drawn', () => {
    const out = JSON.parse(JSON.stringify(spreadExample)) as SpreadOutput;
    out.features[0].geometry = { type: 'MultiPolygon', coordinates: [] };
    out.features[0].properties.area_ha = 0;
    const fc = spreadHorizonGeoJSON(out, 1);
    expect(fc.features.map((f) => (f.properties as { band: string }).band)).toEqual(['p50', 'p90']);
  });
});
