import NgcKeyHelp from '../components/NgcKeyHelp.jsx';

export default function FinishNemotron({ ngcKey, ngcLater, onChange, onRun, onBack }) {
  return (
    <div className="screen-content">
      <h1>Finish Nemotron setup</h1>
      <p className="lead">
        Paste the API key from your free NVIDIA account. It is only used to download models
        once. Nothing is sent off this computer after setup.
      </p>
      <div className="ngc-block">
        <NgcKeyHelp />
        <label className="field-label" htmlFor="finish-ngc-key">
          NVIDIA API key
        </label>
        <input
          id="finish-ngc-key"
          type="password"
          className="input"
          autoComplete="off"
          value={ngcKey || ''}
          disabled={ngcLater}
          onChange={(e) => onChange({ ngc_key: e.target.value || null, ngc_key_later: false })}
        />
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={ngcLater}
            onChange={(e) =>
              onChange({
                ngc_key_later: e.target.checked,
                ngc_key: e.target.checked ? null : ngcKey,
              })
            }
          />
          <span>Do this later (try cached images only)</span>
        </label>
      </div>
      <div className="screen-actions screen-actions--split">
        <button type="button" className="btn btn--secondary" onClick={onBack}>
          Back
        </button>
        <button type="button" className="btn btn--primary" onClick={onRun}>
          Continue
        </button>
      </div>
    </div>
  );
}
