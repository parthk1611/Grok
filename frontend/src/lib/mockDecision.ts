import type { ResponseMeta } from '../types';

/**
 * Single MOCK decision for the whole page (banner, footer, panel badges). Safe by default:
 * MOCK if the adapter is a mock, if no response has arrived yet, or if ANY response does not explicitly
 * say it is not mock (X-Mock-Data: false and no envelope `mock: true`). Disagreement => MOCK.
 */
export function decideMock(adapterIsMock: boolean, metas: Array<ResponseMeta | undefined>): boolean {
  const seen = metas.filter((m): m is ResponseMeta => m !== undefined);
  return adapterIsMock || seen.length === 0 || seen.some((m) => m.mock !== false);
}
