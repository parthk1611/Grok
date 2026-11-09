// @vitest-environment jsdom
// App-level flow tests with the map replaced by a stub (MapLibre needs WebGL, which jsdom lacks).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from './App';
import { createApiClient, createMockAdapter, type ApiAdapter } from './api/client';
import type { DevScenario } from './mocks/fixtures';
import { DISCLAIMER } from './lib/contract';

vi.mock('./components/MapView', () => ({
  MapView: (p: { onSpreadClick: (pt: [number, number]) => void }) => (
    <button type="button" data-testid="fake-map-click" onClick={() => p.onSpreadClick([-86, 50])}>
      map
    </button>
  ),
}));

afterEach(cleanup);

function setup(scenario: DevScenario, wrap?: (a: ApiAdapter) => ApiAdapter) {
  const bodies: unknown[] = [];
  render(
    <App
      makeClient={() => {
        const base = createMockAdapter({ getScenario: () => scenario, latencyMs: 0 });
        const a = wrap ? wrap(base) : base;
        return createApiClient({ ...a, runSpread: (b) => (bodies.push(b), a.runSpread(b)) });
      }}
    />,
  );
  return { bodies, click: () => fireEvent.click(screen.getByTestId('fake-map-click')) };
}

describe('expired-weather spread flow (ruling 1)', () => {
  it('expired weather: pre-check opens the dialog; "Run anyway (degraded)" sends confirm_degraded and marks DEGRADED', async () => {
    const { bodies, click } = setup('expired');
    click();
    await screen.findByTestId('degraded-dialog');
    expect(bodies).toHaveLength(0); // nothing sent before the user decides
    fireEvent.click(screen.getByTestId('degraded-run'));
    await screen.findByTestId('degraded-note');
    expect(bodies[0]).toMatchObject({ confirm_degraded: true });
    expect(within(screen.getByTestId('spread-panel')).getByTestId('expired-banner')).toBeInTheDocument();
  });

  it('"Enter wind/moisture by hand": validates, then sends weather_override; result shows the Manual inputs badge above the stale banner', async () => {
    const { bodies, click } = setup('expired');
    click();
    await screen.findByTestId('degraded-dialog');
    fireEvent.click(screen.getByTestId('degraded-manual'));
    fireEvent.change(screen.getByTestId('ov-wind_speed_kmh'), { target: { value: '500' } });
    fireEvent.change(screen.getByTestId('ov-rh_pct'), { target: { value: 'wet' } });
    fireEvent.click(screen.getByTestId('manual-submit'));
    expect(screen.getByTestId('ov-wind_speed_kmh-error')).toHaveTextContent('between 0 and 200 km/h');
    expect(screen.getByTestId('ov-wind_direction_deg-error')).toHaveTextContent('required');
    expect(screen.getByTestId('ov-rh_pct-error')).toHaveTextContent('must be a number');
    expect(bodies).toHaveLength(0);
    fireEvent.change(screen.getByTestId('ov-wind_speed_kmh'), { target: { value: '25' } });
    fireEvent.change(screen.getByTestId('ov-wind_direction_deg'), { target: { value: '270' } });
    fireEvent.change(screen.getByTestId('ov-rh_pct'), { target: { value: '30' } });
    fireEvent.click(screen.getByTestId('manual-submit'));
    const badge = await screen.findByTestId('manual-inputs-badge');
    expect(bodies[0]).toMatchObject({ weather_override: { wind_speed_kmh: 25, wind_direction_deg: 270, rh_pct: 30 } });
    expect(bodies[0]).not.toHaveProperty('confirm_degraded');
    const stale = within(screen.getByTestId('spread-panel')).getByTestId('stale-banner');
    expect(badge.compareDocumentPosition(stale) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // badge first
    expect(stale).toHaveTextContent('manual weather override; not live data');
    expect(screen.queryByTestId('degraded-note')).toBeNull();
  });

  it('a 409 confirmation_required from the API (weather looked fine) opens the same flow; cancel sends nothing more', async () => {
    // Server says expired even though the client pre-check saw fresh weather.
    const { bodies, click } = setup('fresh', (a) => ({
      ...a,
      runSpread: (b) =>
        b.confirm_degraded || b.weather_override
          ? a.runSpread(b)
          : Promise.resolve({
              status: 409,
              body: { status: 409, error: 'expired', reason: 'confirmation_required', last_cache_time: '2026-11-02T12:00:00Z', request_id: 'req-409-abcdef', disclaimer: DISCLAIMER },
            }),
    }));
    click();
    const dlg = await screen.findByTestId('degraded-dialog');
    expect(dlg).toHaveTextContent('confirmation_required');
    expect(dlg).toHaveTextContent('req-409-abcdef');
    expect(dlg).toHaveTextContent('2026-11-02 12:00');
    expect(bodies).toHaveLength(1);
    fireEvent.click(screen.getByTestId('degraded-cancel'));
    expect(screen.queryByTestId('degraded-dialog')).toBeNull();
    expect(bodies).toHaveLength(1);
  });

  it('a 409 after the user already confirmed is shown as an error, not looped', async () => {
    const { click } = setup('expired', (a) => ({
      ...a,
      runSpread: () =>
        Promise.resolve({
          status: 409,
          body: { status: 409, error: 'still expired', reason: 'confirmation_required', last_cache_time: null, request_id: 'req-loop-0001', disclaimer: DISCLAIMER },
        }),
    }));
    click();
    await screen.findByTestId('degraded-dialog');
    fireEvent.click(screen.getByTestId('degraded-run'));
    await waitFor(() => expect(screen.getByTestId('spread-panel')).toHaveTextContent('req-loop-0001'));
    expect(screen.queryByTestId('degraded-dialog')).toBeNull();
  });
});

describe('ignition date (ruling 2)', () => {
  it('stale_day: default request is today; banner shows the records\' own date', async () => {
    setup('stale_day');
    const b = await screen.findByTestId('stale-day-banner');
    expect(b).toHaveTextContent(/Showing \d{4}-\d{2}-\d{2}; requested \d{4}-\d{2}-\d{2} unavailable/);
    expect(screen.getByTestId('ignition-shown-date').textContent).not.toBe(screen.getByTestId('ignition-requested-date').textContent);
  });
  it('switching to "latest map" removes the substitution banner', async () => {
    setup('stale_day');
    await screen.findByTestId('stale-day-banner');
    fireEvent.click(screen.getByTestId('date-latest'));
    await waitFor(() => expect(screen.getByTestId('ignition-requested-date')).toHaveTextContent('latest available'));
    expect(screen.queryByTestId('stale-day-banner')).toBeNull();
  });
  it('no_cache: ignition shows the no_cached_data message with "never cached" and a request id', async () => {
    setup('no_cache');
    await waitFor(() => expect(screen.getByTestId('ignition-panel')).toHaveTextContent('No cached data exists yet'));
    expect(screen.getByTestId('ignition-panel')).toHaveTextContent('never cached (last_cache_time is null)');
    expect(screen.getByTestId('ignition-panel')).toHaveTextContent(/mock-/);
  });
});
