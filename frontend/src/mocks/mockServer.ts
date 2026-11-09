// MOCK adapter: emulates the RELAY API's status codes and error bodies in the browser, using the MOCK
// fixtures in this folder. Nothing here is real data. Behaviour follows the Team Lead rulings of 2026-11-09:
//  - GET /ignition?date=D|latest: the newest map at or before D (records keep their own date, stale_flag true
//    when it is not D); 404 not_found only when nothing exists at or before D. The mock holds ONE map.
//  - POST /spread: expired weather + neither confirm_degraded nor weather_override => 409 confirmation_required.
//  - no_cache scenario: 503 NO_CACHE_REASON with last_cache_time null everywhere (nothing was ever cached),
//    including spread with confirm_degraded; a spread with weather_override still runs (200, manual, stale).
//  - every error body is exactly {status, error, reason, last_cache_time, request_id, disclaimer} (closed shape).
import type { AdapterResponse, ApiAdapter } from '../api/client';
import type { LonLat } from '../geo/cell';
import { DISCLAIMER } from '../lib/contract';
import { NO_CACHE_REASON } from '../lib/apiError';
import type { SpreadRequestBody, WeatherRecord } from '../types';
import { mockIgnition, mockLatestMapDate, mockSpread, mockWeather, type DevScenario, type Scenario } from './fixtures';

export interface MockAdapterOptions {
  getScenario: () => DevScenario;
  getNow?: () => Date;
  latencyMs?: number;
}

const MOCK_HEADERS = { 'x-mock-data': 'true', 'x-data-mode': 'mock' };
let seq = 0;
const requestId = () => `mock-${Date.now().toString(36)}-${(seq++).toString(36).padStart(4, '0')}`;

function errorResponse(status: number, reason: string, error: string, lastCacheTime: string | null): AdapterResponse {
  const rid = requestId();
  return {
    status,
    body: { status, error: `MOCK: ${error}`, reason, last_cache_time: lastCacheTime, request_id: rid, disclaimer: DISCLAIMER },
    headers: { ...MOCK_HEADERS, 'x-request-id': rid },
  };
}
const okResponse = (body: unknown): AdapterResponse => ({ status: 200, body, headers: { ...MOCK_HEADERS, 'x-request-id': requestId() } });

export function createMockAdapter(opts: MockAdapterOptions): ApiAdapter {
  const now = opts.getNow ?? (() => new Date());
  const delay = () => new Promise<void>((r) => setTimeout(r, opts.latencyMs ?? 150));
  const payloadScenario = (s: DevScenario): Scenario | null => (s === 'no_cache' ? null : s);

  return {
    name: 'MOCK adapter (contracts/examples + synthetic fixtures, emulated API statuses)',
    isMock: true,

    async getIgnition({ date, envelope }) {
      await delay();
      const sc = payloadScenario(opts.getScenario());
      if (!sc) return errorResponse(503, NO_CACHE_REASON, 'No cached data exists yet; no successful ingestion has completed.', null);
      const t = now();
      const latest = mockLatestMapDate(sc, t);
      if (date !== 'latest' && date < latest) {
        return errorResponse(404, 'not_found', `No ignition map exists at or before ${date} (the mock holds only ${latest}).`, null);
      }
      const records = mockIgnition(sc, t, latest, date === 'latest' ? undefined : date);
      if (!envelope || !Array.isArray(records) || sc === 'invalid') return okResponse(records);
      const recs = records as Array<{ mode: string; stale_level: string; age_minutes: number; data_timestamp: string }>;
      const order = ['fresh', 'stale', 'expired'];
      const worst = recs.reduce((a, r) => (order.indexOf(r.stale_level) > order.indexOf(a.stale_level) ? r : a), recs[0]);
      return okResponse({
        mode: recs[0].mode,
        stale_level: worst.stale_level,
        age_minutes: Math.max(...recs.map((r) => r.age_minutes)),
        data_timestamp: recs.map((r) => r.data_timestamp).sort().at(-1),
        requested_date: date,
        mock: true,
        records,
      });
    },

    async getWeather(point: LonLat | null) {
      await delay();
      const sc = payloadScenario(opts.getScenario());
      if (!sc) return errorResponse(503, NO_CACHE_REASON, 'No weather has ever been cached for this point.', null);
      return okResponse(mockWeather(sc, now(), point));
    },

    async runSpread(body: SpreadRequestBody) {
      await delay();
      const sc = payloadScenario(opts.getScenario());
      const point: LonLat = [body.longitude, body.latitude];
      if (!sc) {
        if (body.weather_override) return okResponse(mockSpread('fresh', now(), point, body.weather_override));
        return errorResponse(503, NO_CACHE_REASON, 'No weather has ever been cached; enter wind and moisture by hand to run.', null);
      }
      if (sc === 'expired' && !body.confirm_degraded && !body.weather_override) {
        const wx = mockWeather(sc, now(), point) as WeatherRecord;
        return errorResponse(
          409,
          'confirmation_required',
          'The live weather for this point is expired; send confirm_degraded or weather_override.',
          wx.retrieved_at,
        );
      }
      return okResponse(mockSpread(sc, now(), point, body.weather_override));
    },
  };
}
