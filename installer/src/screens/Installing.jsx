import { useState } from 'react';
import StepRow from '../components/StepRow.jsx';
import { cancelAction } from '../backend.js';

export default function Installing({ stepList, logLines, onCancel }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const handleCancel = async () => {
    setCancelling(true);
    try {
      await cancelAction();
      onCancel?.();
    } catch {
      setCancelling(false);
    }
  };

  return (
    <div className="screen-content">
      <h1>Installing</h1>
      <p className="lead">This may take several minutes. You can leave this window open.</p>
      <ol className="step-list">
        {stepList.map((step) => (
          <StepRow key={step.id} step={step} />
        ))}
      </ol>

      <div className="details-panel">
        <button
          type="button"
          className="btn btn--ghost btn--sm details-panel__toggle"
          onClick={() => setDetailsOpen((o) => !o)}
          aria-expanded={detailsOpen}
        >
          {detailsOpen ? 'Hide details' : 'Details'}
        </button>
        {detailsOpen && (
          <pre className="details-panel__log" ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}>
            {logLines.join('\n')}
          </pre>
        )}
      </div>

      <div className="screen-actions">
        <button
          type="button"
          className="btn btn--secondary"
          onClick={handleCancel}
          disabled={cancelling}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
