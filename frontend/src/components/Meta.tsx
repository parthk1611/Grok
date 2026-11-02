import type { ReactNode } from 'react';
import { DISCLAIMER } from '../lib/contract';
import { formatToronto, formatUtc } from '../lib/time';

/** A timestamp shown in UTC and America/Toronto (DST-aware). */
export function TimeStamp({ iso, label }: { iso: string; label: string }) {
  return (
    <div className="kv" data-testid={`ts-${label}`}>
      <span className="k">{label}</span>
      <span className="v">
        {formatUtc(iso)}
        <br />
        <span className="muted">{formatToronto(iso)} (Toronto)</span>
      </span>
    </div>
  );
}

export function KV({ k, v, mono }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="kv">
      <span className="k">{k}</span>
      <span className={`v ${mono ? 'mono' : ''}`}>{v}</span>
    </div>
  );
}

/** The disclaimer from the payload; falls back to the contract constant when absent. */
export function Disclaimer({ text }: { text?: string | null }) {
  return (
    <p className="disclaimer" data-testid="disclaimer">
      {text && text.length > 0 ? text : DISCLAIMER}
    </p>
  );
}

export const fmtNum = (n: number | null | undefined, unit = '', digits = 1) =>
  n === null || n === undefined ? <span className="missing">missing</span> : `${Number.isInteger(n) ? n : n.toFixed(digits)}${unit}`;
