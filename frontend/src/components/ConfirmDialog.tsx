export function ConfirmDialog({ title, body, confirmLabel, onConfirm, onCancel }: { title: string; body: string[]; confirmLabel: string; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div className="modal-backdrop" data-testid="confirm-dialog">
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
        <h3 id="confirm-title">{title}</h3>
        {body.map((b, i) => (
          <p key={i}>{b}</p>
        ))}
        <div className="modal-actions">
          <button type="button" onClick={onCancel} data-testid="confirm-cancel">
            Cancel
          </button>
          <button type="button" className="danger" onClick={onConfirm} data-testid="confirm-ok">
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
