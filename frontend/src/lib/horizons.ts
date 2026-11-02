import type { SpreadOutput, SpreadFeature } from '../types';

export const HORIZONS = [1, 3, 6] as const;
export type Horizon = (typeof HORIZONS)[number];
export const BANDS = ['p10', 'p50', 'p90'] as const;

/**
 * A horizon is validated only if it is listed in validated_horizons_h.
 * If the field is absent, ALL horizons are treated as unvalidated.
 * (Per-feature `validated` is informational and is not used here.)
 */
export function isHorizonValidated(out: Pick<SpreadOutput, 'validated_horizons_h'>, horizon: number): boolean {
  return Array.isArray(out.validated_horizons_h) && (out.validated_horizons_h as number[]).includes(horizon);
}

export function horizonLabel(out: Pick<SpreadOutput, 'validated_horizons_h'>, horizon: number): string {
  return `${horizon} h${isHorizonValidated(out, horizon) ? '' : ' (unvalidated)'}`;
}

export function featuresForHorizon(out: SpreadOutput, horizon: number): SpreadFeature[] {
  return out.features.filter((f) => f.properties.horizon_h === horizon);
}
