import React, { useState, useMemo } from 'react';
import { saveMedhubQuestionMap } from '../api';

export const QUESTION_TYPE_LABELS = {
  milestone: 'Milestone rating (1–5)',
  comment: 'Comment',
  ignore: 'Ignore',
};

const keyOf = (item) => `${item.form_name}\u0000${item.question}`;

// Each MedHub question, per form, gets a type and a competency area. Used in
// the import preview (questions seen for the first time; every one must be
// mapped before the import can be saved) and in Settings (every mapping,
// editable). In the preview the type may arrive pre-selected from the answers
// seen — a suggestion only; nothing is saved until Save is pressed.
export default function MedhubQuestionMap({ items, competencies, mode = 'new', onSaved }) {
  const initial = useMemo(() => Object.fromEntries(items.map((item) => [keyOf(item), {
    question_type: item.question_type ?? item.suggested_type ?? '',
    competency: item.competency ?? '',
  }])), [items]);
  const [draft, setDraft] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const forms = useMemo(() => {
    const byForm = new Map();
    items.forEach((item) => {
      if (!byForm.has(item.form_name)) byForm.set(item.form_name, []);
      byForm.get(item.form_name).push(item);
    });
    return [...byForm.entries()];
  }, [items]);

  const isComplete = (d) => d.question_type && (d.question_type === 'ignore' || d.competency);
  const isChanged = (item) => {
    const d = draft[keyOf(item)];
    const o = initial[keyOf(item)];
    return d.question_type !== o.question_type || d.competency !== o.competency;
  };
  const toSave = mode === 'new' ? items : items.filter(isChanged);
  const canSave = toSave.length > 0 && toSave.every((item) => isComplete(draft[keyOf(item)]));

  const set = (item, field, value) => setDraft((prev) => ({
    ...prev,
    [keyOf(item)]: { ...prev[keyOf(item)], [field]: value },
  }));

  const handleSave = async () => {
    setSaving(true);
    setError('');
    try {
      await saveMedhubQuestionMap(toSave.map((item) => {
        const d = draft[keyOf(item)];
        return {
          form_name: item.form_name,
          question: item.question,
          question_type: d.question_type,
          competency: d.question_type === 'ignore' ? null : d.competency,
        };
      }));
      await onSaved?.();
    } catch (err) {
      setError(err.message || 'Could not save the mapping.');
    }
    setSaving(false);
  };

  return (
    <div className="question-map">
      {forms.map(([form, formItems]) => (
        <div key={form} className="question-map__form">
          <div className="question-map__form-name">{form}</div>
          {formItems.map((item) => {
            const d = draft[keyOf(item)];
            return (
              <div key={keyOf(item)} className="question-map__row">
                <div className="question-map__question">
                  <div>{item.question}</div>
                  {item.example_answers?.length > 0 && (
                    <div className="text-sm text-muted">
                      e.g. {item.example_answers.map((a) => `“${a.length > 60 ? `${a.slice(0, 57)}…` : a}”`).join(', ')}
                    </div>
                  )}
                  {mode === 'edit' && (
                    <div className="text-sm text-muted">{item.stored} stored answer{item.stored === 1 ? '' : 's'}</div>
                  )}
                </div>
                <div className="question-map__controls">
                  <select
                    className="form-select"
                    aria-label="Question type"
                    value={d.question_type}
                    onChange={(e) => set(item, 'question_type', e.target.value)}
                  >
                    <option value="" disabled>Choose a type…</option>
                    {Object.entries(QUESTION_TYPE_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                  <select
                    className="form-select"
                    aria-label="Competency area"
                    value={d.question_type === 'ignore' ? '' : d.competency}
                    disabled={d.question_type === 'ignore'}
                    onChange={(e) => set(item, 'competency', e.target.value)}
                  >
                    <option value="" disabled>
                      {d.question_type === 'ignore' ? 'Not needed' : 'Choose a competency…'}
                    </option>
                    {competencies.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              </div>
            );
          })}
        </div>
      ))}
      {mode === 'edit' && (
        <p className="text-sm text-muted">
          Changing a question to Ignore hides its stored answers. Changing it from Ignore
          takes effect on the next import of a report that has it.
        </p>
      )}
      {error && <div className="alert alert--danger" style={{ marginTop: '0.75rem' }}>{error}</div>}
      <div className="question-map__actions">
        <button className="btn btn--primary btn--sm" onClick={handleSave} disabled={!canSave || saving}>
          {saving ? 'Saving…' : mode === 'new' ? 'Save mapping' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}
