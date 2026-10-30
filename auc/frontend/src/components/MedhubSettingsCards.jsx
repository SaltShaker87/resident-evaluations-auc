import React, { useState, useEffect, useCallback } from 'react';
import { getMedhubQuestionMap, getIgnoredMedhubNames, unignoreMedhubName } from '../api';
import MedhubQuestionMap from './MedhubQuestionMap';

// Settings for the MedHub import: which competency area (and type) each
// evaluation question belongs to, and the names an import should skip.
export default function MedhubSettingsCards() {
  const [mapping, setMapping] = useState(null);
  const [ignored, setIgnored] = useState([]);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [m, i] = await Promise.all([getMedhubQuestionMap(), getIgnoredMedhubNames()]);
      setMapping(m);
      setIgnored(i);
    } catch {
      setError('Could not load the MedHub settings.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleUnignore = async (name) => {
    setError('');
    try {
      await unignoreMedhubName(name);
      await load();
    } catch (err) {
      setError(err.message || 'Something went wrong.');
    }
  };

  return (
    <>
      <div className="card settings-card">
        <div className="settings-section-title">MedHub question mapping</div>
        <div className="settings-row__desc" style={{ paddingTop: '1rem', paddingBottom: '0.75rem' }}>
          Each evaluation question, per form, and the ACGME competency area it counts toward.
          Milestone ratings feed the graphs on each resident&apos;s page; comments feed the
          comment list and AI summaries; ignored questions are not imported. New questions are
          added here the first time an import contains them.
        </div>
        {error && (
          <div className="settings-row__desc settings-row__note settings-row__note--error">{error}</div>
        )}
        {mapping && mapping.items.length === 0 && (
          <div className="text-sm text-muted" style={{ paddingBottom: '0.5rem' }}>
            No questions yet — they appear after the first MedHub import.
          </div>
        )}
        {mapping && mapping.items.length > 0 && (
          <MedhubQuestionMap
            key={JSON.stringify(mapping.items.map((i) => [i.question_type, i.competency]))}
            mode="edit"
            items={mapping.items}
            competencies={mapping.competencies}
            onSaved={load}
          />
        )}
      </div>

      <div className="card settings-card">
        <div className="settings-section-title">Ignored MedHub names</div>
        <div className="settings-row__desc" style={{ paddingTop: '1rem' }}>
          Names a MedHub import skips without asking — residents who have graduated or left.
          Stop ignoring one to see it in the &quot;Not in roster&quot; list again.
        </div>
        {ignored.length === 0 && (
          <div className="text-sm text-muted" style={{ padding: '0.5rem 0' }}>No ignored names.</div>
        )}
        {ignored.map((n) => (
          <div key={n.name_key} className="settings-row">
            <div className="settings-row__label">{n.name}</div>
            <div className="settings-row__control">
              <button className="btn btn--secondary btn--sm" onClick={() => handleUnignore(n.name)}>
                Stop ignoring
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
