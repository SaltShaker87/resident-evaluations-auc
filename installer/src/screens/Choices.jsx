import { openUrl } from '../backend.js';
import { SECURITY_URL } from '../utils.js';

export default function Choices({
  system,
  recommendation,
  choices,
  onChange,
  onInstall,
  onBack,
}) {
  const update = (patch) => onChange({ ...choices, ...patch });

  const nemotronMode = recommendation.nemotron.mode;
  const showNemotronToggle = nemotronMode !== 'unavailable';

  return (
    <div className="screen-content screen-content--scroll">
      <h1>Your choices</h1>
      <p className="lead">You can change these later by running the installer again.</p>

      <section className="choice-section">
        <h2>AI summaries</h2>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={choices.ai_enabled}
            onChange={(e) => update({ ai_enabled: e.target.checked })}
          />
          <span>Turn on AI-generated resident summaries</span>
        </label>
        {!choices.ai_enabled && (
          <p className="hint">
            Everything else in AUC works without AI summaries. You can turn this on later.
          </p>
        )}
      </section>

      {choices.ai_enabled && (
        <section className="choice-section">
          <h2>AI model</h2>
          <fieldset className="radio-group">
            {recommendation.models.map((m) => (
              <label
                key={m.name}
                className={`radio-card ${!m.fits ? 'radio-card--disabled' : ''} ${choices.model === m.name ? 'radio-card--selected' : ''}`}
              >
                <input
                  type="radio"
                  name="model"
                  value={m.name}
                  disabled={!m.fits}
                  checked={choices.model === m.name}
                  onChange={() => update({ model: m.name })}
                />
                <span className="radio-card__title">
                  {m.label}
                  {m.recommended && <span className="badge">Recommended</span>}
                </span>
                <span className="radio-card__desc">{m.description}</span>
                {!m.fits && (
                  <span className="radio-card__warn">
                    Needs more than {m.min_memory_gb - 1} GB of graphics card memory; this computer has {system?.gpu?.memory_gb ?? 0} GB.
                  </span>
                )}
              </label>
            ))}
          </fieldset>
          <p className="best-message">{recommendation.best_message}</p>
        </section>
      )}

      {choices.ai_enabled && (
        <section className="choice-section">
          <h2>NVIDIA Nemotron</h2>
          {nemotronMode === 'unavailable' && (
            <p className="muted">{recommendation.nemotron.reason}</p>
          )}
          {showNemotronToggle && (
            <>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={choices.nemotron_enabled}
                  onChange={(e) => update({ nemotron_enabled: e.target.checked })}
                />
                <span>Use NVIDIA Nemotron for the best summaries</span>
              </label>
              {nemotronMode === 'default' && (
                <p className="hint">
                  This is the best fit for your machine. You will need a free NVIDIA account
                  to download the models once. Your key is only used for that download; nothing
                  is sent off this computer after setup.
                </p>
              )}
              {nemotronMode === 'optional' && (
                <p className="hint">
                  Optional on this machine. If you turn it on, you will need a free NVIDIA
                  account to download the models once. Your key is only used for that download;
                  nothing is sent off this computer after setup.
                </p>
              )}
              {choices.nemotron_enabled && (
                <div className="ngc-block">
                  <button
                    type="button"
                    className="btn btn--secondary btn--sm"
                    onClick={() => openUrl('https://ngc.nvidia.com')}
                  >
                    Open NVIDIA account site
                  </button>
                  <label className="field-label" htmlFor="ngc-key">
                    Paste your NVIDIA API key (optional now)
                  </label>
                  <input
                    id="ngc-key"
                    type="password"
                    className="input"
                    autoComplete="off"
                    value={choices.ngc_key || ''}
                    disabled={choices.ngc_key_later}
                    onChange={(e) => update({ ngc_key: e.target.value || null, ngc_key_later: false })}
                  />
                  <label className="toggle-row">
                    <input
                      type="checkbox"
                      checked={choices.ngc_key_later}
                      onChange={(e) =>
                        update({
                          ngc_key_later: e.target.checked,
                          ngc_key: e.target.checked ? null : choices.ngc_key,
                        })
                      }
                    />
                    <span>Do this later</span>
                  </label>
                </div>
              )}
            </>
          )}
        </section>
      )}

      <section className="choice-section">
        <h2>Who connects to AUC</h2>
        <fieldset className="radio-group">
          <label className={`radio-card ${choices.network_scope === 'local' ? 'radio-card--selected' : ''}`}>
            <input
              type="radio"
              name="scope"
              value="local"
              checked={choices.network_scope === 'local'}
              onChange={() => update({ network_scope: 'local' })}
            />
            <span className="radio-card__title">Just this computer</span>
            <span className="radio-card__desc">
              Only this machine can open AUC. This is the safest option on hospital networks.
            </span>
          </label>
          <label className={`radio-card ${choices.network_scope === 'lan' ? 'radio-card--selected' : ''}`}>
            <input
              type="radio"
              name="scope"
              value="lan"
              checked={choices.network_scope === 'lan'}
              onChange={() => update({ network_scope: 'lan' })}
            />
            <span className="radio-card__title">Other computers on my network too</span>
            <span className="radio-card__desc">
              Colleagues on the same network can open AUC in their browser. On hospital Wi‑Fi,
              the login is not encrypted over the network.
              <button
                type="button"
                className="link-btn"
                onClick={() => openUrl(SECURITY_URL)}
              >
                Read security guidance
              </button>
            </span>
          </label>
        </fieldset>
      </section>

      {system.existing?.kind === 'manual' && (
        <section className="choice-section">
          <h2>Existing installation</h2>
          <p className="hint">
            We found AUC installed manually at{' '}
            <code>{system.existing.working_directory}</code>.
          </p>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={choices.adopt_existing}
              onChange={(e) => update({ adopt_existing: e.target.checked })}
            />
            <span>Move my existing AUC data into the new install</span>
          </label>
        </section>
      )}

      <div className="screen-actions screen-actions--split">
        <button type="button" className="btn btn--secondary" onClick={onBack}>
          Back
        </button>
        <button
          type="button"
          className="btn btn--primary"
          disabled={choices.ai_enabled && !choices.model}
          onClick={onInstall}
        >
          Install
        </button>
      </div>
    </div>
  );
}
