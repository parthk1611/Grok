import type { OutputMode } from '../types';

const TEXT: Record<OutputMode, string> = {
  retrospective: 'RETROSPECTIVE - not a forecast',
  hindcast: 'HINDCAST - not a forecast',
  forecast_like: 'FORECAST-LIKE REPLAY - not a live forecast',
  forecast: 'FORECAST',
};

const HELP: Record<OutputMode, string> = {
  retrospective: 'Scored on past data.',
  hindcast: 'Replay using observed weather; not a prediction made in advance.',
  forecast_like: 'Replay using only forecast inputs that were available at the start time.',
  forecast: 'Live forecast.',
};

/** Prominent mode label. Everything except `forecast` is shown as "not a forecast". */
export function ModeBadge({ mode }: { mode: OutputMode }) {
  const notForecast = mode !== 'forecast';
  return (
    <span className={`modebadge ${notForecast ? 'modebadge-notforecast' : 'modebadge-forecast'}`} title={HELP[mode]} data-testid="mode-badge" data-mode={mode}>
      {TEXT[mode]}
    </span>
  );
}

export const modeText = (m: OutputMode) => TEXT[m];
