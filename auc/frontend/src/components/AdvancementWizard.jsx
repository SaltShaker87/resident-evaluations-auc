import React, { useState, useEffect } from 'react';
import { X, ArrowLeft, ArrowRight, Check, AlertTriangle, GraduationCap } from 'lucide-react';
import { getResidents, executeAdvancement } from '../api';

const STEP_TITLES = {
  1: 'Step 1 of 4: PGY-3 Residents',
  2: 'Step 2 of 4: PGY-2 Residents',
  3: 'Step 3 of 4: PGY-1 Residents',
  4: 'Step 4 of 4: Review and Confirm',
};

const fullName = (r) => `Dr. ${r.first_name} ${r.last_name}`;

function NameList({ residents, suffix }) {
  if (!residents.length) return <p className="wizard-empty">None</p>;
  return (
    <ul className="wizard-name-list">
      {residents.map((r) => (
        <li key={r.id}>
          {fullName(r)}
          {suffix ? <span className="text-muted"> {suffix(r)}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export default function AdvancementWizard({ onClose, onComplete }) {
  const [loading, setLoading] = useState(true);
  const [residents, setResidents] = useState([]);
  const [step, setStep] = useState(1);
  const [pgy3Choice, setPgy3Choice] = useState({}); // id -> 'graduate' | 'chief'
  const [catAdvance, setCatAdvance] = useState({}); // id -> bool
  const [prelimDepart, setPrelimDepart] = useState({}); // id -> bool
  const [confirmText, setConfirmText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  useEffect(() => {
    getResidents(true)
      .then((data) => {
        setResidents(data);
        const p3 = {}, cat = {}, prelim = {};
        data.forEach((r) => {
          if (r.pgy_year === 3) p3[r.id] = 'graduate';
          else if (r.pgy_year === 1 && r.is_prelim) prelim[r.id] = true;
          else if (r.pgy_year === 1) cat[r.id] = true;
        });
        setPgy3Choice(p3);
        setCatAdvance(cat);
        setPrelimDepart(prelim);
        setLoading(false);
      })
      .catch(() => {
        setError('Failed to load residents');
        setLoading(false);
      });
  }, []);

  const pgy3 = residents.filter((r) => r.pgy_year === 3);
  const pgy2 = residents.filter((r) => r.pgy_year === 2);
  const pgy1cat = residents.filter((r) => r.pgy_year === 1 && !r.is_prelim);
  const pgy1prelim = residents.filter((r) => r.pgy_year === 1 && r.is_prelim);

  const graduate = pgy3.filter((r) => pgy3Choice[r.id] === 'graduate');
  const chief = pgy3.filter((r) => pgy3Choice[r.id] === 'chief');
  const advanceTo3 = pgy2;
  const advanceTo2 = pgy1cat.filter((r) => catAdvance[r.id]);
  const depart = pgy1prelim.filter((r) => prelimDepart[r.id]);

  const totalChanges =
    graduate.length + chief.length + advanceTo3.length + advanceTo2.length + depart.length;

  const buildSummary = () =>
    [
      graduate.length ? `${graduate.length} graduating` : null,
      chief.length ? `${chief.length} becoming chief` : null,
      advanceTo3.length ? `${advanceTo3.length} advanced to PGY-3` : null,
      advanceTo2.length ? `${advanceTo2.length} advanced to PGY-2` : null,
      depart.length ? `${depart.length} prelim${depart.length !== 1 ? 's' : ''} departed` : null,
    ]
      .filter(Boolean)
      .join(' · ');

  const handleSubmit = async () => {
    setSubmitting(true);
    setError('');
    try {
      await executeAdvancement({
        graduate: graduate.map((r) => r.id),
        chief: chief.map((r) => r.id),
        advance_to_pgy3: advanceTo3.map((r) => r.id),
        advance_to_pgy2: advanceTo2.map((r) => r.id),
        depart: depart.map((r) => r.id),
        summary: buildSummary(),
      });
      setDone(true);
    } catch (e) {
      setError(e.message || 'Advancement failed');
      setSubmitting(false);
    }
  };

  const canSubmit = confirmText.trim() === 'ADVANCE' && !submitting;

  return (
    <div className="wizard-overlay">
      <div className="wizard">
        <div className="wizard__header">
          <h2>{done ? 'Advancement Complete' : STEP_TITLES[step]}</h2>
          <button className="btn btn--ghost btn--sm" onClick={onClose} title="Cancel">
            <X size={18} />
          </button>
        </div>

        <div className="wizard__body">
          {loading ? (
            <div className="loading-state"><div className="spinner" /><span>Loading residents…</span></div>
          ) : done ? (
            <div className="wizard-success">
              <div className="wizard-success__icon"><Check size={40} /></div>
              <h3>Residents advanced successfully</h3>
              <p className="text-muted">{buildSummary() || 'No changes were applied.'}</p>
              <p className="text-sm text-muted mt-md">
                You can undo this advancement from the Settings page.
              </p>
            </div>
          ) : (
            <>
              {error && (
                <div className="alert alert--danger mb-md">
                  <AlertTriangle size={16} /> {error}
                </div>
              )}

              {step === 1 && (
                <>
                  <p className="wizard-intro">
                    Choose what happens to each graduating PGY-3 resident. Chief residents are kept
                    in the system but removed from active evaluations.
                  </p>
                  {pgy3.length === 0 ? (
                    <p className="wizard-empty">No PGY-3 residents to advance.</p>
                  ) : (
                    <div className="wizard-rows">
                      {pgy3.map((r) => (
                        <div key={r.id} className="wizard-row">
                          <span className="wizard-row__name">{fullName(r)}</span>
                          <select
                            className="form-select"
                            style={{ width: 'auto', minWidth: '160px' }}
                            value={pgy3Choice[r.id]}
                            onChange={(e) =>
                              setPgy3Choice((p) => ({ ...p, [r.id]: e.target.value }))
                            }
                          >
                            <option value="graduate">Graduate</option>
                            <option value="chief">Chief Resident</option>
                          </select>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="wizard-count">
                    Graduating: {graduate.length} | Becoming Chief: {chief.length}
                  </div>
                </>
              )}

              {step === 2 && (
                <>
                  <p className="wizard-intro">
                    All PGY-2 residents will advance to PGY-3. Review the list and confirm it looks
                    right.
                  </p>
                  {pgy2.length === 0 ? (
                    <p className="wizard-empty">No PGY-2 residents to advance.</p>
                  ) : (
                    <div className="wizard-rows">
                      {pgy2.map((r) => (
                        <div key={r.id} className="wizard-row">
                          <span className="wizard-row__name">{fullName(r)}</span>
                          <span className="tag tag--pgy tag--pgy-3">PGY-2 → PGY-3</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="wizard-count">Advancing to PGY-3: {advanceTo3.length}</div>
                </>
              )}

              {step === 3 && (
                <>
                  <p className="wizard-intro">
                    Categorical PGY-1s advance to PGY-2. Prelim residents depart the program.
                    Uncheck anyone who should not follow the default.
                  </p>

                  <div className="wizard-subhead">Categorical Residents</div>
                  {pgy1cat.length === 0 ? (
                    <p className="wizard-empty">No categorical PGY-1 residents.</p>
                  ) : (
                    <div className="wizard-rows">
                      {pgy1cat.map((r) => (
                        <label key={r.id} className="wizard-row wizard-row--checkbox">
                          <input
                            type="checkbox"
                            checked={!!catAdvance[r.id]}
                            onChange={(e) =>
                              setCatAdvance((p) => ({ ...p, [r.id]: e.target.checked }))
                            }
                          />
                          <span className="wizard-row__name">{fullName(r)}</span>
                          <span className="text-sm text-muted">Advance to PGY-2</span>
                        </label>
                      ))}
                    </div>
                  )}

                  <div className="wizard-subhead">Prelim Residents</div>
                  {pgy1prelim.length === 0 ? (
                    <p className="wizard-empty">No prelim residents.</p>
                  ) : (
                    <div className="wizard-rows">
                      {pgy1prelim.map((r) => (
                        <label key={r.id} className="wizard-row wizard-row--checkbox">
                          <input
                            type="checkbox"
                            checked={!!prelimDepart[r.id]}
                            onChange={(e) =>
                              setPrelimDepart((p) => ({ ...p, [r.id]: e.target.checked }))
                            }
                          />
                          <span className="wizard-row__name">
                            {fullName(r)}
                            {r.prelim_specialty ? (
                              <span className="text-muted"> · {r.prelim_specialty}</span>
                            ) : null}
                          </span>
                          <span className="text-sm text-muted">Depart</span>
                        </label>
                      ))}
                    </div>
                  )}
                  <div className="wizard-count">
                    Advancing to PGY-2: {advanceTo2.length} | Departing: {depart.length}
                  </div>
                </>
              )}

              {step === 4 && (
                <>
                  <p className="wizard-intro">
                    Review every change below. This will update {totalChanges} resident
                    {totalChanges !== 1 ? 's' : ''}. A snapshot is saved first so you can undo.
                  </p>

                  <div className="wizard-review">
                    <div className="wizard-review__group">
                      <div className="wizard-review__label">Graduating ({graduate.length})</div>
                      <NameList residents={graduate} />
                    </div>
                    <div className="wizard-review__group">
                      <div className="wizard-review__label">Becoming Chief Resident ({chief.length})</div>
                      <NameList residents={chief} />
                    </div>
                    <div className="wizard-review__group">
                      <div className="wizard-review__label">Advancing to PGY-3 ({advanceTo3.length})</div>
                      <NameList residents={advanceTo3} />
                    </div>
                    <div className="wizard-review__group">
                      <div className="wizard-review__label">Advancing to PGY-2 ({advanceTo2.length})</div>
                      <NameList residents={advanceTo2} />
                    </div>
                    <div className="wizard-review__group">
                      <div className="wizard-review__label">Departing (Prelim) ({depart.length})</div>
                      <NameList residents={depart} suffix={(r) => (r.prelim_specialty ? `· ${r.prelim_specialty}` : '')} />
                    </div>
                  </div>

                  <div className="alert alert--warning mt-md mb-md">
                    <AlertTriangle size={16} />
                    <span>
                      Total changes: <strong>{totalChanges}</strong>. Type{' '}
                      <strong>ADVANCE</strong> below to enable the confirm button.
                    </span>
                  </div>
                  <div className="form-group">
                    <label>Type ADVANCE to confirm</label>
                    <input
                      className="form-input"
                      value={confirmText}
                      onChange={(e) => setConfirmText(e.target.value)}
                      placeholder="ADVANCE"
                      autoFocus
                    />
                  </div>
                </>
              )}
            </>
          )}
        </div>

        {!loading && (
          <div className="wizard__footer">
            {done ? (
              <button className="btn btn--primary" onClick={onComplete}>
                Back to Settings
              </button>
            ) : (
              <>
                <button className="btn btn--danger" onClick={onClose}>Cancel</button>
                <div className="wizard__footer-right">
                  {step > 1 && (
                    <button className="btn btn--secondary" onClick={() => setStep((s) => s - 1)}>
                      <ArrowLeft size={15} /> Back
                    </button>
                  )}
                  {step < 4 ? (
                    <button className="btn btn--primary" onClick={() => setStep((s) => s + 1)}>
                      Next <ArrowRight size={15} />
                    </button>
                  ) : (
                    <button className="btn btn--primary" onClick={handleSubmit} disabled={!canSubmit}>
                      <GraduationCap size={15} />
                      {submitting ? 'Advancing…' : 'Confirm Advancement'}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
