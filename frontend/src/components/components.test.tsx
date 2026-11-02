// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { ErrorState, MockBadge, MockBanner, StaleBanner } from './Banners';
import { ModeBadge } from './ModeBadge';
import { Footer } from './Footer';
import { Disclaimer } from './Meta';
import { CellDetails, IgnitionPanel, SpreadPanel, WeatherPanel, type SpreadRun } from './Panels';
import { assessIgnition } from '../lib/staleness';
import { DISCLAIMER } from '../lib/contract';
import { mockIgnition, mockSpread, mockWeather, SCENARIOS, type Scenario } from '../mocks/fixtures';
import type { IgnitionRecord, SpreadOutput, WeatherRecord } from '../types';

afterEach(cleanup);
const NOW = new Date('2026-11-02T20:00:00Z');
const ign = (s: Scenario) => mockIgnition(s, NOW) as IgnitionRecord[];
const spread = (s: Scenario) => mockSpread(s, NOW, [-86, 50]) as SpreadOutput;
const runOf = (out: SpreadOutput, degraded = false): SpreadRun => ({ point: [-86, 50], result: { ok: true, data: out }, degradedConfirmed: degraded });
const renderSpread = (out: SpreadOutput, horizon: 1 | 3 | 6 = 6, degraded = false) =>
  render(<SpreadPanel run={runOf(out, degraded)} loading={false} horizon={horizon} onHorizon={() => {}} isMock />);

describe('MOCK labelling', () => {
  it('persistent banner and per-panel badges', () => {
    render(<><MockBanner /><IgnitionPanel state={{ ok: true, data: ign('fresh') }} isMock /><WeatherPanel state={null} isMock /></>);
    expect(screen.getByTestId('mock-banner')).toHaveTextContent('MOCK DATA');
    expect(screen.getAllByTestId('mock-badge')).toHaveLength(2);
  });
  it('MockBadge renders text MOCK', () => {
    render(<MockBadge />);
    expect(screen.getByTestId('mock-badge')).toHaveTextContent('MOCK');
  });
});

describe('staleness banners', () => {
  it('stale_flag true => visible stale banner with the reason (ignition panel)', () => {
    render(<IgnitionPanel state={{ ok: true, data: ign('stale') }} isMock />);
    const b = screen.getByTestId('stale-banner');
    expect(b).toHaveTextContent('STALE DATA');
    expect(b).toHaveTextContent('stale_flag is true');
    expect(b).toHaveTextContent('MOCK: synthetic input feed is 120 min old');
  });
  it('fresh => no banner', () => {
    render(<IgnitionPanel state={{ ok: true, data: ign('fresh') }} isMock />);
    expect(screen.queryByTestId('stale-banner')).toBeNull();
    expect(screen.queryByTestId('expired-banner')).toBeNull();
  });
  it('expired => distinct expired banner, not the stale one', () => {
    render(<IgnitionPanel state={{ ok: true, data: ign('expired') }} isMock />);
    expect(screen.getByTestId('expired-banner')).toHaveTextContent('EXPIRED DATA');
    expect(screen.queryByTestId('stale-banner')).toBeNull();
  });
  it('StaleBanner lists proposed thresholds', () => {
    render(<StaleBanner assessment={assessIgnition(ign('stale')[0])} subject="x" />);
    expect(screen.getByTestId('stale-banner')).toHaveTextContent('proposed');
  });
  it('weather panel shows level, age and cache mode; expired weather gets the expired banner', () => {
    const rec = mockWeather('expired', NOW, null) as WeatherRecord;
    render(<WeatherPanel state={{ ok: true, data: rec }} isMock />);
    expect(screen.getByTestId('expired-banner')).toBeInTheDocument();
    expect(screen.getByTestId('weather-panel')).toHaveTextContent('from cache');
  });
  it('weather panel shows data age and fetch age (v0.2.1) when both are present', () => {
    const rec = mockWeather('stale', NOW, null) as WeatherRecord;
    render(<WeatherPanel state={{ ok: true, data: rec }} isMock />);
    const p = screen.getByTestId('weather-panel');
    expect(p).toHaveTextContent('data age');
    expect(p).toHaveTextContent('2.0 h');
    expect(p).toHaveTextContent('fetch age');
    expect(p).toHaveTextContent('45 min');
    expect(screen.getByTestId('stale-banner')).toHaveTextContent('fetch age: 45 min');
  });
  it('fetch age absent is stated as not provided', () => {
    const rec = mockWeather('fresh', NOW, null) as WeatherRecord;
    render(<WeatherPanel state={{ ok: true, data: rec }} isMock />);
    expect(within(screen.getByTestId('weather-panel')).getByText('not provided')).toBeInTheDocument();
  });
  it('forecast weather shows source_run_time and lead', () => {
    const rec = mockWeather('rich', NOW, null) as WeatherRecord;
    render(<WeatherPanel state={{ ok: true, data: rec }} isMock />);
    expect(screen.getByTestId('ts-source_run_time')).toHaveTextContent('(Toronto)');
    expect(screen.getByTestId('weather-panel')).toHaveTextContent('6 h');
  });
  it('weather nulls are shown as missing, not as 0', () => {
    const rec = mockWeather('sparse', NOW, null) as WeatherRecord;
    render(<WeatherPanel state={{ ok: true, data: rec }} isMock />);
    expect(within(screen.getByTestId('weather-panel')).getAllByText('missing').length).toBeGreaterThanOrEqual(4);
  });
  it('spread panel: stale and expired banners from stale_level', () => {
    renderSpread(spread('stale'));
    expect(screen.getByTestId('stale-banner')).toBeInTheDocument();
    cleanup();
    renderSpread(spread('expired'), 6, true);
    expect(screen.getByTestId('expired-banner')).toBeInTheDocument();
    expect(screen.getByTestId('degraded-note')).toBeInTheDocument();
  });
});

describe('mode badge', () => {
  it.each([
    ['retrospective', /RETROSPECTIVE - not a forecast/],
    ['hindcast', /HINDCAST - not a forecast/],
    ['forecast_like', /not a live forecast/],
  ] as const)('%s is labelled as not a forecast', (mode, re) => {
    render(<ModeBadge mode={mode} />);
    expect(screen.getByTestId('mode-badge')).toHaveTextContent(re);
  });
  it('forecast is plain FORECAST', () => {
    render(<ModeBadge mode="forecast" />);
    expect(screen.getByTestId('mode-badge')).toHaveTextContent(/^FORECAST$/);
  });
  it('spread panel shows the payload mode', () => {
    renderSpread(spread('fresh'));
    expect(screen.getByTestId('mode-badge')).toHaveAttribute('data-mode', 'hindcast');
  });
});

describe('error states', () => {
  it('invalid payload renders an explicit error and no data', () => {
    render(<IgnitionPanel state={{ ok: false, kind: 'invalid', title: 'Ignition risk: payload failed contract validation', errors: ['[1] /probability: must be <= 1'] }} isMock />);
    expect(screen.getByTestId('error-state')).toHaveTextContent('failed contract validation');
    expect(screen.getByTestId('error-state')).toHaveTextContent('must be <= 1');
    expect(screen.queryByTestId('mode-badge')).toBeNull();
  });
  it('ErrorState distinguishes unavailable', () => {
    render(<ErrorState title="t" errors={['boom']} kind="unavailable" />);
    expect(screen.getByTestId('error-state')).toHaveTextContent('No data could be loaded');
  });
});

describe('spread panel (contract v0.2.0)', () => {
  it('estimated: band table with p10/p50/p90 and unvalidated labels on 1 h and 3 h', () => {
    renderSpread(spread('fresh'), 1);
    expect(screen.getByTestId('band-table')).toHaveTextContent('p10');
    expect(screen.getByTestId('horizon-1')).toHaveTextContent('1 h (unvalidated)');
    expect(screen.getByTestId('horizon-3')).toHaveTextContent('3 h (unvalidated)');
    expect(screen.getByTestId('horizon-6')).toHaveTextContent(/^6 h$/);
    expect(screen.getByTestId('unvalidated-note')).toBeInTheDocument();
  });
  it('validated horizon 6 h has no unvalidated note', () => {
    renderSpread(spread('fresh'), 6);
    expect(screen.queryByTestId('unvalidated-note')).toBeNull();
  });
  it('empty validated_horizons_h => every horizon unvalidated', () => {
    const out = { ...spread('fresh'), validated_horizons_h: [] };
    renderSpread(out, 6);
    for (const h of [1, 3, 6]) expect(screen.getByTestId(`horizon-${h}`)).toHaveTextContent('unvalidated');
    expect(screen.getByTestId('unvalidated-note')).toHaveTextContent('no horizon is listed');
  });
  it('not_estimated: says so, and does NOT render a p10/p50/p90 band table', () => {
    renderSpread(spread('sparse'));
    expect(screen.getByTestId('uncertainty-not-estimated')).toHaveTextContent('Uncertainty not estimated');
    expect(screen.queryByTestId('band-table')).toBeNull();
    expect(screen.getByTestId('single-run-summary')).toBeInTheDocument();
  });
  it('empty band shows "no area reached this level"', () => {
    renderSpread(spread('rich'), 1);
    expect(screen.getByTestId('empty-band')).toHaveTextContent('no area reached this level');
  });
  it('run details: perturbation, fuel source, grid resolution, weather series, wind-at-start label', () => {
    renderSpread(spread('rich'));
    const p = screen.getByTestId('spread-panel');
    expect(p).toHaveTextContent('ros_scale');
    expect(p).toHaveTextContent('example-not-a-real-fuel-grid');
    expect(p).toHaveTextContent('90 m');
    expect(p).toHaveTextContent('wind at start_time');
    expect(screen.getByTestId('weather-series').querySelectorAll('tbody tr')).toHaveLength(7);
  });
  it('no perturbation parameters is stated explicitly', () => {
    renderSpread(spread('sparse'));
    expect(screen.getByTestId('no-perturbation')).toBeInTheDocument();
  });
  it('shows timestamps in UTC and Toronto', () => {
    renderSpread(spread('fresh'));
    const ts = screen.getByTestId('ts-run_timestamp');
    expect(ts).toHaveTextContent('2026-11-02 20:00 UTC');
    expect(ts).toHaveTextContent('2026-11-02 15:00 EST (Toronto)');
  });
});

describe('cell details (ignition v0.2.0 optional fields)', () => {
  it('shows calibration, feature_set_version, rank when present', () => {
    render(<CellDetails record={ign('rich')[0]} onClose={() => {}} />);
    const d = screen.getByTestId('cell-details');
    expect(d).toHaveTextContent('synthetic-calibration-label');
    expect(d).toHaveTextContent('synthetic-feature-set-v0');
    expect(d).toHaveTextContent('probability (raw)');
    expect(d).toHaveTextContent('no danger category');
  });
  it('says "not provided" when absent', () => {
    render(<CellDetails record={ign('fresh')[0]} onClose={() => {}} />);
    expect(within(screen.getByTestId('cell-details')).getAllByText('not provided').length).toBeGreaterThanOrEqual(3);
  });
  it('shows timestamps, model version and the disclaimer', () => {
    render(<CellDetails record={ign('fresh')[0]} onClose={() => {}} />);
    const d = screen.getByTestId('cell-details');
    expect(d).toHaveTextContent('(Toronto)');
    expect(d).toHaveTextContent('example-not-a-real-model');
    expect(screen.getByTestId('disclaimer')).toHaveTextContent(DISCLAIMER);
  });
});

describe('disclaimer', () => {
  it('footer always shows the exact contract string', () => {
    render(<Footer />);
    expect(screen.getByTestId('footer')).toHaveTextContent(DISCLAIMER);
  });
  it('falls back to the contract constant when the payload has none', () => {
    render(<Disclaimer text={undefined} />);
    expect(screen.getByTestId('disclaimer')).toHaveTextContent(DISCLAIMER);
  });
  it('every non-invalid ignition fixture carries the exact disclaimer', () => {
    for (const s of SCENARIOS.filter((x) => x !== 'invalid')) for (const r of ign(s)) expect(r.disclaimer).toBe(DISCLAIMER);
  });
});
