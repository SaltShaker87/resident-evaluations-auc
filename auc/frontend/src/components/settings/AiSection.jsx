import React, { useState, useEffect } from 'react';
import { getOllamaModels, getRagStatus, setRetrievalEngine } from '../../api';

// Index health, as one word in the tag. The sentence explaining it — and what
// to do about it — comes from the server, so there is one wording to maintain.
const RAG_STATUS_LABELS = {
  ok: 'Ready',
  warning: 'Attention',
  error: 'Unavailable',
};

// A retrieval engine can be switched to only if this machine can run it and
// its index is built. The server checks the same thing again on the switch,
// and says why when it refuses; this only saves offering a switch that will fail.
const engineUsable = (info) => info.available && info.index?.level !== 'error';
const engineBlocker = (info) => (info.available ? info.index?.message : info.reason);

export default function AiSection() {
  const [models, setModels] = useState([]);
  const [ollamaError, setOllamaError] = useState('');
  const [defaultModel, setDefaultModelState] = useState(
    () => localStorage.getItem('defaultOllamaModel') || ''
  );

  // ACGME index health. null while the first check is in flight.
  const [ragStatus, setRagStatus] = useState(null);
  const [engineBusy, setEngineBusy] = useState(false);
  const [engineError, setEngineError] = useState('');

  useEffect(() => {
    getOllamaModels().then((result) => {
      if (Array.isArray(result) && result.length > 0) {
        setModels(result);
        setOllamaError('');
      } else if (result?.error) {
        setOllamaError('Ollama not reachable — make sure it\'s running.');
      } else {
        setOllamaError('Ollama is running but no models are installed.');
      }
    }).catch(() => {
      setOllamaError('Ollama not reachable — make sure it\'s running.');
    });

    // Checked here so a broken index is visible before someone tries to
    // generate a summary in a committee meeting.
    getRagStatus()
      .then((result) => setRagStatus(result))
      .catch(() => setRagStatus({
        level: 'error',
        message: 'Could not check the ACGME index — the server did not answer.',
      }));
  }, []);

  // The switch returns the new status, so the index row below follows it.
  const handleEngineChange = async (e) => {
    setEngineBusy(true);
    setEngineError('');
    try {
      setRagStatus(await setRetrievalEngine(e.target.value));
    } catch (err) {
      setEngineError(err.message || 'Could not switch the retrieval engine.');
    }
    setEngineBusy(false);
  };

  const handleModelChange = (e) => {
    const val = e.target.value;
    setDefaultModelState(val);
    if (val) {
      localStorage.setItem('defaultOllamaModel', val);
    } else {
      localStorage.removeItem('defaultOllamaModel');
    }
  };

  return (
    <div className="card settings-card">
      <div className="settings-row">
        <div>
          <div className="settings-row__label">Default Ollama model</div>
          <div className="settings-row__desc">
            Used when generating AI summaries on resident pages
          </div>
        </div>
        <div className={`settings-row__control${ollamaError ? ' settings-row__control--message' : ''}`}>
          {ollamaError ? (
            <span className="settings-row__message">
              {ollamaError}
            </span>
          ) : (
            <select
              className="form-select"
              style={{ width: 'auto', minWidth: '200px' }}
              value={defaultModel}
              onChange={handleModelChange}
              disabled={models.length === 0}
            >
              <option value="">Select a default model</option>
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {ragStatus?.engines && (
        <div className="settings-row">
          <div>
            <div className="settings-row__label">Retrieval engine</div>
            <div className="settings-row__desc">
              How a comment that matches no ACGME keyword finds its sub-competency.
              Default on this machine: {ragStatus.engines[ragStatus.default_engine]?.label}
              {ragStatus.is_spark ? ' (DGX Spark detected)' : ''}.
            </div>
            {Object.entries(ragStatus.engines)
              .filter(([key, info]) => key !== ragStatus.engine && !engineUsable(info))
              .map(([key, info]) => (
                <div key={key} className="settings-row__desc settings-row__note">
                  {info.label} can&apos;t be selected: {engineBlocker(info)}
                </div>
              ))}
            {engineError && (
              <div className="settings-row__desc settings-row__note settings-row__note--error">
                {engineError}
              </div>
            )}
          </div>
          <div className="settings-row__control">
            <select
              className="form-select"
              style={{ width: 'auto', minWidth: '200px' }}
              value={ragStatus.engine}
              onChange={handleEngineChange}
              disabled={engineBusy}
            >
              {Object.entries(ragStatus.engines).map(([key, info]) => {
                const selectable = key === ragStatus.engine || engineUsable(info);
                return (
                  <option key={key} value={key} disabled={!selectable}>
                    {info.label}{selectable ? '' : ' — unavailable'}
                  </option>
                );
              })}
            </select>
          </div>
        </div>
      )}

      <div className="settings-row settings-row--status">
        <div className="settings-row__heading">
          <div className="settings-row__label">ACGME reference index</div>
          <span className={`tag tag--index tag--index-${ragStatus ? ragStatus.level : 'checking'}`}>
            {RAG_STATUS_LABELS[ragStatus?.level] || 'Checking'}
          </span>
        </div>
        <div className="settings-row__desc">
          {ragStatus
            ? ragStatus.message
            : 'Checking the index that grounds summaries in the ACGME milestones…'}
        </div>
      </div>
    </div>
  );
}
