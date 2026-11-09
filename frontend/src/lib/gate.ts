import type { Loaded, WeatherRecord } from '../types';
import { assessWeather } from './staleness';

/**
 * Reasons a spread run needs explicit user confirmation first (cache_fallback.md v0.2.0:
 * "An expired spread input requires a UI confirmation step before a run").
 * Empty array => run immediately. An unusable (invalid / unavailable) weather record also
 * needs confirmation: we never silently run on weather we could not validate.
 */
export function spreadConfirmationReasons(weather: Loaded<WeatherRecord>): string[] {
  if (!weather.ok) return [`The weather record could not be used (${weather.title}).`];
  const a = assessWeather(weather.data);
  if (a.level === 'expired') {
    return [`The weather record is EXPIRED (age ${Math.round(weather.data.age_minutes)} min, stale_level ${weather.data.stale_level}).`];
  }
  return [];
}
