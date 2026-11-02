import { describe, expect, it } from 'vitest';
import { horizonLabel, isHorizonValidated } from './horizons';
import spreadExample from '../../../contracts/examples/spread_output.example.json';

describe('unvalidated-horizon labelling', () => {
  const out = spreadExample as { validated_horizons_h?: Array<1 | 3 | 6> };
  it('example lists only 6 h as validated', () => {
    expect(isHorizonValidated(out, 6)).toBe(true);
    expect(isHorizonValidated(out, 1)).toBe(false);
    expect(isHorizonValidated(out, 3)).toBe(false);
    expect(horizonLabel(out, 1)).toBe('1 h (unvalidated)');
    expect(horizonLabel(out, 6)).toBe('6 h');
  });
  it('absent validated_horizons_h => ALL horizons unvalidated', () => {
    for (const h of [1, 3, 6]) {
      expect(isHorizonValidated({}, h)).toBe(false);
      expect(horizonLabel({}, h)).toContain('unvalidated');
    }
  });
  it('empty list => all unvalidated', () => {
    expect(isHorizonValidated({ validated_horizons_h: [] }, 6)).toBe(false);
  });
  it('listing 1, 3 and 6 validates them all', () => {
    const all = { validated_horizons_h: [1, 3, 6] as Array<1 | 3 | 6> };
    for (const h of [1, 3, 6]) expect(horizonLabel(all, h)).toBe(`${h} h`);
  });
});
