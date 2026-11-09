// Smoke test of the dashboard's API client + RELAY HTTP adapter against a running RELAY server.
// NOT part of `npm test`. Run:  RELAY_SMOKE_URL=http://127.0.0.1:8765 RELAY_SMOKE_SCENARIO=expired npm run smoke:relay
// It never patches anything: every deviation is printed as a FINDING (JSON report on stdout and in
// $RELAY_SMOKE_OUT if set). The only hard failure is an unreachable server.
import { writeFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createApiClient } from '../src/api/client';
import { createRelayAdapter } from '../src/api/relayAdapter';
import { torontoDate } from '../src/lib/time';
import { NO_CACHE_REASON } from '../src/lib/apiError';
import type { Loaded } from '../src/types';

const URL_ = process.env.RELAY_SMOKE_URL ?? '';
const SCENARIO = process.env.RELAY_SMOKE_SCENARIO ?? 'unknown';
const POINT: [number, number] = [-86, 50];

function summarize<T>(r: Loaded<T>, pick?: (d: T) => unknown) {
  return r.ok
    ? { ok: true, mock: r.meta?.mock, warnings: r.meta?.warnings ?? [], data: pick ? pick(r.data) : undefined }
    : { ok: false, kind: r.kind, http: r.httpStatus, reason: r.apiError?.reason, last_cache_time: r.apiError?.last_cache_time, request_id: r.apiError?.request_id, title: r.title, errors: r.errors };
}

describe.skipIf(!URL_)(`RELAY smoke (${SCENARIO})`, () => {
  it('runs every dashboard call and reports findings', async () => {
    const health = await fetch(`${URL_}/healthz`).then((r) => r.json() as Promise<Record<string, unknown>>);
    expect(health.status).toBe('ok');
    const report: Record<string, unknown> = { scenario: SCENARIO, health };
    const findings: string[] = [];
    for (const envelope of [false, true]) {
      const c = createApiClient(createRelayAdapter({ baseUrl: URL_ }), { envelope });
      const today = torontoDate(new Date());
      const tag = envelope ? 'envelope' : 'array';
      const ig = (d: string) =>
        c.loadIgnition(d).then((r) =>
          summarize(r, (x) => ({ shown: x.dates.shownDates, banner: x.dates.banner, n: x.records.length, stale_flags: [...new Set(x.records.map((q) => q.stale_flag))], envelope: x.envelope })),
        );
      report[`ignition_today_${tag}`] = await ig(today);
      report[`ignition_latest_${tag}`] = await ig('latest');
      report[`ignition_old_${tag}`] = await ig('2000-01-01');
    }
    const c = createApiClient(createRelayAdapter({ baseUrl: URL_ }));
    report.weather = summarize(await c.loadWeather(POINT), (w) => ({ stale_level: w.stale_level, age: w.age_minutes, kind: w.kind }));
    const pick = (o: { stale_level: string; uncertainty_status: string; inputs: { weather_source: string; stale_flag: boolean; stale_reason?: string | null; wind_speed_kmh: number } }) => ({
      stale_level: o.stale_level,
      uncertainty_status: o.uncertainty_status,
      weather_source: o.inputs.weather_source,
      stale_flag: o.inputs.stale_flag,
      stale_reason: o.inputs.stale_reason,
      wind: o.inputs.wind_speed_kmh,
    });
    report.spread_plain = summarize(await c.runSpread(POINT), pick);
    report.spread_confirm = summarize(await c.runSpread(POINT, { confirmDegraded: true }), pick);
    report.spread_manual = summarize(await c.runSpread(POINT, { override: { wind_speed_kmh: 22, wind_direction_deg: 225, ffmc: 88, rh_pct: 35 } }), pick);
    report.spread_out_of_domain = summarize(await c.runSpread([0, 0]));
    // Collect findings: client-side warnings and error-model problems are already mismatches.
    for (const [k, v] of Object.entries(report)) {
      const s = v as { warnings?: string[]; errors?: string[]; kind?: string; reason?: string; last_cache_time?: string | null };
      for (const w of s?.warnings ?? []) findings.push(`${k}: ${w}`);
      if (s?.kind === 'api_error') for (const e of s.errors ?? []) findings.push(`${k}: ${e}`);
      if (s?.kind === 'invalid') findings.push(`${k}: payload failed contract validation: ${(s.errors ?? []).join('; ')}`);
      if (s?.kind === 'api_error' && s.reason !== NO_CACHE_REASON && s.reason !== 'not_found' && s.reason !== 'invalid_request' && s.reason !== 'out_of_domain' && s.last_cache_time === null && SCENARIO !== 'no_cache') {
        findings.push(`${k}: ${s.reason} has last_cache_time null although scenario "${SCENARIO}" has a cache (ruling: null only when never cached)`);
      }
    }
    report.findings = findings;
    const out = JSON.stringify(report, null, 2);
    console.log(out);
    if (process.env.RELAY_SMOKE_OUT) writeFileSync(process.env.RELAY_SMOKE_OUT, out);
  }, 120_000);
});
