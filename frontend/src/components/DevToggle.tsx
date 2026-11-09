import { DEV_SCENARIOS, SCENARIO_HELP, type DevScenario } from '../mocks/fixtures';

export function DevToggle({ scenario, onChange }: { scenario: DevScenario; onChange: (s: DevScenario) => void }) {
  return (
    <div className="devtoggle" data-testid="dev-toggle">
      <label>
        <span className="badge badge-dev">DEV</span> Fixture:{' '}
        <select value={scenario} onChange={(e) => onChange(e.target.value as DevScenario)} aria-label="Fixture scenario">
          {DEV_SCENARIOS.map((s) => (
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
