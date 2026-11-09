import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ApiClient, IgnitionResult, SpreadOptions } from './api/client';
import { createConfiguredClient, readApiConfig } from './api';
import { BASEMAP_DISABLED_BY_CONFIG } from './config';
import { DEV_SCENARIOS, type DevScenario } from './mocks/fixtures';
import type { ApiErrorInfo, Loaded, WeatherOverride, WeatherRecord } from './types';
import type { RequestedDate } from './lib/ignitionDate';
import { torontoDate } from './lib/time';
import { decideMock } from './lib/mockDecision';
import { MockBanner } from './components/Banners';
import { DevToggle } from './components/DevToggle';
import { Footer } from './components/Footer';
import { DegradedRunDialog } from './components/DegradedRunDialog';
import { MapView } from './components/MapView';
import { CellDetails, IgnitionPanel, Legend, SpreadPanel, WeatherPanel, type SpreadRun } from './components/Panels';
import { spreadConfirmationReasons } from './lib/gate';
import { ignitionPointGeoJSON, ignitionToGeoJSON, spreadHorizonGeoJSON } from './lib/geojson';
import type { Horizon } from './lib/horizons';

/** A mutable value read by non-React code (the mock adapter), outside React state. */
function createValueStore<T>(initial: T) {
  let value = initial;
  return { get: () => value, set: (v: T) => void (value = v) };
}

function initialScenario(): DevScenario {
  const q = new URLSearchParams(window.location.search).get('scenario');
  return (DEV_SCENARIOS as readonly string[]).includes(q ?? '') ? (q as DevScenario) : 'fresh';
}

function initialDate(): RequestedDate {
  const q = new URLSearchParams(window.location.search).get('date');
  return q === 'latest' || /^\d{4}-\d{2}-\d{2}$/.test(q ?? '') ? (q as string) : torontoDate(new Date());
}

interface PendingConfirm {
  point: [number, number];
  reasons: string[];
  /** Set when the API itself answered 409 confirmation_required. */
  serverError?: ApiErrorInfo | null;
}

export interface AppProps {
  /** Test hook: build the client from the DEV scenario getter. Defaults to the in-browser MOCK adapter. */
  makeClient?: (getScenario: () => DevScenario) => ApiClient;
}

const API_CONFIG = readApiConfig();

export default function App({ makeClient }: AppProps = {}) {
  const [scenario, setScenario] = useState<DevScenario>(initialScenario);
  const [ignitionDate, setIgnitionDate] = useState<RequestedDate>(initialDate);
  // Built once per mount (lazy state initializer). The mock adapter reads the DEV scenario from a small store
  // that is synced in an effect declared before the loading effect, so it is current when that effect runs.
  const [{ client, scenarioStore }] = useState(() => {
    const store = createValueStore(scenario);
    const make = makeClient ?? ((g: () => DevScenario) => createConfiguredClient(API_CONFIG, g));
    return { client: make(store.get), scenarioStore: store };
  });
  useEffect(() => {
    scenarioStore.set(scenario);
  }, [scenarioStore, scenario]);
  
  const [ignition, setIgnition] = useState<Loaded<IgnitionResult> | null>(null);
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

  // Clear everything derived from the old fixture/date. Called from the handlers that change them (not from an
  // effect, so there is no cascading render); the loading effect below then fetches the new map.
  const resetDerived = useCallback(() => {
    generation.current += 1; // invalidates in-flight requests
    setIgnition(null);
    setWeather(null);
    setRun(null);
    setPending(null);
    setSpreadLoading(false);
    setSelectedCellId(null);
  }, []);
  const changeScenario = useCallback((s: DevScenario) => {
    resetDerived();
    setScenario(s);
  }, [resetDerived]);
  const changeDate = useCallback((d: RequestedDate) => {
    resetDerived();
    setIgnitionDate(d);
  }, [resetDerived]);

  // (re)load ignition whenever the fixture or the date changes
  useEffect(() => {
    const gen = ++generation.current;
    void client.loadIgnition(ignitionDate).then((r) => {
      if (gen === generation.current) setIgnition(r);
    });
  }, [scenario, ignitionDate, client]);

  const execRun = useCallback(
    async (point: [number, number], opts: Pick<SpreadOptions, 'confirmDegraded' | 'override'> = {}) => {
      const gen = generation.current;
      setSpreadLoading(true);
      setPending(null);
      const result = await client.runSpread(point, opts);
      if (gen !== generation.current) return;
      setSpreadLoading(false);
      // 409 confirmation_required opens the expired-weather flow, unless the user already confirmed or
      // entered inputs (then the 409 is shown as an error, never looped).
      if (!result.ok && result.kind === 'api_error' && result.apiError?.reason === 'confirmation_required' && !opts.confirmDegraded && !opts.override) {
        setPending({ point, reasons: ['The API reports that the live weather for this point is expired.'], serverError: result.apiError });
        return;
      }
      setRun({ point, result, degradedConfirmed: !!opts.confirmDegraded });
      setRunId((n) => n + 1);
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
      await execRun(point);
    },
    [client, execRun],
  );

  const cells = useMemo(() => (ignition?.ok ? ignitionToGeoJSON(ignition.data.records) : null), [ignition]);
  const spreadOut = run?.result.ok ? run.result.data : null;
  const spreadGeo = useMemo(() => spreadHorizonGeoJSON(spreadOut, horizon), [spreadOut, horizon]);
  const spreadPointGeo = useMemo(() => ignitionPointGeoJSON(spreadOut), [spreadOut]);
  const selected = ignition?.ok ? (ignition.data.records.find((r) => r.cell_id === selectedCellId) ?? null) : null;
  // MOCK whenever the adapter is a mock, OR until at least one response explicitly says it is not mock and
  // none says it is (envelope `mock` / X-Mock-Data). Fail safe: unknown => MOCK.
  const isMock = decideMock(client.adapter.isMock, [ignition?.meta, weather?.meta, run?.result.meta]);

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
        <div className="datepick" data-testid="date-picker">
          <label>
            <input
              type="checkbox"
              checked={ignitionDate === 'latest'}
              onChange={(e) => changeDate(e.target.checked ? 'latest' : torontoDate(new Date()))}
              data-testid="date-latest"
            />{' '}
            latest map
          </label>
          <input
            type="date"
            aria-label="Ignition map date (America/Toronto)"
            value={ignitionDate === 'latest' ? '' : ignitionDate}
            disabled={ignitionDate === 'latest'}
            onChange={(e) => e.target.value && changeDate(e.target.value)}
            data-testid="date-input"
          />
        </div>
        {client.adapter.isMock && <DevToggle scenario={scenario} onChange={changeScenario} />}
      </header>
      {isMock ? <MockBanner /> : <div className="banner banner-warn" data-testid="live-banner">Responses report X-Mock-Data: false. Check model_version on every panel before trusting a value.</div>}
      {!makeClient && API_CONFIG.configError && <div className="banner banner-warn" data-testid="config-error">{API_CONFIG.configError}</div>}
      {!client.adapter.isMock && <div className="small adapter-note" data-testid="adapter-name">Data source: {client.adapter.name}</div>}
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
          {mode === 'spread' && <div className="map-hint" data-testid="spread-hint">Spread mode: click anywhere on the map to request a {isMock ? 'MOCK ' : ''}spread run.</div>}
          {cells && cells.skipped > 0 && <div className="map-note">{cells.skipped} cell(s) skipped: unparseable cell_id</div>}
        </div>
        <aside className="sidebar">
          <IgnitionPanel state={ignition} isMock={isMock} />
          {ignition?.ok && <CellDetails record={selected} onClose={() => setSelectedCellId(null)} />}
          <WeatherPanel state={weather} isMock={isMock} />
          <SpreadPanel run={run} loading={spreadLoading} horizon={horizon} onHorizon={setHorizon} isMock={isMock} />
        </aside>
      </main>
      <Footer isMock={isMock} />
      {pending && (
        <DegradedRunDialog
          reasons={pending.reasons}
          serverError={pending.serverError}
          onCancel={() => setPending(null)}
          onRunDegraded={() => void execRun(pending.point, { confirmDegraded: true })}
          onManual={(o: WeatherOverride) => void execRun(pending.point, { override: o })}
        />
      )}
    </div>
  );
}
