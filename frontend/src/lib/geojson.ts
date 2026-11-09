import type { Feature, FeatureCollection, Polygon } from 'geojson';
import { cellIdToRing } from '../geo/cell';
import type { IgnitionRecord, SpreadFeature, SpreadOutput } from '../types';
import { BANDS, isEmptyBand } from './horizons';

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

/**
 * Spread features to draw for one horizon. The decision is made ONLY from `uncertainty_status`,
 * never from what the band features contain (Team Lead ruling, 2026-11-09):
 * - "estimated": p10, p50, p90 bands, ordered outer -> inner so inner bands draw on top
 *   (even if two bands happen to share a footprint).
 * - "not_estimated": exactly one feature, the p50 footprint, tagged band "single_run" so the map styles it
 *   neutrally and labels it "single-run footprint". p10 and p90 are ignored whether they repeat the same
 *   footprint (BLAZE's emission) or are empty MultiPolygons (alternative still being settled). There is no
 *   fallback to p10/p90: if p50 is missing or empty, nothing is drawn.
 * Empty bands (empty MultiPolygon, area_ha 0) are never drawn.
 */
export function spreadHorizonGeoJSON(out: SpreadOutput | null, horizon: number): FeatureCollection {
  if (!out) return { type: 'FeatureCollection', features: [] };
  const inHorizon = out.features.filter((f) => f.properties.horizon_h === horizon);
  if (out.uncertainty_status === 'not_estimated') {
    const single = singleRunFootprint(out, horizon);
    return {
      type: 'FeatureCollection',
      features: single && !isEmptyBand(single) ? [{ ...single, properties: { ...single.properties, band: 'single_run' } }] : [],
    };
  }
  const features = inHorizon
    .filter((f) => !isEmptyBand(f))
    .sort((a, b) => BANDS.indexOf(a.properties.band) - BANDS.indexOf(b.properties.band));
  return { type: 'FeatureCollection', features };
}

/** The p50 feature of a horizon (the single-run footprint when uncertainty is not estimated), or null. */
export function singleRunFootprint(out: SpreadOutput, horizon: number): SpreadFeature | null {
  return out.features.find((f) => f.properties.horizon_h === horizon && f.properties.band === 'p50') ?? null;
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
