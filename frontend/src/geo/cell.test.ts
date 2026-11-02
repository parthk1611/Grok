import { describe, expect, it } from 'vitest';
import { cellIdToCentre, cellIdToRing, lonLatToCellId, parseCellId } from './cell';

// Expected centroids copied from data/interim/ontario_fire_region_10km_grid.csv
// (centroid_longitude, centroid_latitude, 6 decimals) - independent of this code.
const GRID_CSV_ROWS: Array<[string, number, number]> = [
  ['ofr10km_-10000_100000', -95.069909, 49.945759],
  ['ofr10km_-10000_110000', -95.070064, 50.035999],
  ['ofr10km_160000_310000', -92.582499, 51.818095],
  ['ofr10km_950000_-210000', -82.656465, 46.334669],
  ['ofr10km_1390000_-240000', -77.321375, 45.161867],
  ['ofr10km_800000_450000', -82.952593, 52.423897],
];

describe('cell_id -> polygon', () => {
  it('parses negative and positive corners', () => {
    expect(parseCellId('ofr10km_-10000_100000')).toEqual({ x0: -10000, y0: 100000 });
    expect(parseCellId('ofr10km_950000_-210000')).toEqual({ x0: 950000, y0: -210000 });
  });

  it('rejects malformed ids', () => {
    for (const bad of ['', 'ofr10km_1_', 'cell_1_2', 'ofr10km_1.5_2', 'ofr10km_a_b', 'ofr10km_-10000_100000x']) {
      expect(parseCellId(bad)).toBeNull();
      expect(cellIdToRing(bad)).toBeNull();
    }
  });

  it('builds a closed 5-point ring in lon/lat', () => {
    const ring = cellIdToRing('ofr10km_-10000_100000')!;
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
    for (const [lon, lat] of ring) {
      expect(lon).toBeGreaterThan(-96);
      expect(lon).toBeLessThan(-94);
      expect(lat).toBeGreaterThan(49);
      expect(lat).toBeLessThan(51);
    }
  });

  it('is counter-clockwise (SW, SE, NE, NW) and about 10 km on a side', () => {
    const ring = cellIdToRing('ofr10km_-10000_100000')!;
    const [sw, se, ne, nw] = ring;
    expect(se[0]).toBeGreaterThan(sw[0]);
    expect(ne[1]).toBeGreaterThan(se[1] - 0.01);
    expect(nw[1]).toBeGreaterThan(sw[1]);
    // ~10 km north-south: 1 deg lat ~ 111.2 km
    const dLatKm = (nw[1] - sw[1]) * 111.2;
    expect(dLatKm).toBeGreaterThan(9.5);
    expect(dLatKm).toBeLessThan(10.5);
  });

  it.each(GRID_CSV_ROWS)('centre of %s matches the grid CSV centroid (<= 2e-5 deg)', (id, lon, lat) => {
    const c = cellIdToCentre(id)!;
    expect(Math.abs(c[0] - lon)).toBeLessThan(2e-5);
    expect(Math.abs(c[1] - lat)).toBeLessThan(2e-5);
  });

  it('round-trips: the centre of a cell maps back to the same cell id', () => {
    for (const [id] of GRID_CSV_ROWS) {
      const c = cellIdToCentre(id)!;
      expect(lonLatToCellId(c[0], c[1])).toBe(id);
    }
  });

  it('neighbouring cells share an edge (no gaps in the lattice)', () => {
    const a = cellIdToRing('ofr10km_0_100000')!;
    const b = cellIdToRing('ofr10km_10000_100000')!;
    expect(a[1][0]).toBeCloseTo(b[0][0], 9);
    expect(a[1][1]).toBeCloseTo(b[0][1], 9);
    expect(a[2][0]).toBeCloseTo(b[3][0], 9);
  });
});
