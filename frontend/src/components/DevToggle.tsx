import { SCENARIOS, SCENARIO_HELP, type Scenario } from '../mocks/fixtures';

export function DevToggle({ scenario, onChange }: { scenario: Scenario; onChange: (s: Scenario) => void }) {
  return (
    <div className="devtoggle" data-testid="dev-toggle">
      <label>
        <span className="badge badge-dev">DEV</span> Fixture:{' '}
        <select value={scenario} onChange={(e) => onChange(e.target.value as Scenario)} aria-label="Fixture scenario">
          {SCENARIOS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <span className="muted small">{SCENARIO_HELP[scenario]}</span>
    </div>
  );
}
