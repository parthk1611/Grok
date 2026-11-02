// Single place for tunable configuration.

/**
 * Staleness thresholds, minutes of age.
 * SOURCE: contracts/cache_fallback.md ("Age and levels"). PROPOSED defaults,
 * pending Team Lead approval and RELAY's measurement of real feed cadence.
 * Do not hard-code these anywhere else.
 */
export const STALENESS_THRESHOLDS = {
  /** age <= this => fresh (two missed 15-minute polls) */
  freshMaxMinutes: 30,
  /** freshMax < age <= this => stale; above => expired */
  staleMaxMinutes: 6 * 60,
  /** shown in the UI next to every threshold-derived statement */
  status: 'proposed' as const,
};

export const TIME_ZONE = 'America/Toronto';

/**
 * Basemap. Free OpenStreetMap raster tiles by default (attribution required;
 * see https://operations.osmfoundation.org/policies/tiles/ - fine for a demo,
 * not for production traffic). Override at build/dev time with
 *   VITE_BASEMAP_URL  (a {z}/{x}/{y} tile URL template, or "none" to disable)
 *   VITE_BASEMAP_ATTRIBUTION
 * The map always renders every data layer without a basemap (offline demo).
 */
const env = import.meta.env as Record<string, string | undefined>;
export const BASEMAP = {
  url: env.VITE_BASEMAP_URL ?? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  attribution:
    env.VITE_BASEMAP_ATTRIBUTION ??
    '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
  tileSize: 256,
  maxzoom: 19,
};
export const BASEMAP_DISABLED_BY_CONFIG = BASEMAP.url.trim().toLowerCase() === 'none';

export const MAP_DEFAULTS = {
  center: [-86, 50.5] as [number, number], // northern Ontario
  zoom: 4.6,
};
