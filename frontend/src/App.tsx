import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createApiClient, createMockAdapter } from './api/client';
import { BASEMAP_DISABLED_BY_CONFIG } from './config';
import { SCENARIOS, type Scenario } from './mocks/fixtures';
import type { IgnitionRecord, Loaded, WeatherRecord } from './types';
import { MockBanner } from './components/Banners';
import { DevToggle } from './components/DevToggle';
import { Footer } from './components/Footer';
import { ConfirmDialog } from './components/ConfirmDialog';
import { MapView } from './components/MapView';
import { CellDetails, IgnitionPanel, Legend, SpreadPanel, WeatherPanel, type SpreadRun } from './components/Panels';
import { spreadConfirmationReasons } from './lib/gate';
import { ignitionPointGeoJSON, ignitionToGeoJSON, spreadHorizonGeoJSON } from './lib/geojson';
import type { Horizon } from './lib/horizons';

function initialScenario(): Scenario {
  const q = new URLSearchParams(window.location.search).get('scenario');
  return (SCENARIOS as readonly string[]).includes(q ?? '') ? (q as Scenario) : 'fresh';
}

interface PendingConfirm {
  point: [number, number];
  reasons: string[];
}

export default function App() {
  const [scenario, setScenario] = useState<Scenario>(initialScenario);
  const scenarioRef = useRef(scenario);
  scenarioRef.current = scenario;
  const client = useMemo(() => createApiClient(createMockAdapter({ getScenario: () => scenarioRef.current })), []);
  const isMock = client.adapter.isMock;

  const [ignition, setIgnition] = useState<Loaded<IgnitionRecord[]> | null>(null);
  const [weather, setWeather] = useState<Loaded<WeatherRecord> | null>(null);
  const [run, setRun] = useState<SpreadRun | null>(null);
  const [runId, setRunId] = useState(0);
  const [spreadLoading, setSpreadLoading] = useState(false);
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const [mode, setMode] = useState<'ignition' | 'spread'>('ignition');
  const [horizon, setHorizon] = useState<Horizon>(6);
  const [selectedCellId, setSelectedCellId] = useState<string | null>(null);
  const [showBasemap, setShowBasemap] = useState(!BASEMAP_DISABLED_BY_CONFIG);
  const generation = useRef(0); // invalidates in-flight requests when the fixture changes

  // (re)load ignition whenever the fixture changes; clear everything derived from the old fixture
  useEffect(() => {
    const gen = ++generation.current;
    setIgnition(null);
    setWeather(null);
    setRun(null);
    setPending(null);
    setSpreadLoading(false);
    setSelectedCellId(null);
    void client.loadIgnition().then((r) => {
      if (gen === generation.current) setIgnition(r);
    });
  }, [scenario, client]);

  const execRun = useCallback(
    async (point: [number, number], degradedConfirmed: boolean) => {
      const gen = generation.current;
      setSpreadLoading(true);
      const result = await client.runSpread(point);
      if (gen !== generation.current) return;
      setRun({ point, result, degradedConfirmed });
      setRunId((n) => n + 1);
      setSpreadLoading(false);
    },
    [client],
  );

  const requestSpread = useCallback(
    async (point: [number, number]) => {
      const gen = generation.current;
      setSpreadLoading(true);
      setPending(null);
      const wx = await client.loadWeather(point);
      if (gen !== generation.current) return;
      setWeather(wx);
      const reasons = spreadConfirmationReasons(wx);
      if (reasons.length > 0) {
        setSpreadLoading(false);
        setPending({ point, reasons });
        return;
      }
      await execRun(point, false);
    },
    [client, execRun],
  );

  const cells = useMemo(() => (ignition?.ok ? ignitionToGeoJSON(ignition.data) : null), [ignition]);
  const spreadOut = run?.result.ok ? run.result.data : null;
  const spreadGeo = useMemo(() => spreadHorizonGeoJSON(spreadOut, horizon), [spreadOut, horizon]);
  const spreadPointGeo = useMemo(() => ignitionPointGeoJSON(spreadOut), [spreadOut]);
  const selected = ignition?.ok ? (ignition.data.find((r) => r.cell_id === selectedCellId) ?? null) : null;

  return (
    <div className="app">
      <header className="topbar">
        <h1>COMP-385 Ontario wildfire decision support</h1>
        <div className="modes" role="group" aria-label="Map mode">
          <button type="button" className={mode === 'ignition' ? 'active' : ''} aria-pressed={mode === 'ignition'} onClick={() => setMode('ignition')} data-testid="mode-ignition">
            Ignition risk
          </button>
          <button type="button" className={mode === 'spread' ? 'active' : ''} aria-pressed={mode === 'spread'} onClick={() => setMode('spread')} data-testid="mode-spread">
            Spread (click map)
          </button>
        </div>
        <label className="small">
          <input type="checkbox" checked={showBasemap} disabled={BASEMAP_DISABLED_BY_CONFIG} onChange={(e) => setShowBasemap(e.target.checked)} data-testid="basemap-toggle" /> Basemap
          {BASEMAP_DISABLED_BY_CONFIG ? ' (disabled by config)' : ''}
        </label>
        <DevToggle scenario={scenario} onChange={setScenario} />
      </header>
      <MockBanner />
      <main className="main">
        <div className="mapcol">
          <MapView
            ignition={cells?.fc ?? { type: 'FeatureCollection', features: [] }}
            maxProbability={cells?.maxProbability ?? 0}
            spread={spreadGeo}
            spreadPoint={spreadPointGeo}
            spreadFitKey={spreadOut ? `${runId}-${horizon}` : null}
            selectedCellId={selectedCellId}
            mode={mode}
            showBasemap={showBasemap}
            onCellClick={setSelectedCellId}
            onSpreadClick={(p) => void requestSpread(p)}
          />
          <Legend
            maxProbability={cells?.maxProbability ?? 0}
            mode={mode}
            spreadVisible={!!spreadOut}
            estimated={spreadOut ? spreadOut.uncertainty_status === 'estimated' : true}
          />
          {mode === 'spread' && <div className="map-hint" data-testid="spread-hint">Spread mode: click anywhere on the map to request a MOCK spread run.</div>}
          {cells && cells.skipped > 0 && <div className="map-note">{cells.skipped} cell(s) skipped: unparseable cell_id</div>}
        </div>
        <aside className="sidebar">
          <IgnitionPanel state={ignition} isMock={isMock} />
          {ignition?.ok && <CellDetails record={selected} onClose={() => setSelectedCellId(null)} />}
          <WeatherPanel state={weather} isMock={isMock} />
          <SpreadPanel run={run} loading={spreadLoading} horizon={horizon} onHorizon={setHorizon} isMock={isMock} />
        </aside>
      </main>
      <Footer />
      {pending && (
        <ConfirmDialog
          title="Run spread on expired or unavailable weather?"
          body={[...pending.reasons, 'The result would be degraded and must not be treated as current. Continue only if you understand this.']}
          confirmLabel="Run anyway (degraded)"
          onCancel={() => setPending(null)}
          onConfirm={() => {
            const p = pending.point;
            setPending(null);
            void execRun(p, true);
          }}
        />
      )}
    </div>
  );
}
