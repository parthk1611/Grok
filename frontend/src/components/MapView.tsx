import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import type { GeoJSONSource, MapMouseEvent, StyleSpecification } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
// maplibre-gl v6 loads its worker from a path relative to the main module, which breaks once Vite
// bundles it into assets/. Bundle the worker explicitly and point maplibre at it.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { FeatureCollection } from 'geojson';
import { BASEMAP, BASEMAP_DISABLED_BY_CONFIG, MAP_DEFAULTS } from '../config';
import { BAND_COLORS, rampStops } from '../lib/colors';

maplibregl.setWorkerUrl(workerUrl);

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };

function buildStyle(): StyleSpecification {
  const style: StyleSpecification = {
    version: 8,
    sources: {},
    // A plain background layer means every data layer renders with no basemap (offline demo).
    layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#e8edf1' } }],
  };
  if (!BASEMAP_DISABLED_BY_CONFIG) {
    style.sources.basemap = {
      type: 'raster',
      tiles: [BASEMAP.url],
      tileSize: BASEMAP.tileSize,
      maxzoom: BASEMAP.maxzoom,
      attribution: BASEMAP.attribution,
    };
    style.layers.push({ id: 'basemap', type: 'raster', source: 'basemap', paint: { 'raster-opacity': 0.9 } });
  }
  return style;
}

export interface MapViewProps {
  ignition: FeatureCollection;
  maxProbability: number;
  spread: FeatureCollection;
  spreadPoint: FeatureCollection;
  spreadFitKey: string | null;
  selectedCellId: string | null;
  mode: 'ignition' | 'spread';
  showBasemap: boolean;
  onCellClick: (cellId: string | null) => void;
  onSpreadClick: (lngLat: [number, number]) => void;
}

export function MapView(p: MapViewProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [tileError, setTileError] = useState(false);
  const latest = useRef(p);
  // keep the newest props for map event handlers (synced after render, never during it)
  useEffect(() => {
    latest.current = p;
  });

  // create the map once
  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: buildStyle(),
      center: MAP_DEFAULTS.center,
      zoom: MAP_DEFAULTS.zoom,
      attributionControl: { compact: false }, // always-visible attribution (OSM tile policy)
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

    map.on('load', () => {
      map.addSource('ignition', { type: 'geojson', data: EMPTY });
      map.addSource('spread', { type: 'geojson', data: EMPTY });
      map.addSource('spread-point', { type: 'geojson', data: EMPTY });
      map.addLayer({
        id: 'ignition-fill',
        type: 'fill',
        source: 'ignition',
        paint: { 'fill-color': ['interpolate', ['linear'], ['get', 'probability'], ...rampStops(1)] as never, 'fill-opacity': 0.72 },
      });
      map.addLayer({ id: 'ignition-line', type: 'line', source: 'ignition', paint: { 'line-color': '#ffffff', 'line-width': 0.6, 'line-opacity': 0.8 } });
      map.addLayer({
        id: 'ignition-selected',
        type: 'line',
        source: 'ignition',
        filter: ['==', ['get', 'cell_id'], ''],
        paint: { 'line-color': '#000000', 'line-width': 3 },
      });
      map.addLayer({
        id: 'spread-fill',
        type: 'fill',
        source: 'spread',
        paint: {
          'fill-color': ['match', ['get', 'band'], 'p10', BAND_COLORS.p10, 'p50', BAND_COLORS.p50, 'p90', BAND_COLORS.p90, '#9aa0a6'],
          'fill-opacity': 0.55,
        },
      });
      map.addLayer({
        id: 'spread-line',
        type: 'line',
        source: 'spread',
        paint: { 'line-color': '#5a1a00', 'line-width': 1.2 },
      });
      map.addLayer({
        id: 'spread-point',
        type: 'circle',
        source: 'spread-point',
        paint: { 'circle-radius': 6, 'circle-color': '#000', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 },
      });
      setReady(true);
    });

    map.on('click', (e: MapMouseEvent) => {
      const cur = latest.current;
      if (cur.mode === 'spread') {
        cur.onSpreadClick([e.lngLat.lng, e.lngLat.lat]);
        return;
      }
      const hit = map.getLayer('ignition-fill') ? map.queryRenderedFeatures(e.point, { layers: ['ignition-fill'] }) : [];
      cur.onCellClick(hit.length ? String(hit[0].properties?.cell_id ?? '') || null : null);
    });
    map.on('error', (e: unknown) => {
      const src = (e as { sourceId?: string }).sourceId;
      if (src === 'basemap') setTileError(true);
    });
    return () => {
      map.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  const setData = (id: string, data: FeatureCollection) => {
    const src = mapRef.current?.getSource(id) as GeoJSONSource | undefined;
    src?.setData(data);
  };

  useEffect(() => {
    if (!ready) return;
    setData('ignition', p.ignition);
    const map = mapRef.current!;
    map.setPaintProperty('ignition-fill', 'fill-color', ['interpolate', ['linear'], ['get', 'probability'], ...rampStops(p.maxProbability)]);
  }, [ready, p.ignition, p.maxProbability]);

  useEffect(() => {
    if (ready) {
      setData('spread', p.spread);
      setData('spread-point', p.spreadPoint);
    }
  }, [ready, p.spread, p.spreadPoint]);

  // zoom to the spread footprint when a new run arrives or the horizon changes
  useEffect(() => {
    if (!ready || !p.spreadFitKey || p.spread.features.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    const walk = (c: unknown): void => {
      if (Array.isArray(c) && typeof c[0] === 'number') bounds.extend([c[0], c[1] as number]);
      else if (Array.isArray(c)) c.forEach(walk);
    };
    p.spread.features.forEach((f) => walk((f.geometry as { coordinates: unknown }).coordinates));
    if (!bounds.isEmpty()) mapRef.current?.fitBounds(bounds, { padding: 80, maxZoom: 11, duration: 0 });
    // Intentionally keyed on spreadFitKey only: refit on a new run or horizon, not on every new features array.
    // The features are read from the props of the render that changed the key.
  }, [ready, p.spreadFitKey]); // eslint-disable-line react-hooks/exhaustive-deps -- see comment above

  useEffect(() => {
    if (ready) mapRef.current!.setFilter('ignition-selected', ['==', ['get', 'cell_id'], p.selectedCellId ?? '']);
  }, [ready, p.selectedCellId]);

  useEffect(() => {
    const map = mapRef.current;
    if (ready && map?.getLayer('basemap')) map.setLayoutProperty('basemap', 'visibility', p.showBasemap ? 'visible' : 'none');
  }, [ready, p.showBasemap]);

  useEffect(() => {
    const map = mapRef.current;
    if (map) map.getCanvas().style.cursor = p.mode === 'spread' ? 'crosshair' : '';
  }, [ready, p.mode]);

  return (
    <div className="mapwrap">
      <div ref={container} className="map" data-testid="map" />
      {tileError && p.showBasemap && (
        <div className="map-note" data-testid="basemap-error">
          Basemap tiles unavailable (offline?). Data layers are still shown.
        </div>
      )}
    </div>
  );
}
