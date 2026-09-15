import { useState } from 'react';
import { openApp } from '../backend.js';
import PreflightList from '../components/PreflightList.jsx';

function nemotronReasonFrom(outcome) {
  const warning = outcome.warnings?.find((w) => /nemotron|NVIDIA refused|sign in/i.test(w));
  if (!warning) return null;
  const fallbackAt = warning.indexOf('. AUC will write');
  if (fallbackAt >= 0) return warning.slice(0, fallbackAt);
  const firstDot = warning.indexOf('. ');
  return firstDot >= 0 ? warning.slice(0, firstDot) : warning;
}

export default function Done({ outcome, preflight, onFinishNemotron }) {
  const [healthOpen, setHealthOpen] = useState(false);
  const nemotronReason = outcome.nemotron_pending ? nemotronReasonFrom(outcome) : null;

  return (
    <div className="screen-content">
      <h1>All set</h1>
      <p className="lead">{outcome.summary}</p>

      {outcome.warnings?.length > 0 && (
        <ul className="warn-list">
          {outcome.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      {outcome.nemotron_pending && (
        <div className="callout callout--amber">
          <p>
            AI summaries are using the Standard engine for now. The last attempt to start
            NVIDIA Nemotron did not finish
            {nemotronReason ? <>: {nemotronReason}</> : null}. You can try again from here.
          </p>
          <button type="button" className="btn btn--primary btn--sm" onClick={onFinishNemotron}>
            Finish Nemotron setup
          </button>
        </div>
      )}

      {outcome.app_url && (
        <>
          <button type="button" className="btn btn--primary btn--lg" onClick={() => openApp()}>
            Open AUC
          </button>
          <p className="hint">
            Address: <code>{outcome.app_url}</code>
          </p>
          <p className="hint">
            AUC starts on its own whenever this computer starts. You can also open it from
            your applications menu.
          </p>
        </>
      )}

      {preflight?.lines?.length > 0 && (
        <div className="collapsible">
          <button
            type="button"
            className="collapsible__toggle"
            onClick={() => setHealthOpen((o) => !o)}
            aria-expanded={healthOpen}
          >
            Health check
          </button>
          {healthOpen && <PreflightList lines={preflight.lines} />}
        </div>
      )}
    </div>
  );
}
