import { CONTRACT_VERSION, DISCLAIMER } from '../lib/contract';

/** `isMock` comes from decideMock() (same decision as the page banner). Defaults to MOCK: safe when unset. */
export function Footer({ isMock = true }: { isMock?: boolean }) {
  return (
    <footer className="footer" data-testid="footer">
      <strong>{DISCLAIMER}</strong>
      <span className="muted">
        {' '}
        · Contract v{CONTRACT_VERSION} (draft) · COMP-385 dashboard shell ·{' '}
        <span data-testid="footer-data-label">{isMock ? 'MOCK DATA' : 'responses report X-Mock-Data: false'}</span>
      </span>
    </footer>
  );
}
