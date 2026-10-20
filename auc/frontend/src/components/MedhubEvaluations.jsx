import React, { useEffect, useMemo, useRef, useState } from 'react';

// Milestone levels as the y axis shows them, and in full for the tooltip.
const LEVELS = [
  { n: 1, short: 'Conditional', full: 'Conditional on improvement' },
  { n: 2, short: 'Intern', full: 'Intern' },
  { n: 3, short: 'Junior', full: 'Junior Resident' },
  { n: 4, short: 'Senior', full: 'Senior Resident' },
  { n: 5, short: 'Aspirational', full: 'Aspirational' },
];
const levelName = (n) => LEVELS.find((l) => l.n === n)?.full ?? '';
const levelShort = (n) => LEVELS.find((l) => l.n === n)?.short ?? '';
// An average between two levels is named as the span, never rounded into one.
const averageName = (avg) => (Math.abs(avg - Math.round(avg)) < 0.05
  ? levelName(Math.round(avg))
  : `${levelShort(Math.floor(avg))}–${levelShort(Math.ceil(avg))}`);

const DAY = 24 * 60 * 60 * 1000;
const toTime = (iso) => new Date(`${iso}T00:00:00`).getTime();
const formatDay = (iso) => new Date(`${iso}T00:00:00`)
  .toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;

// Chart geometry in CSS pixels. Each chart measures its own width and draws
// at that size, so axis text is never scaled down to fit a narrow column.
const H = 196;
const M = { top: 12, right: 14, bottom: 26, left: 82 };
const PLOT_H = H - M.top - M.bottom;

function useWidth(initial) {
  const ref = useRef(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => {
      setWidth(Math.max(220, Math.round(entry.contentRect.width)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function monthTicks(start, end) {
  const ticks = [];
  const d = new Date(start);
  d.setDate(1);
  d.setHours(0, 0, 0, 0);
  if (d.getTime() < start) d.setMonth(d.getMonth() + 1);
  while (d.getTime() <= end) {
    ticks.push(d.getTime());
    d.setMonth(d.getMonth() + 1);
  }
  // Thin to at most six labels so they never collide.
  const step = Math.ceil(ticks.length / 6) || 1;
  return ticks.filter((_, i) => i % step === 0);
}

// One competency's ratings over time. Each pale dot is one or more ratings at
// that level for a rotation ending that day (bigger = more); the line joins
// each rotation's average. One hue: the line is the story, dots the context.
function CompetencyChart({ title, ratings, domain }) {
  const [tip, setTip] = useState(null);
  const [frameRef, W] = useWidth(320);
  const PLOT_W = W - M.left - M.right;
  const [t0, t1] = domain;
  const x = (t) => M.left + ((t - t0) / (t1 - t0)) * PLOT_W;
  const y = (level) => M.top + ((5 - level) / 4) * PLOT_H;

  const groups = useMemo(() => {
    const byKey = new Map();
    ratings.forEach((r) => {
      const key = `${r.rotation_end}|${r.level}`;
      if (!byKey.has(key)) byKey.set(key, { date: r.rotation_end, level: r.level, items: [] });
      byKey.get(key).items.push(r);
    });
    return [...byKey.values()];
  }, [ratings]);

  const averages = useMemo(() => {
    const byDate = new Map();
    ratings.forEach((r) => {
      if (!byDate.has(r.rotation_end)) byDate.set(r.rotation_end, []);
      byDate.get(r.rotation_end).push(r);
    });
    return [...byDate.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, items]) => ({ date, avg: mean(items.map((r) => r.level)), items }));
  }, [ratings]);

  const show = (px, py, lines) => setTip({ left: px, top: py, lines });
  const hide = () => setTip(null);
  const path = averages.map((a, i) => `${i ? 'L' : 'M'}${x(toTime(a.date))},${y(a.avg)}`).join(' ');
  const ticks = monthTicks(t0, t1);

  return (
    <div className="mh-chart">
      <div className="mh-chart__title">{title}</div>
      <div className="mh-chart__frame" ref={frameRef}>
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}: ${ratings.length} ratings`}>
          {LEVELS.map((l) => (
            <g key={l.n}>
              <line className="mh-chart__grid" x1={M.left} x2={W - M.right} y1={y(l.n)} y2={y(l.n)} />
              <text className="mh-chart__axis" x={M.left - 8} y={y(l.n)} textAnchor="end" dominantBaseline="middle">
                {l.short}
              </text>
            </g>
          ))}
          {ticks.map((t) => (
            <text key={t} className="mh-chart__axis" x={x(t)} y={H - 8} textAnchor="middle">
              {new Date(t).toLocaleDateString('en-US', { month: 'short' })}
              {new Date(t).getMonth() === 0 || t === ticks[0] ? ` ’${String(new Date(t).getFullYear()).slice(2)}` : ''}
            </text>
          ))}

          {ratings.length === 0 && (
            <text className="mh-chart__empty" x={M.left + PLOT_W / 2} y={M.top + PLOT_H / 2} textAnchor="middle">
              No ratings
            </text>
          )}

          {groups.map((g) => (
            <circle
              key={`${g.date}|${g.level}`}
              className="mh-chart__point"
              cx={x(toTime(g.date))}
              cy={y(g.level)}
              r={4 + Math.min(g.items.length - 1, 3)}
            />
          ))}
          {averages.length > 1 && <path className="mh-chart__line" d={path} />}
          {averages.map((a) => (
            <circle key={a.date} className="mh-chart__avg" cx={x(toTime(a.date))} cy={y(a.avg)} r={4} />
          ))}

          {/* Hit targets: 24px, larger than the marks they describe. */}
          {groups.map((g) => {
            const lines = [
              `${levelName(g.level)} (${g.level})`,
              `${g.items.length} rating${g.items.length === 1 ? '' : 's'} · rotation ending ${formatDay(g.date)}`,
              ...[...new Set(g.items.map((r) => r.evaluator))],
            ];
            return (
              <circle
                key={`hit-${g.date}|${g.level}`}
                className="mh-chart__hit"
                cx={x(toTime(g.date))}
                cy={y(g.level)}
                r={12}
                tabIndex={0}
                aria-label={lines.join('. ')}
                onMouseEnter={() => show(x(toTime(g.date)), y(g.level), lines)}
                onFocus={() => show(x(toTime(g.date)), y(g.level), lines)}
                onMouseLeave={hide}
                onBlur={hide}
              />
            );
          })}
          {averages.map((a) => {
            const lines = [
              `Average ${a.avg.toFixed(1)} · ${averageName(a.avg)}`,
              `${a.items.length} rating${a.items.length === 1 ? '' : 's'} · rotation ending ${formatDay(a.date)}`,
            ];
            return (
              <circle
                key={`hit-avg-${a.date}`}
                className="mh-chart__hit"
                cx={x(toTime(a.date))}
                cy={y(a.avg)}
                r={8}
                tabIndex={0}
                aria-label={lines.join('. ')}
                onMouseEnter={() => show(x(toTime(a.date)), y(a.avg), lines)}
                onFocus={() => show(x(toTime(a.date)), y(a.avg), lines)}
                onMouseLeave={hide}
                onBlur={hide}
              />
            );
          })}
        </svg>
        {tip && (
          <div className="mh-chart__tip" style={{ left: tip.left, top: tip.top }}>
            <strong>{tip.lines[0]}</strong>
            {tip.lines.slice(1).map((line) => <div key={line}>{line}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}

export default function MedhubEvaluations({ data, medhubName }) {
  const [form, setForm] = useState('all');
  const [showAll, setShowAll] = useState(false);

  const forms = useMemo(() => [...new Set([
    ...(data?.ratings ?? []).map((r) => r.form_name),
    ...(data?.comments ?? []).map((c) => c.form_name),
  ])].sort(), [data]);

  if (!data) return null;
  const inForm = (r) => form === 'all' || r.form_name === form;
  const ratings = data.ratings.filter(inForm);
  const rated = ratings.filter((r) => r.level != null);
  const comments = data.comments.filter(inForm).slice().reverse();

  const times = rated.map((r) => toTime(r.rotation_end));
  let domain = times.length ? [Math.min(...times), Math.max(...times)] : [0, 1];
  if (domain[1] - domain[0] < 28 * DAY) {
    const mid = (domain[0] + domain[1]) / 2;
    domain = [mid - 14 * DAY, mid + 14 * DAY];
  } else {
    const pad = (domain[1] - domain[0]) * 0.04;
    domain = [domain[0] - pad, domain[1] + pad];
  }

  const competencyRows = [...data.competencies, 'No single competency']
    .map((c) => {
      const mine = ratings.filter((r) => r.competency === c);
      const levels = mine.filter((r) => r.level != null).map((r) => r.level);
      return {
        competency: c,
        count: levels.length,
        average: levels.length ? mean(levels) : null,
        unable: mine.filter((r) => r.answer_kind === 'unable').length,
        notOnScale: mine.filter((r) => r.answer_kind === 'unrecognised').length,
      };
    })
    .filter((row) => data.competencies.includes(row.competency) || row.count || row.unable);

  const nothingYet = data.ratings.length === 0 && data.comments.length === 0
    && data.blank_comments === 0 && data.na_comments === 0;

  return (
    <div className="section">
      <div className="section__header">
        <div className="section__title">MedHub Evaluations</div>
        {forms.length > 1 && (
          <select
            className="form-select"
            style={{ width: 'auto', padding: '0.25rem 0.5rem', fontSize: '0.85rem' }}
            aria-label="Evaluation form"
            value={form}
            onChange={(e) => setForm(e.target.value)}
          >
            <option value="all">All forms</option>
            {forms.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        )}
      </div>

      {nothingYet ? (
        <div className="text-sm text-muted" style={{ padding: '0.5rem 0' }}>
          {medhubName
            ? <>No MedHub evaluations imported yet for <strong>{medhubName}</strong>. Import them from the MedHub Import page.</>
            : <>Set this resident&apos;s MedHub name (Edit, above) so MedHub imports can find their evaluations.</>}
        </div>
      ) : (
        <>
          <div className="mh-key text-sm text-muted">
            <span className="mh-key__item"><span className="mh-key__dot" /> each rating (bigger = several at the same level)</span>
            <span className="mh-key__item"><span className="mh-key__line" /> average per rotation</span>
            <span>Plotted on the rotation end date. “Unable to Assess” is not plotted or averaged.</span>
          </div>
          <div className="mh-charts">
            {data.competencies.map((c) => (
              <CompetencyChart
                key={c}
                title={c}
                ratings={rated.filter((r) => r.competency === c)}
                domain={domain}
              />
            ))}
          </div>

          <div className="table-scroll" style={{ marginTop: '1rem' }}>
            <table className="data-table">
              <thead>
                <tr><th>Competency</th><th>Average</th><th>Ratings</th><th>Unable to assess</th></tr>
              </thead>
              <tbody>
                {competencyRows.map((row) => (
                  <tr key={row.competency}>
                    <td>{row.competency}</td>
                    <td className="num">
                      {row.average == null ? '—' : `${row.average.toFixed(1)} · ${averageName(row.average)}`}
                    </td>
                    <td className="num">{row.count}{row.notOnScale ? ` (+${row.notOnScale} not on scale)` : ''}</td>
                    <td className="num">{row.unable}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <button className="btn btn--ghost btn--sm" style={{ marginTop: '0.5rem' }} onClick={() => setShowAll((s) => !s)}>
            {showAll ? 'Hide' : 'Show'} every rating as a table ({ratings.length})
          </button>
          {showAll && (
            <div className="table-scroll" style={{ marginTop: '0.5rem' }}>
              <table className="data-table data-table--wrap">
                <thead>
                  <tr><th>Rotation</th><th>Evaluator</th><th>Service</th><th>Question</th><th>Answer</th><th>Level</th><th>Competency</th></tr>
                </thead>
                <tbody>
                  {ratings.map((r) => (
                    <tr key={`${r.rotation_start}|${r.evaluator}|${r.form_name}|${r.question}`}>
                      <td>{formatDay(r.rotation_start)} – {formatDay(r.rotation_end)}</td>
                      <td>{r.evaluator}</td>
                      <td>{r.service}</td>
                      <td>{r.question}</td>
                      <td>{r.answer || '(blank)'}</td>
                      <td className="num">{r.level ?? '—'}</td>
                      <td>{r.competency}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="mh-comments">
            <div className="mh-comments__head">
              <div className="section__title" style={{ fontSize: '1rem' }}>MedHub comments ({comments.length})</div>
              {(data.blank_comments > 0 || data.na_comments > 0) && (
                <span className="text-sm text-muted">
                  Also, across all forms: {data.blank_comments} left blank · {data.na_comments} marked N/A
                </span>
              )}
            </div>
            {comments.length === 0 && <div className="text-sm text-muted">No comments with text.</div>}
            {comments.map((c) => (
              <div key={`${c.rotation_start}|${c.evaluator}|${c.form_name}|${c.question}`} className="mh-comment">
                <div className="mh-comment__meta">
                  <strong>{formatDay(c.rotation_end)}</strong> · {c.evaluator}
                  {c.service ? ` · ${c.service}` : ''} · {c.form_name}
                </div>
                <div className="mh-comment__question">{c.question}</div>
                <div className="mh-comment__text">{c.answer}</div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
