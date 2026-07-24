import React from 'react';
import { Quote, AlertCircle, MinusCircle } from 'lucide-react';

// Display order of the six ACGME competency domains. Matches
// rag_retrieval.DOMAIN_ORDER on the backend.
const DOMAIN_ORDER = ['PC', 'MK', 'SBP', 'PBLI', 'PROF', 'ICS'];

const LEVELS = [1, 2, 3, 4, 5];

// Everything the model produces is rendered as plain text nodes — there is no
// markdown anywhere in this pipeline.

function EmptyState({ status }) {
  if (status === 'pending') {
    return (
      <div className="subcomp-card__empty">
        <div className="spinner spinner--sm" />
        <span>Generating…</span>
      </div>
    );
  }
  if (status === 'no_evidence') {
    return (
      <div className="subcomp-card__empty">
        <MinusCircle size={14} />
        <span>No evidence this cycle</span>
      </div>
    );
  }
  if (status === 'insufficient_evidence') {
    return (
      <div className="subcomp-card__empty subcomp-card__empty--warn">
        <AlertCircle size={14} />
        <span>
          Insufficient evidence — the draft was withheld because none of its quotes
          could be verified against the source comments.
        </span>
      </div>
    );
  }
  return (
    <div className="subcomp-card__empty subcomp-card__empty--warn">
      <AlertCircle size={14} />
      <span>Could not generate this section.</span>
    </div>
  );
}

function SubcompetencyCard({ section, editable, onChange }) {
  const isOk = section.status === 'ok';

  return (
    <div className={`subcomp-card subcomp-card--${section.status}`}>
      <div className="subcomp-card__head">
        <div className="subcomp-card__title">
          <span className="subcomp-card__code">{section.id}</span>
          <span className="subcomp-card__name">{section.name}</span>
        </div>
        {isOk && (
          editable ? (
            <label className="level-chip level-chip--edit">
              <span className="level-chip__label">Suggested level</span>
              <select
                className="level-chip__select"
                value={section.level ?? ''}
                onChange={(e) => onChange({
                  ...section,
                  level: e.target.value === '' ? null : Number(e.target.value),
                })}
              >
                <option value="">—</option>
                {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
              <span className="level-chip__draft">Committee draft</span>
            </label>
          ) : (
            <span className="level-chip">
              <span className="level-chip__label">Suggested level</span>
              <span className="level-chip__value">{section.level ?? '—'}</span>
              <span className="level-chip__draft">Committee draft</span>
            </span>
          )
        )}
      </div>

      {!isOk ? (
        <EmptyState status={section.status} />
      ) : (
        <>
          {editable ? (
            <textarea
              className="subcomp-card__narrative-input"
              value={section.narrative || ''}
              rows={3}
              onChange={(e) => onChange({ ...section, narrative: e.target.value })}
            />
          ) : (
            <p className="subcomp-card__narrative">{section.narrative}</p>
          )}

          {section.quotes?.length > 0 && (
            <div className="subcomp-evidence">
              <div className="subcomp-evidence__label">
                <Quote size={11} /> Supporting evidence
              </div>
              {section.quotes.map((q, i) => (
                <blockquote key={i} className="subcomp-quote">
                  <span className="subcomp-quote__text">{q.text}</span>
                  {q.source && (
                    <cite className="subcomp-quote__source">{q.source}</cite>
                  )}
                </blockquote>
              ))}
            </div>
          )}

          {section.dropped_quote_count > 0 && (
            <div className="subcomp-card__dropped">
              {section.dropped_quote_count} unverifiable{' '}
              {section.dropped_quote_count === 1 ? 'quote was' : 'quotes were'} dropped.
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function SummaryReport({ report, editable = false, onChange }) {
  const sections = report?.sections || [];
  if (sections.length === 0) return null;

  // Group by domain, keeping domains in canonical order and any unexpected
  // domain code at the end rather than dropping it.
  const byDomain = new Map();
  sections.forEach((s) => {
    if (!byDomain.has(s.domain)) byDomain.set(s.domain, []);
    byDomain.get(s.domain).push(s);
  });
  const domainCodes = [
    ...DOMAIN_ORDER.filter((d) => byDomain.has(d)),
    ...[...byDomain.keys()].filter((d) => !DOMAIN_ORDER.includes(d)),
  ];

  const handleSectionChange = (updated) => {
    if (!onChange) return;
    onChange({
      ...report,
      sections: sections.map((s) => (s.id === updated.id ? updated : s)),
    });
  };

  return (
    <div className="summary-report">
      {domainCodes.map((code) => {
        const group = byDomain.get(code);
        return (
          <section key={code} className="summary-domain">
            <h3 className="summary-domain__title">
              {group[0].domain_name || code}
            </h3>
            {group.map((s) => (
              <SubcompetencyCard
                key={s.id}
                section={s}
                editable={editable}
                onChange={handleSectionChange}
              />
            ))}
          </section>
        );
      })}
    </div>
  );
}
