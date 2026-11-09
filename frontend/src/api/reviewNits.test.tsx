// @vitest-environment jsdom
// RELAY review nits on PR #6 (2026-11-09): 504 spread_timeout mapping, client-side timeout message,
// footer MOCK decision, provisional v0.3.0 envelope fields (mock, stale_sources).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { createApiClient } from './client';
import { createRelayAdapter } from './relayAdapter';
import { ErrorState, StaleBanner } from '../components/Banners';
import { Footer } from '../components/Footer';
import { IgnitionPanel } from '../components/Panels';
import { DISCLAIMER } from '../lib/contract';
import { decideMock } from '../lib/mockDecision';
import { parseIgnitionPayload } from '../lib/envelope';
import { assessIgnition } from '../lib/staleness';
import { checkIgnitionDates } from '../lib/ignitionDate';
import type { IgnitionRecord } from '../types';
import ignitionExample from '../../../contracts/examples/ignition_output.example.json';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });

describe('504 spread_timeout through the RELAY adapter', () => {
  it('maps to the timeout reason, not a generic failure, and renders it', async () => {
    const body = { status: 504, error: 'The spread run did not finish within 60 s; nothing is served.', reason: 'spread_timeout', last_cache_time: '2026-11-09T21:00:00Z', request_id: 'req-504-abcdef', disclaimer: DISCLAIMER };
    const fetchImpl = (() => Promise.resolve(json(504, body, { 'X-Request-ID': 'req-504-abcdef' }))) as unknown as typeof fetch;
    const r = await createApiClient(createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl })).runSpread([-86, 50]);
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.kind).toBe('api_error');
    expect(r.httpStatus).toBe(504);
    expect(r.apiError?.reason).toBe('spread_timeout');
    expect(r.errors).toEqual([]);
    expect(r.title).toMatch(/did not finish within the server time limit/);
    expect(r.title).not.toMatch(/request failed/);
    render(<ErrorState {...r} />);
    expect(screen.getByTestId('error-state')).toHaveAttribute('data-reason', 'spread_timeout');
    expect(screen.getByTestId('api-error-reason')).toHaveTextContent('spread_timeout');
    expect(screen.getByTestId('api-error-request-id')).toHaveTextContent('req-504-abcdef');
  });
});

describe('client-side 70 s abort', () => {
  const hangingFetch = (() =>
    ((_url: string, init?: RequestInit) =>
      new Promise<Response>((_res, rej) => {
        init?.signal?.addEventListener('abort', () => rej(new DOMException('The operation was aborted.', 'AbortError')));
      })) as unknown as typeof fetch)();

  it('says "request timed out after 70 s", not "request failed"', async () => {
    vi.useFakeTimers();
    const p = createApiClient(createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl: hangingFetch })).runSpread([-86, 50]);
    await vi.advanceTimersByTimeAsync(69_999);
    let settled = false;
    void p.then(() => (settled = true));
    await Promise.resolve();
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const r = await p;
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.kind).toBe('unavailable');
    expect(r.title).toBe('Spread run: request timed out after 70 s');
    render(<ErrorState {...r} />);
    expect(screen.getByTestId('error-state')).toHaveTextContent('request timed out after 70 s');
  });
  it('a plain network error still reads "request failed"', async () => {
    const fetchImpl = (() => Promise.reject(new TypeError('fetch failed'))) as unknown as typeof fetch;
    const r = await createApiClient(createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl })).loadWeather([-86, 50]);
    expect(!r.ok && r.title).toBe('Live weather: request failed');
  });
});

describe('footer follows the page MOCK decision', () => {
  it('decideMock: safe by default, disagreement => MOCK, only all-false => not mock', () => {
    expect(decideMock(true, [{ warnings: [], mock: false }])).toBe(true);
    expect(decideMock(false, [])).toBe(true);
    expect(decideMock(false, [undefined, undefined])).toBe(true);
    expect(decideMock(false, [{ warnings: [] }])).toBe(true);
    expect(decideMock(false, [{ warnings: [], mock: false }, { warnings: [], mock: true }])).toBe(true);
    expect(decideMock(false, [{ warnings: [], mock: false }, undefined, { warnings: [], mock: false }])).toBe(false);
  });
  it('footer says MOCK DATA by default and when isMock', () => {
    render(<Footer />);
    expect(screen.getByTestId('footer-data-label')).toHaveTextContent('MOCK DATA');
    cleanup();
    render(<Footer isMock />);
    expect(screen.getByTestId('footer-data-label')).toHaveTextContent('MOCK DATA');
  });
  it('footer drops MOCK DATA only when isMock is false', () => {
    render(<Footer isMock={false} />);
    expect(screen.getByTestId('footer')).not.toHaveTextContent('MOCK DATA');
    expect(screen.getByTestId('footer-data-label')).toHaveTextContent('X-Mock-Data: false');
    expect(screen.getByTestId('footer')).toHaveTextContent(DISCLAIMER);
  });
});

describe('PROVISIONAL v0.3.0 envelope fields (mock, stale_sources)', () => {
  const rec = { ...ignitionExample, date: '2026-07-15', stale_flag: true, stale_level: 'stale', stale_reason: 'x' };
  const env = (extra: Record<string, unknown>) => ({
    mode: 'retrospective', stale_level: 'stale', age_minutes: 120, data_timestamp: '2026-07-14T20:00:00Z', requested_date: '2026-07-15', records: [rec], ...extra,
  });
  const sources = [
    { source: 'hrdps', stale_level: 'stale', age_minutes: 120, fetch_age_minutes: 5 },
    { source: 'swob_realtime', stale_level: 'fresh', age_minutes: 10 },
  ];

  it('accepts valid stale_sources and mock', () => {
    const r = parseIgnitionPayload(env({ mock: true, stale_sources: sources }));
    expect(r.valid).toBe(true);
    if (r.valid) {
      expect(r.data.envelope?.stale_sources).toEqual(sources);
      expect(r.data.envelope?.mock).toBe(true);
    }
  });
  it.each([
    ['not an array', { stale_sources: { hrdps: 'stale' } }, /stale_sources: must be an array/],
    ['unknown feed', { stale_sources: [{ source: 'goes', stale_level: 'stale', age_minutes: 1 }] }, /source: must be one of/],
    ['bad level', { stale_sources: [{ source: 'hrdps', stale_level: 'old', age_minutes: 1 }] }, /stale_level: must be one of/],
    ['negative age', { stale_sources: [{ source: 'hrdps', stale_level: 'stale', age_minutes: -1 }] }, /age_minutes: must be a number >= 0/],
    ['missing age', { stale_sources: [{ source: 'hrdps', stale_level: 'stale' }] }, /age_minutes: must be a number >= 0/],
    ['bad fetch age', { stale_sources: [{ source: 'hrdps', stale_level: 'stale', age_minutes: 1, fetch_age_minutes: 'x' }] }, /fetch_age_minutes/],
    ['extra item key', { stale_sources: [{ source: 'hrdps', stale_level: 'stale', age_minutes: 1, note: 'x' }] }, /unexpected field "note"/],
    ['mock not boolean', { mock: 'yes' }, /mock: must be a boolean/],
    ['other unknown envelope key', { retryable: true }, /unexpected field "retryable"/],
  ])('rejects %s', (_n, extra, re) => {
    const r = parseIgnitionPayload(env(extra));
    expect(r.valid).toBe(false);
    if (!r.valid) expect(r.errors.join(' | ')).toMatch(re);
  });
  it('envelope mock: true wins over X-Mock-Data: false (and the disagreement is listed)', async () => {
    const fetchImpl = (() => Promise.resolve(json(200, env({ mock: true }), { 'X-Mock-Data': 'false' }))) as unknown as typeof fetch;
    const r = await createApiClient(createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl }), { envelope: true }).loadIgnition('2026-07-15');
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.meta?.mock).toBe(true);
    expect(r.meta?.warnings.join(' ')).toMatch(/disagrees with X-Mock-Data/);
    expect(decideMock(false, [r.meta])).toBe(true);
  });
  it('a stale_sources entry worse than the envelope stale_level is listed as a deviation', async () => {
    const fetchImpl = (() => Promise.resolve(json(200, env({ stale_sources: [{ source: 'hrdps', stale_level: 'expired', age_minutes: 900 }] })))) as unknown as typeof fetch;
    const r = await createApiClient(createRelayAdapter({ baseUrl: 'http://h:1', fetchImpl })).loadIgnition('2026-07-15');
    expect(r.ok && r.meta?.warnings.join(' ')).toMatch(/hrdps is "expired" but envelope stale_level is "stale"/);
  });
  it('stale banner lists each feed from stale_sources', () => {
    const p = parseIgnitionPayload(env({ stale_sources: sources }));
    if (!p.valid) throw new Error('fixture invalid');
    const records = p.data.records as IgnitionRecord[];
    render(<IgnitionPanel state={{ ok: true, data: { records, envelope: p.data.envelope, requested: '2026-07-15', dates: checkIgnitionDates(records, '2026-07-15') } }} isMock />);
    const banner = screen.getByTestId('stale-banner');
    expect(banner).toHaveTextContent('provisional v0.3.0');
    expect(screen.getByTestId('stale-source-hrdps')).toHaveTextContent('hrdps: stale, data age 2.0 h, fetch age 5 min');
    expect(screen.getByTestId('stale-source-swob_realtime')).toHaveTextContent('swob_realtime: fresh');
  });
  it('no stale_sources => no per-feed list', () => {
    render(<StaleBanner assessment={assessIgnition(rec as IgnitionRecord)} subject="x" />);
    expect(screen.queryByTestId('stale-sources')).toBeNull();
  });
});
