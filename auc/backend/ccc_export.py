"""
AUC — CCC study exports.

Pure builders: each takes an open sqlite3 connection and returns `(header, rows)`.
No router, no globals, no module state.

Two rules hold everywhere in this file:

1. **`study_code` is the only resident identifier that may appear in an export.**
   Never a name, never a `resident_id`. `_check_columns` enforces it, so a future
   edit that adds an identifying column fails loudly instead of leaking quietly.

2. **`include_text=False` (the default) omits every free-text column**, rather than
   blanking it. Free text is where identifiable narrative hides. The consequence is
   that the two variants have different column sets, so an analysis script has to
   tolerate both.
"""

import csv
import io
import json
import re
from datetime import date

from fastapi.responses import Response

# Columns that must never be emitted, whatever include_text says.
FORBIDDEN_COLUMNS = {
    "resident_id",
    "first_name",
    "last_name",
    "photo_filename",
    "medical_school",
    "interests",
}

# Free text, emitted only when include_text=True.
TEXT_COLUMNS = {
    "room_raised_notes",
    "pushback_note",
    "closing_notes",
    "detail",
    "item_text",
    "owner",
}


def _check_columns(header, include_text=None):
    """Guard the two export rules. Raises rather than emitting a bad file."""
    leaked = FORBIDDEN_COLUMNS.intersection(header)
    if leaked:
        raise RuntimeError(f"CCC export would leak identifying columns: {sorted(leaked)}")
    if include_text is False:
        text = TEXT_COLUMNS.intersection(header)
        if text:
            raise RuntimeError(f"CCC export leaked free text with include_text=false: {sorted(text)}")
    return header


def csv_response(basename: str, header, rows) -> Response:
    """A CSV download, named the way the rest of AUC names its exports."""
    _check_columns(header)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    safe = re.sub(r"[^A-Za-z0-9_-]", "", basename) or "export"
    filename = f"{safe}-{date.today().isoformat()}.csv"
    return Response(
        # BOM so Excel reads it as UTF-8 rather than the system codepage — the same
        # reason the MedHub importer decodes utf-8-sig.
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _roles_pipe(raw):
    """JSON array of roles -> pipe-delimited, e.g. 'chief|pd'."""
    if not raw:
        return ""
    try:
        return "|".join(json.loads(raw))
    except (ValueError, TypeError):
        return ""


def _select(row, columns):
    return [row[c] if row[c] is not None else "" for c in columns]


def totals(conn):
    """Counts for the Study Data page's totals line."""
    def count(sql):
        return conn.execute(sql).fetchone()[0]

    return {
        "sessions": count("SELECT COUNT(*) FROM ccc_sessions"),
        "open_session": count("SELECT COUNT(*) FROM ccc_sessions WHERE closed_at IS NULL"),
        "resident_logs": count("SELECT COUNT(*) FROM ccc_resident_logs"),
        "residents_covered": count(
            "SELECT COUNT(DISTINCT study_code) FROM ccc_resident_logs WHERE study_code IS NOT NULL"
        ),
        "contributions": count("SELECT COUNT(*) FROM ccc_contributions WHERE voided = 0"),
        "action_items": count("SELECT COUNT(*) FROM ccc_action_items"),
        "action_item_checks": count("SELECT COUNT(*) FROM ccc_action_item_checks"),
    }


# ---------------------------------------------------------------------------
# resident-sessions.csv — one row per resident log, across all sessions
# ---------------------------------------------------------------------------

_RESIDENT_SESSIONS_SQL = """
SELECT
    l.study_code,
    s.meeting_date                                  AS session_date,
    s.cycle_label,
    l.pgy_year_at_session,
    l.room_input_level,
    l.roles_spoke,
    l.referenced_written_eval,
    l.group_read_shifted,
    l.pushback,
    l.room_raised_notes,
    l.pushback_note,
    l.closing_notes,
    -- Was this a real discussion, or just an incidental page visit? A log row is created
    -- merely by opening a resident's page during a meeting, so without this the two are
    -- indistinguishable and phantom rows dilute any denominator.
    CASE WHEN
            l.room_input_level IS NOT NULL
         OR (l.roles_spoke IS NOT NULL AND TRIM(l.roles_spoke) NOT IN ('', '[]'))
         OR l.referenced_written_eval IS NOT NULL
         OR l.group_read_shifted IS NOT NULL
         OR l.pushback IS NOT NULL
         -- Free text and an explicit close are evidence of a real discussion too. Only the
         -- 0/1 result is exported, never the text, so this is safe with include_text off.
         OR (l.room_raised_notes IS NOT NULL AND TRIM(l.room_raised_notes) != '')
         OR (l.pushback_note IS NOT NULL AND TRIM(l.pushback_note) != '')
         OR (l.closing_notes IS NOT NULL AND TRIM(l.closing_notes) != '')
         OR l.closed_at IS NOT NULL
         OR COUNT(c.id) > 0            -- surviving contributions only; the join drops voided
        THEN 1 ELSE 0 END AS touched,
    SUM(CASE WHEN c.contribution_type = 'todo_surfaced' THEN 1 ELSE 0 END) AS n_todo_surfaced,
    SUM(CASE WHEN c.contribution_type = 'eval_content'  THEN 1 ELSE 0 END) AS n_eval_content,
    SUM(CASE WHEN c.contribution_type = 'pattern_trend' THEN 1 ELSE 0 END) AS n_pattern_trend,
    SUM(CASE WHEN c.contribution_type = 'discrepancy'   THEN 1 ELSE 0 END) AS n_discrepancy,
    SUM(CASE WHEN c.outcome = 'no_effect'          THEN 1 ELSE 0 END) AS n_outcome_no_effect,
    SUM(CASE WHEN c.outcome = 'added_detail'       THEN 1 ELSE 0 END) AS n_outcome_added_detail,
    SUM(CASE WHEN c.outcome = 'changed_assessment' THEN 1 ELSE 0 END) AS n_outcome_changed_assessment,
    SUM(CASE WHEN c.outcome = 'new_action_item'    THEN 1 ELSE 0 END) AS n_outcome_new_action_item,
    -- Derived from the first SURVIVING contribution, not from l.first_contribution_at.
    -- That column is stamped on insert and never revisited, so if the first card was
    -- later voided it would report a time to a contribution that is not in this file.
    CASE
        WHEN MIN(c.created_at) IS NULL THEN NULL
        ELSE CAST(ROUND((julianday(MIN(c.created_at)) - julianday(l.opened_at)) * 86400)
                  AS INTEGER)
    END AS seconds_to_first_contribution,
    CASE
        WHEN l.closed_at IS NOT NULL THEN 'log_closed'
        WHEN s.closed_at IS NOT NULL THEN 'session_closed'
        WHEN MAX(c.created_at) IS NOT NULL THEN 'last_contribution'
        ELSE 'none'
    END AS duration_basis,
    ROUND(
        (julianday(COALESCE(l.closed_at, s.closed_at, MAX(c.created_at)))
         - julianday(l.opened_at)) * 1440.0, 2
    ) AS minutes_on_resident
FROM ccc_resident_logs l
JOIN ccc_sessions s ON s.id = l.session_id
LEFT JOIN ccc_contributions c
       ON c.resident_log_id = l.id AND c.voided = 0
GROUP BY l.id
ORDER BY s.meeting_date, s.created_at, l.study_code
"""

_RESIDENT_SESSIONS_BASE = [
    "study_code",
    "session_date",
    "cycle_label",
    # Early on purpose: this is the row-level gate you apply before reading anything else.
    "touched",
    "pgy_year_at_session",
    "room_input_level",
    "roles_spoke",
    "referenced_written_eval",
    "n_todo_surfaced",
    "n_eval_content",
    "n_pattern_trend",
    "n_discrepancy",
    "n_outcome_no_effect",
    "n_outcome_added_detail",
    "n_outcome_changed_assessment",
    "n_outcome_new_action_item",
    "group_read_shifted",
    "pushback",
    "minutes_on_resident",
    "duration_basis",
    "seconds_to_first_contribution",
]

_RESIDENT_SESSIONS_TEXT = ["room_raised_notes", "pushback_note", "closing_notes"]


def resident_sessions(conn, include_text=False):
    columns = list(_RESIDENT_SESSIONS_BASE)
    if include_text:
        columns += _RESIDENT_SESSIONS_TEXT
    rows = []
    for row in conn.execute(_RESIDENT_SESSIONS_SQL).fetchall():
        out = []
        for col in columns:
            out.append(_roles_pipe(row["roles_spoke"]) if col == "roles_spoke"
                       else ("" if row[col] is None else row[col]))
        rows.append(out)
    return _check_columns(columns, include_text), rows


# ---------------------------------------------------------------------------
# contributions.csv — one row per contribution
# ---------------------------------------------------------------------------

_CONTRIBUTIONS_SQL = """
SELECT
    l.study_code,
    s.meeting_date AS session_date,
    s.cycle_label,
    l.room_input_level,
    c.contribution_type,
    c.todo_status,
    c.outcome,
    c.retrieval_seconds,
    c.created_at   AS logged_at,
    c.detail
FROM ccc_contributions c
JOIN ccc_resident_logs l ON l.id = c.resident_log_id
JOIN ccc_sessions s ON s.id = l.session_id
WHERE c.voided = 0
ORDER BY s.meeting_date, s.created_at, l.study_code, c.created_at, c.id
"""

_CONTRIBUTIONS_BASE = [
    "study_code",
    "session_date",
    "cycle_label",
    "room_input_level",
    "contribution_type",
    "todo_status",
    "outcome",
    "retrieval_seconds",
    "logged_at",
]


def contributions(conn, include_text=False):
    columns = list(_CONTRIBUTIONS_BASE)
    if include_text:
        columns.append("detail")
    rows = [_select(row, columns) for row in conn.execute(_CONTRIBUTIONS_SQL).fetchall()]
    return _check_columns(columns, include_text), rows


# ---------------------------------------------------------------------------
# action-items.csv — the cycle-over-cycle recall experiment
# ---------------------------------------------------------------------------
#
# The spec's grain is one row per check. `include_unchecked=True` switches to a LEFT
# JOIN so items nobody revisited also appear (blank check columns, had_check=0) —
# that wider set is the denominator for a recall rate.

_ACTION_ITEMS_SQL = """
SELECT
    i.study_code,
    CASE WHEN i.item_text IS NOT NULL AND TRIM(i.item_text) != '' THEN 1 ELSE 0 END
        AS item_text_present,
    cs.cycle_label AS created_cycle,
    ss.cycle_label AS check_cycle,
    CASE WHEN k.id IS NULL THEN 0 ELSE 1 END AS had_check,
    k.recalled_by_room,
    k.surfaced_by_auc,
    k.checked_at,
    i.status AS current_status,
    i.item_text,
    i.owner
FROM ccc_action_items i
{join} ccc_action_item_checks k ON k.action_item_id = i.id
LEFT JOIN ccc_sessions cs ON cs.id = i.created_session_id
LEFT JOIN ccc_sessions ss ON ss.id = k.session_id
ORDER BY i.created_at, i.id, ss.meeting_date
"""

_ACTION_ITEMS_BASE = [
    "study_code",
    "item_text_present",
    "created_cycle",
    "check_cycle",
    "recalled_by_room",
    "surfaced_by_auc",
    "current_status",
]


def action_items(conn, include_text=False, include_unchecked=False):
    columns = list(_ACTION_ITEMS_BASE)
    if include_unchecked:
        columns.insert(columns.index("recalled_by_room"), "had_check")
    if include_text:
        columns += ["item_text", "owner"]
    sql = _ACTION_ITEMS_SQL.format(join="LEFT JOIN" if include_unchecked else "JOIN")
    rows = [_select(row, columns) for row in conn.execute(sql).fetchall()]
    return _check_columns(columns, include_text), rows


# ---------------------------------------------------------------------------
# Per-session JSON — backup and debugging
# ---------------------------------------------------------------------------

# Log columns safe to emit: everything except resident_id.
_LOG_JSON_COLUMNS = [
    "id", "session_id", "study_code", "pgy_year_at_session", "opened_at",
    "first_contribution_at", "closed_at", "room_input_level", "roles_spoke",
    "referenced_written_eval", "room_raised_notes", "group_read_shifted",
    "pushback", "pushback_note", "closing_notes",
]

_ITEM_JSON_COLUMNS = [
    "id", "study_code", "created_session_id", "item_text", "owner", "status",
    "created_at", "resolved_at",
]


def session_json(conn, session_id):
    """One meeting's captured data, keyed by study_code only."""
    session = dict(
        conn.execute("SELECT * FROM ccc_sessions WHERE id = ?", (session_id,)).fetchone()
    )

    logs = []
    for row in conn.execute(
        """SELECT * FROM ccc_resident_logs WHERE session_id = ?
           ORDER BY opened_at, id""",
        (session_id,),
    ).fetchall():
        log = {c: row[c] for c in _LOG_JSON_COLUMNS}
        log["roles_spoke"] = json.loads(row["roles_spoke"]) if row["roles_spoke"] else []
        log["contributions"] = [
            dict(c)
            for c in conn.execute(
                """SELECT id, created_at, contribution_type, todo_status, detail, outcome,
                          retrieval_seconds, voided
                   FROM ccc_contributions WHERE resident_log_id = ?
                   ORDER BY created_at, id""",
                (row["id"],),
            ).fetchall()
        ]
        logs.append(log)

    # Items created in this meeting, plus any item checked during it.
    items = [
        {c: row[c] for c in _ITEM_JSON_COLUMNS}
        for row in conn.execute(
            """SELECT DISTINCT i.* FROM ccc_action_items i
               LEFT JOIN ccc_action_item_checks k ON k.action_item_id = i.id
               WHERE i.created_session_id = ? OR k.session_id = ?
               ORDER BY i.created_at, i.id""",
            (session_id, session_id),
        ).fetchall()
    ]

    checks = [
        dict(row)
        for row in conn.execute(
            """SELECT k.id, k.action_item_id, k.session_id, k.checked_at,
                      k.recalled_by_room, k.surfaced_by_auc, i.study_code
               FROM ccc_action_item_checks k
               JOIN ccc_action_items i ON i.id = k.action_item_id
               WHERE k.session_id = ?
               ORDER BY k.checked_at, k.id""",
            (session_id,),
        ).fetchall()
    ]

    return {
        "schema": "auc.ccc.session.v1",
        "session": session,
        "resident_logs": logs,
        "action_items": items,
        "action_item_checks": checks,
    }
