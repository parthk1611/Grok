// Neutral sequential ramp (viridis, 5 stops). Deliberately NOT red/amber/green:
// no danger categories are approved, so colour must not imply a danger level.
export const RAMP = ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'] as const;

/** MapLibre `interpolate` stops for a linear 0..max scale. Stops must be strictly ascending. */
export function rampStops(max: number): Array<number | string> {
  const m = max > 0 ? max : 1;
  const out: Array<number | string> = [];
  RAMP.forEach((c, i) => out.push((m * i) / (RAMP.length - 1), c));
  return out;
}

export function formatProbability(p: number): string {
  if (p === 0) return '0';
  if (p < 0.001) return p.toExponential(2);
  return p.toPrecision(3);
}

export const BAND_COLORS = { p10: '#fdd49e', p50: '#fc8d59', p90: '#b30000' } as const;
