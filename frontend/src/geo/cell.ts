// Ignition grid geometry. Contract: contracts/grid_spec.md
//   cell_id = ofr10km_{x0}_{y0}, (x0, y0) = SW corner in EPSG:3978 metres, 10 km cells.
import proj4 from 'proj4';

export const CELL_SIZE_M = 10_000;

/** EPSG:3978 NAD83 / Canada Atlas Lambert (proj4 definition from the EPSG registry). */
export const EPSG_3978 =
  '+proj=lcc +lat_1=49 +lat_2=77 +lat_0=49 +lon_0=-95 +x_0=0 +y_0=0 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs';

const toLonLat = proj4(EPSG_3978, 'EPSG:4326');

const CELL_ID_RE = /^ofr10km_(-?\d+)_(-?\d+)$/;

export interface CellCorner {
  x0: number;
  y0: number;
}

export function parseCellId(cellId: string): CellCorner | null {
  const m = CELL_ID_RE.exec(cellId);
  if (!m) return null;
  return { x0: Number(m[1]), y0: Number(m[2]) };
}

export type LonLat = [number, number];

/** Closed GeoJSON ring (lon, lat), counter-clockwise from the SW corner. Null if the id is malformed. */
export function cellIdToRing(cellId: string): LonLat[] | null {
  const c = parseCellId(cellId);
  if (!c) return null;
  const { x0, y0 } = c;
  const s = CELL_SIZE_M;
  const corners: Array<[number, number]> = [
    [x0, y0],
    [x0 + s, y0],
    [x0 + s, y0 + s],
    [x0, y0 + s],
  ];
  const ring = corners.map(([x, y]) => toLonLat.forward([x, y]) as LonLat);
  ring.push(ring[0]);
  return ring;
}

/** Cell centre (x0+5000, y0+5000) reprojected to lon/lat. */
export function cellIdToCentre(cellId: string): LonLat | null {
  const c = parseCellId(cellId);
  if (!c) return null;
  return toLonLat.forward([c.x0 + CELL_SIZE_M / 2, c.y0 + CELL_SIZE_M / 2]) as LonLat;
}

/** Inverse: lon/lat -> the cell id containing it (floor to the 10 km lattice). */
export function lonLatToCellId(lon: number, lat: number): string {
  const [x, y] = proj4('EPSG:4326', EPSG_3978, [lon, lat]);
  const x0 = Math.floor(x / CELL_SIZE_M) * CELL_SIZE_M;
  const y0 = Math.floor(y / CELL_SIZE_M) * CELL_SIZE_M;
  return `ofr10km_${x0}_${y0}`;
}
