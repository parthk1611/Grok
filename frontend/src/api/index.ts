// Adapter selection from build/dev-time env (see README):
//   VITE_API_ADAPTER   mock (default) | relay
//   VITE_API_BASE_URL  e.g. http://127.0.0.1:8765 (required for relay)
//   VITE_API_ENVELOPE  true to request ?envelope=true for ignition (the client accepts both shapes regardless)
import { createApiClient, type ApiClient } from './client';
import { createMockAdapter } from '../mocks/mockServer';
import { createRelayAdapter } from './relayAdapter';
import type { DevScenario } from '../mocks/fixtures';

export interface ApiConfig {
  adapter: 'mock' | 'relay';
  baseUrl: string | null;
  envelope: boolean;
  /** Set when the env asked for something unusable; the app falls back to MOCK and says why. */
  configError: string | null;
}

export function readApiConfig(env: Record<string, string | undefined> = import.meta.env as Record<string, string | undefined>): ApiConfig {
  const kind = (env.VITE_API_ADAPTER ?? 'mock').trim().toLowerCase();
  const baseUrl = (env.VITE_API_BASE_URL ?? '').trim() || null;
  const envelope = (env.VITE_API_ENVELOPE ?? '').trim().toLowerCase() === 'true';
  if (kind === 'mock') return { adapter: 'mock', baseUrl, envelope, configError: null };
  if (kind !== 'relay') return { adapter: 'mock', baseUrl, envelope, configError: `VITE_API_ADAPTER="${kind}" is not mock|relay; using MOCK` };
  if (!baseUrl || !/^https?:\/\//.test(baseUrl)) {
    return { adapter: 'mock', baseUrl, envelope, configError: 'VITE_API_ADAPTER=relay needs VITE_API_BASE_URL (http[s]://host:port); using MOCK' };
  }
  return { adapter: 'relay', baseUrl, envelope, configError: null };
}

export function createConfiguredClient(cfg: ApiConfig, getScenario: () => DevScenario): ApiClient {
  if (cfg.adapter === 'relay' && cfg.baseUrl) return createApiClient(createRelayAdapter({ baseUrl: cfg.baseUrl }), { envelope: cfg.envelope });
  return createApiClient(createMockAdapter({ getScenario }), { envelope: cfg.envelope });
}
