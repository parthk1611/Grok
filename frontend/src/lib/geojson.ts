import type { Feature, FeatureCollection, Polygon } from 'geojson';
import { cellIdToRing } from '../geo/cell';
import type { IgnitionRecord, SpreadOutput } from '../types';
import { BANDS } from './horizons';

export interface CellProps {
  cell_id: string;
  probability: number;
  date: string;
}

/** Ignition records -> polygons derived from cell_id (EPSG:3978 SW corner, 10 km) in EPSG:4326. Records with unparseable ids are skipped and counted. */
export function ignitionToGeoJSON(records: IgnitionRecord[]): { fc: FeatureCollection<Polygon, CellProps>; skipped: number; maxProbability: number } {
  const features: Array<Feature<Polygon, CellProps>> = [];
  let skipped = 0;
  let maxProbability = 0;
  for (const r of records) {
    const ring = cellIdToRing(r.cell_id);
    if (!ring) {
      skipped += 1;
      continue;
    }
    maxProbability = Math.max(maxProbability, r.probability);
    features.push({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [ring] },
      properties: { cell_id: r.cell_id, probability: r.probability, date: r.date },
    });
  }
  return { fc: { type: 'FeatureCollection', features }, skipped, maxProbability };
}

/** Spread features for one horizon, ordered outer (p10) -> inner (p90) so inner bands draw on top. */
export function spreadHorizonGeoJSON(out: SpreadOutput | null, horizon: number): FeatureCollection {
  const features = (out?.features ?? [])
    .filter((f) => f.properties.horizon_h === horizon)
    .sort((a, b) => BANDS.indexOf(a.properties.band) - BANDS.indexOf(b.properties.band));
  return { type: 'FeatureCollection', features };
}

export function ignitionPointGeoJSON(out: SpreadOutput | null): FeatureCollection {
  if (!out) return { type: 'FeatureCollection', features: [] };
  return {
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', geometry: { type: 'Point', coordinates: [out.ignition_point.longitude, out.ignition_point.latitude] }, properties: {} },
    ],
  };
}
