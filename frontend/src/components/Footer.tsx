import { CONTRACT_VERSION, DISCLAIMER } from '../lib/contract';

export function Footer() {
  return (
    <footer className="footer" data-testid="footer">
      <strong>{DISCLAIMER}</strong>
      <span className="muted"> · Contract v{CONTRACT_VERSION} (draft) · COMP-385 dashboard shell · MOCK DATA</span>
    </footer>
  );
}
