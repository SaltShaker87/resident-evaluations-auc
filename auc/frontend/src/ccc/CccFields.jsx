/**
 * AUC — CCC drawer field primitives.
 *
 * Every control here is one tap or one short line of typing, because this is filled in
 * live during a meeting while the operator is also talking. No dropdowns, no modals, no
 * confirmation dialogs. Text fields autosave on a debounce as well as on blur/Enter, so
 * there is never a save button to remember.
 *
 * Tapping an already-selected choice clears it — that is the undo for a mis-tap, and it
 * matters because every field is optional and a wrong value is worse than a blank one.
 */

import React, { useEffect, useRef, useState } from 'react';

export function ChoiceRow({ options, value, onChange, size = 'sm' }) {
  return (
    <div className={`ccc-choice ccc-choice--${size}`}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={`ccc-choice__btn${value === opt.value ? ' is-active' : ''}`}
          onClick={() => onChange(value === opt.value ? null : opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function ChipRow({ options, values, onChange }) {
  const selected = new Set(values || []);
  const toggle = (v) => {
    const next = new Set(selected);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    onChange(options.filter((o) => next.has(o.value)).map((o) => o.value));
  };
  return (
    <div className="ccc-chips">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={`ccc-chip${selected.has(opt.value) ? ' is-active' : ''}`}
          onClick={() => toggle(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

const YES_NO = [
  { value: true, label: 'Yes' },
  { value: false, label: 'No' },
];

export function YesNo({ value, onChange }) {
  return (
    <div className="ccc-choice ccc-choice--sm">
      {YES_NO.map((opt) => (
        <button
          key={String(opt.value)}
          type="button"
          className={`ccc-choice__btn${value === opt.value ? ' is-active' : ''}`}
          onClick={() => onChange(value === opt.value ? null : opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Text that saves itself: debounced while typing, immediately on blur or Enter.
 *
 * Local state is the source of truth for rendering, so a slow or failed save never
 * yanks the cursor or discards a half-typed sentence. `value` is only adopted when it
 * changes externally (a fresh log loading in).
 */
export function AutoText({
  value, onSave, placeholder, multiline = false, debounceMs = 600, rows = 2,
}) {
  const [text, setText] = useState(value ?? '');
  const timer = useRef(null);
  const latest = useRef(text);
  const external = useRef(value ?? '');
  // Callers pass an inline arrow, so onSave has a new identity every render. Holding it
  // in a ref keeps the unmount effect's dependency list empty — otherwise the cleanup
  // would run on every keystroke and flush the pending save immediately, which would
  // defeat the debounce entirely and fire one request per character.
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  useEffect(() => {
    const incoming = value ?? '';
    if (incoming !== external.current) {
      external.current = incoming;
      setText(incoming);
      latest.current = incoming;
    }
  }, [value]);

  useEffect(() => () => {
    // Unmounting (drawer closed, resident changed) must not drop the last keystrokes.
    if (timer.current) {
      clearTimeout(timer.current);
      onSaveRef.current(latest.current);
    }
  }, []);

  const change = (next) => {
    setText(next);
    latest.current = next;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      onSaveRef.current(latest.current);
    }, debounceMs);
  };

  const commit = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    onSaveRef.current(latest.current);
  };

  const shared = {
    value: text,
    placeholder,
    onChange: (e) => change(e.target.value),
    onBlur: commit,
    className: 'ccc-input',
  };

  if (multiline) {
    return <textarea {...shared} rows={rows} />;
  }
  return (
    <input
      type="text"
      {...shared}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          commit();
          e.target.blur();
        }
      }}
    />
  );
}

export function Section({ title, hint, children }) {
  return (
    <section className="ccc-section">
      <h3 className="ccc-section__title">{title}</h3>
      {hint && <p className="ccc-section__hint">{hint}</p>}
      {children}
    </section>
  );
}

export function Field({ label, children }) {
  return (
    <div className="ccc-field">
      {label && <span className="ccc-field__label">{label}</span>}
      {children}
    </div>
  );
}
