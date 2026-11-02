import { useMemo } from 'react';
import type { IgnitionRecord, Loaded, SpreadOutput, WeatherRecord } from '../types';
import { MOCK_MODEL_VERSION } from '../lib/contract';
import { assessIgnition, assessSpread, assessWeather, maxLevel, type StalenessAssessment } from '../lib/staleness';
import { BANDS, HORIZONS, horizonLabel, isEmptyBand, isHorizonValidated, type Horizon } from '../lib/horizons';
import { formatAge, formatToronto, formatUtc } from '../lib/time';
import { BAND_COLORS, formatProbability, RAMP } from '../lib/colors';
import { cellIdToCentre, parseCellId } from '../geo/cell';
import { ErrorState, LevelChip, MockBadge, StaleBanner, UnvalidatedBadge } from './Banners';
import { ModeBadge } from './ModeBadge';
import { Disclaimer, fmtNum, KV, TimeStamp } from './Meta';

const isMockVersion = (v: string) => v === MOCK_MODEL_VERSION;

function Loading({ what }: { what: string }) {
  return <p className="muted">Loading {what}…</p>;
}

/** The record with the worst staleness; used for the panel-level banner. */
function worstIgnition(records: IgnitionRecord[]): { rec: IgnitionRecord; a: StalenessAssessment } {
  let best = records[0];
  let bestA = assessIgnition(best);
  for (const r of records.slice(1)) {
    const a = assessIgnition(r);
    if (maxLevel(bestA.level, a.level) !== bestA.level) {
      best = r;
      bestA = a;
    }
  }
  return { rec: best, a: bestA };
}

export function IgnitionPanel({ state, isMock }: { state: Loaded<IgnitionRecord[]> | null; isMock: boolean }) {
  return (
    <section className="panel" data-testid="ignition-panel">
      <h2>
        Ignition risk {isMock && <MockBadge />}
      </h2>
      {!state ? (
        <Loading what="ignition risk" />
      ) : !state.ok ? (
        <ErrorState title={state.title} errors={state.errors} kind={state.kind} />
      ) : (
        <IgnitionBody records={state.data} />
      )}
    </section>
  );
}

function IgnitionBody({ records }: { records: IgnitionRecord[] }) {
  const { rec, a } = useMemo(() => worstIgnition(records), [records]);
  const versions = [...new Set(records.map((r) => r.model_version))];
  const modes = [...new Set(records.map((r) => r.mode))];
  const newest = records.reduce((m, r) => (r.data_timestamp > m ? r.data_timestamp : m), records[0].data_timestamp);
  return (
    <>
      <div>
        {modes.map((m) => (
          <ModeBadge key={m} mode={m} />
        ))}
      </div>
      <StaleBanner assessment={a} subject="Ignition inputs" />
      <KV k="cells" v={records.length} />
      <KV k="date (Toronto day)" v={rec.date} mono />
      <KV k="model_version" v={versions.join(', ')} mono />
      <TimeStamp iso={newest} label="data_timestamp" />
      <KV
        k="staleness"
        v={
          <>
            <LevelChip assessment={a} /> data age {formatAge(a.ageMinutes)} · stale_flag {String(rec.stale_flag)} · mode {a.mode}
          </>
        }
      />
      <Disclaimer text={rec.disclaimer} />
    </>
  );
}

export function CellDetails({ record, onClose }: { record: IgnitionRecord | null; onClose: () => void }) {
  if (!record) return <p className="muted small">Click a cell on the map for details.</p>;
  const c = cellIdToCentre(record.cell_id);
  const corner = parseCellId(record.cell_id);
  const a = assessIgnition(record);
  return (
    <section className="panel" data-testid="cell-details">
      <h3>
        Cell {record.cell_id}{' '}
        <button type="button" className="link" onClick={onClose} aria-label="Close cell details">
          close
        </button>
      </h3>
      <ModeBadge mode={record.mode} />
      <StaleBanner assessment={a} subject="This cell's inputs" />
      <KV k="probability (raw)" v={`${formatProbability(record.probability)} (P ≥1 ignition in cell on date; no danger category)`} />
      <KV k="date" v={record.date} mono />
      <KV k="rank in day" v={record.rank_in_day ?? <span className="missing">not provided</span>} />
      <KV k="calibration" v={record.calibration === undefined ? <span className="missing">not provided</span> : (record.calibration ?? 'null')} />
      <KV k="feature_set_version" v={record.feature_set_version ?? <span className="missing">not provided</span>} mono />
      <KV k="grid_version" v={record.grid_version ?? <span className="missing">not provided</span>} mono />
      <KV k="model_version" v={record.model_version} mono />
      {c && <KV k="centre" v={`${c[1].toFixed(4)}°N, ${c[0].toFixed(4)}°E`} />}
      {corner && <KV k="SW corner EPSG:3978" v={`x ${corner.x0} m, y ${corner.y0} m`} mono />}
      <TimeStamp iso={record.data_timestamp} label="data_timestamp" />
      {record.generated_at && <TimeStamp iso={record.generated_at} label="generated_at" />}
      <KV k="staleness" v={<><LevelChip assessment={a} /> age {formatAge(a.ageMinutes)} · stale_flag {String(record.stale_flag)}</>} />
      <Disclaimer text={record.disclaimer} />
    </section>
  );
}

export function WeatherPanel({ state, isMock }: { state: Loaded<WeatherRecord> | null; isMock: boolean }) {
  return (
    <section className="panel" data-testid="weather-panel">
      <h2>
        Live weather {isMock && <MockBadge />}
      </h2>
      {!state ? (
        <p className="muted small">No weather loaded yet. It is requested for the clicked point when a spread run starts.</p>
      ) : !state.ok ? (
        <ErrorState title={state.title} errors={state.errors} kind={state.kind} />
      ) : (
        <WeatherBody rec={state.data} />
      )}
    </section>
  );
}

function WeatherBody({ rec }: { rec: WeatherRecord }) {
  const a = assessWeather(rec);
  return (
    <>
      <StaleBanner assessment={a} subject="The weather record" />
      <KV k="source" v={rec.source} mono />
      <KV k="kind" v={rec.kind} />
      <KV k="location" v={`${rec.latitude.toFixed(3)}°N, ${rec.longitude.toFixed(3)}°E`} />
      <TimeStamp iso={rec.valid_time} label="valid_time" />
      {rec.source_run_time && <TimeStamp iso={rec.source_run_time} label="source_run_time" />}
      {rec.kind === 'forecast' && <KV k="forecast lead" v={rec.forecast_lead_h == null ? <span className="missing">not provided</span> : `${rec.forecast_lead_h} h`} />}
      <TimeStamp iso={rec.retrieved_at} label="retrieved_at" />
      <KV
        k="staleness"
        v={
          <>
            <LevelChip assessment={a} /> {a.mode}
          </>
        }
      />
      <KV k="data age" v={`${formatAge(rec.age_minutes)} (${a.ageBasis})`} />
      <KV
        k="fetch age"
        v={rec.fetch_age_minutes === undefined ? <span className="missing">not provided</span> : `${formatAge(rec.fetch_age_minutes)} (now - retrieved_at)${a.fetchLevel ? `, ${a.fetchLevel}` : ''}`}
      />
      <KV k="declared stale_level" v={`${rec.stale_level} (worse of the two ages)`} />
      <KV k="wind" v={<>{fmtNum(rec.wind_speed_kmh, ' km/h')} from {fmtNum(rec.wind_direction_deg, '°', 0)}</>} />
      <KV k="temp" v={fmtNum(rec.temp_c, ' °C')} />
      <KV k="RH" v={fmtNum(rec.rh_pct, ' %', 0)} />
      <KV k="precip" v={fmtNum(rec.precip_mm, ' mm')} />
      {rec.quality_flags && rec.quality_flags.length > 0 && <KV k="quality flags" v={rec.quality_flags.join('; ')} />}
      <Disclaimer />
    </>
  );
}

export interface SpreadRun {
  point: [number, number];
  result: Loaded<SpreadOutput>;
  /** User confirmed running on expired / invalid weather. */
  degradedConfirmed: boolean;
}

export function SpreadPanel({
  run,
  loading,
  horizon,
  onHorizon,
  isMock,
}: {
  run: SpreadRun | null;
  loading: boolean;
  horizon: Horizon;
  onHorizon: (h: Horizon) => void;
  isMock: boolean;
}) {
  return (
    <section className="panel" data-testid="spread-panel">
      <h2>
        Spread run {isMock && <MockBadge />}
      </h2>
      {loading ? (
        <Loading what="spread run" />
      ) : !run ? (
        <p className="muted small">Switch to “Spread” mode and click the map to request a run.</p>
      ) : !run.result.ok ? (
        <ErrorState title={run.result.title} errors={run.result.errors} kind={run.result.kind} />
      ) : (
        <SpreadBody out={run.result.data} degradedConfirmed={run.degradedConfirmed} horizon={horizon} onHorizon={onHorizon} />
      )}
    </section>
  );
}

function SpreadBody({ out, degradedConfirmed, horizon, onHorizon }: { out: SpreadOutput; degradedConfirmed: boolean; horizon: Horizon; onHorizon: (h: Horizon) => void }) {
  const a = assessSpread(out);
  const estimated = out.uncertainty_status === 'estimated';
  const rows = out.features.filter((f) => f.properties.horizon_h === horizon);
  const validated = isHorizonValidated(out, horizon);
  return (
    <>
      <ModeBadge mode={out.mode} />
      <StaleBanner assessment={a} subject="Spread weather inputs" />
      {degradedConfirmed && (
        <div className="banner banner-degraded" data-testid="degraded-note">
          <strong>DEGRADED RUN.</strong> You confirmed running on expired or unavailable weather. Do not treat this as current.
        </div>
      )}
      {!estimated && (
        <div className="banner banner-uncertainty" data-testid="uncertainty-not-estimated">
          <strong>Uncertainty not estimated.</strong> This run has no usable ensemble (n_runs = {out.n_runs}), so p10 / p50 / p90 are not shown as bands. The map draws a
          single-run footprint only.
        </div>
      )}
      <div className="horizon-toggle" role="group" aria-label="Horizon">
        {HORIZONS.map((h) => (
          <button key={h} type="button" className={h === horizon ? 'active' : ''} aria-pressed={h === horizon} onClick={() => onHorizon(h)} data-testid={`horizon-${h}`}>
            {horizonLabel(out, h)}
          </button>
        ))}
      </div>
      {!validated && (
        <p className="small" data-testid="unvalidated-note">
          <UnvalidatedBadge /> {horizon} h has not been validated against observed fire growth
          {out.validated_horizons_h.length === 0 ? ' (no horizon is listed in validated_horizons_h)' : ''}.
        </p>
      )}
      {rows.length === 0 ? (
        <p className="muted small" data-testid="no-horizon-features">No features for this horizon in the payload.</p>
      ) : estimated ? (
        <table className="bands" data-testid="band-table">
          <thead>
            <tr>
              <th>Band</th>
              <th>Level</th>
              <th>Area</th>
              <th>Head dir.</th>
            </tr>
          </thead>
          <tbody>
            {BANDS.map((b) => {
              const f = rows.find((r) => r.properties.band === b);
              if (!f) return (
                <tr key={b}><td>{b}</td><td colSpan={3} className="missing">band missing from payload</td></tr>
              );
              const empty = isEmptyBand(f);
              return (
                <tr key={b}>
                  <td>
                    <span className="swatch" style={{ background: BAND_COLORS[b] }} /> {b}
                  </td>
                  <td>≥{Math.round(f.properties.burn_probability_level * 100)}% of runs</td>
                  <td>{empty ? <em data-testid="empty-band">no area reached this level</em> : `${f.properties.area_ha} ha`}</td>
                  <td>{f.properties.head_direction_deg == null ? '—' : `${f.properties.head_direction_deg}°`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <p className="small" data-testid="single-run-summary">
          Single-run footprint area:{' '}
          {(() => {
            const f = rows.find((r) => r.properties.band === 'p50') ?? rows[0];
            return isEmptyBand(f) ? <em>no area reached</em> : `${f.properties.area_ha} ha`;
          })()}
        </p>
      )}
      <h3>Run details</h3>
      <TimeStamp iso={out.run_timestamp} label="run_timestamp" />
      {out.start_time && <TimeStamp iso={out.start_time} label="start_time" />}
      <KV k="model_version" v={out.model_version} mono />
      <KV k="ignition point" v={`${out.ignition_point.latitude.toFixed(4)}°N, ${out.ignition_point.longitude.toFixed(4)}°E`} />
      <KV k="n_runs / seed" v={`${out.n_runs} / ${out.seed ?? 'not provided'}`} />
      <KV k="uncertainty_status" v={out.uncertainty_status} mono />
      <KV k="grid resolution" v={`${out.grid_resolution_m} m (EPSG:3978)`} />
      <KV k="staleness" v={<><LevelChip assessment={a} /> age {formatAge(a.ageMinutes)} · stale_level {out.stale_level} · stale_flag {String(out.inputs.stale_flag)}</>} />
      <KV k="validated horizons" v={out.validated_horizons_h.length ? out.validated_horizons_h.map((h) => `${h} h`).join(', ') : 'none'} />
      <h3>Inputs</h3>
      <KV k="weather source" v={out.inputs.weather_source} mono />
      <TimeStamp iso={out.inputs.weather_valid_time} label="weather_valid_time" />
      <KV k="wind at start_time" v={`${out.inputs.wind_speed_kmh} km/h from ${out.inputs.wind_direction_deg}°`} />
      <KV k="temp / RH / precip" v={<>{fmtNum(out.inputs.temp_c, ' °C')} / {fmtNum(out.inputs.rh_pct, ' %', 0)} / {fmtNum(out.inputs.precip_mm, ' mm')}</>} />
      <KV k="FFMC / BUI" v={<>{fmtNum(out.inputs.ffmc)} / {fmtNum(out.inputs.bui)}</>} />
      <KV k="fuel type" v={out.inputs.fuel_type ?? <span className="missing">not resolved</span>} />
      <KV k="fuel source" v={out.inputs.fuel_source} mono />
      <h3>Perturbation</h3>
      {out.perturbation.parameters.length === 0 ? (
        <p className="muted small" data-testid="no-perturbation">No parameters were perturbed.</p>
      ) : (
        <ul className="plain small" data-testid="perturbation-list">
          {out.perturbation.parameters.map((p) => (
            <li key={p.name}>
              <code>{p.name}</code>: {p.distribution}
              {p.sigma !== undefined && ` σ=${p.sigma}`}
              {p.low !== undefined && ` low=${p.low}`}
              {p.high !== undefined && ` high=${p.high}`}
              {p.value !== undefined && ` value=${p.value}`}
            </li>
          ))}
        </ul>
      )}
      {out.inputs.weather_series && out.inputs.weather_series.length > 0 && (
        <>
          <h3>Weather series (hourly)</h3>
          <table className="bands small" data-testid="weather-series">
            <thead>
              <tr>
                <th>Valid (Toronto)</th>
                <th>Wind km/h</th>
                <th>From °</th>
              </tr>
            </thead>
            <tbody>
              {out.inputs.weather_series.map((w) => (
                <tr key={w.valid_time} title={formatUtc(w.valid_time)}>
                  <td>{formatToronto(w.valid_time)}</td>
                  <td>{w.wind_speed_kmh}</td>
                  <td>{w.wind_direction_deg}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {isMockVersion(out.model_version) && <p className="muted small">MOCK run: the example footprint translated to the clicked point; not a simulation.</p>}
      <Disclaimer text={out.disclaimer} />
    </>
  );
}

export function Legend({ maxProbability, mode, spreadVisible, estimated }: { maxProbability: number; mode: 'ignition' | 'spread'; spreadVisible: boolean; estimated: boolean }) {
  return (
    <div className="legend" data-testid="legend">
      <div className="legend-title">Ignition probability per 10 km cell per day</div>
      <div className="ramp" style={{ background: `linear-gradient(to right, ${RAMP.join(',')})` }} />
      <div className="ramp-labels">
        <span>0</span>
        <span>{maxProbability > 0 ? formatProbability(maxProbability) : '—'}</span>
      </div>
      <div className="small">probability, no approved danger thresholds. Linear scale from 0 to the maximum in the loaded payload (relative, not a danger level).</div>
      {(spreadVisible || mode === 'spread') && (
        <div className="small spread-legend">
          {estimated ? (
            <>
              Spread bands (burn probability across runs):{' '}
              {BANDS.map((b) => (
                <span key={b} className="legend-band">
                  <span className="swatch" style={{ background: BAND_COLORS[b] }} />
                  {b}
                </span>
              ))}
            </>
          ) : (
            <>
              <span className="swatch" style={{ background: '#9aa0a6' }} /> single-run footprint (uncertainty not estimated)
            </>
          )}
        </div>
      )}
    </div>
  );
}
