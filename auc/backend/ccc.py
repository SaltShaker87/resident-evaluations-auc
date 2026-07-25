"""
AUC — CCC (Clinical Competency Committee) meeting capture.

Instrumentation for a QI study measuring what the committee's spontaneous recall
misses: what the room raises on its own, what the operator has to surface, whether
surfacing it changed the group's assessment, how long retrieval took, and — cycle
over cycle — whether prior action items are remembered unprompted.

Design notes that are load-bearing:

- **Client-generated ids.** Ids are 8-char TEXT primary keys, and the frontend mints
  them before it POSTs. Every insert is `INSERT OR IGNORE`, so a double click and a
  retry-after-lost-response are both no-ops, and the frontend knows a row's PATCH
  path before its POST has landed (which is what lets its offline outbox be a plain
  FIFO instead of a dependency graph).

- **`resident_id` is `ON DELETE SET NULL`, not CASCADE.** Removing a resident must
  not silently destroy study data mid-study. `study_code` and `pgy_year_at_session`
  are snapshotted onto the CCC rows at write time so the exports stay complete (and
  de-identified) after the resident record is gone. `DELETE /api/residents/{id}` is
  unchanged and still succeeds.

- **`study_code` is the only resident identifier that may leave this app.** See
  `ccc_export.py`, which refuses to emit a forbidden column.
"""

import json
import uuid
from datetime import date
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import ccc_export

router = APIRouter(prefix="/api/ccc", tags=["ccc"])

# Set by app.py via init_ccc() before the app starts serving requests.
_db_connection = None


def init_ccc(db_connection_factory):
    """Receive the database connection context manager from app.py."""
    global _db_connection
    _db_connection = db_connection_factory


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
#
# app.py's init_db() owns schema creation; this module only supplies the SQL.
# Every CHECK passes on NULL, which is what makes "all fields optional" true —
# a partially filled log is valid data.

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS ccc_sessions (
        id TEXT PRIMARY KEY,
        meeting_date TEXT,
        cycle_label TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        closed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS ccc_resident_logs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        resident_id TEXT,                       -- nullable on purpose: ON DELETE SET NULL
        study_code TEXT,                        -- snapshot: survives resident deletion
        pgy_year_at_session INTEGER,            -- snapshot: survives advancement + deletion
        opened_at TEXT DEFAULT (datetime('now')),
        first_contribution_at TEXT,
        closed_at TEXT,
        room_input_level TEXT CHECK(room_input_level IN ('none', 'thin', 'substantial')),
        roles_spoke TEXT,                       -- JSON array, canonicalised on write
        referenced_written_eval INTEGER,
        room_raised_notes TEXT,
        group_read_shifted INTEGER,
        pushback INTEGER,
        pushback_note TEXT,
        closing_notes TEXT,
        FOREIGN KEY (session_id) REFERENCES ccc_sessions(id) ON DELETE CASCADE,
        FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE SET NULL
    );
    -- One log per resident per session. Also what makes get-or-create safe under a
    -- double click. SQLite treats NULLs as distinct, so this still behaves after a
    -- resident is deleted and their resident_id goes NULL.
    CREATE UNIQUE INDEX IF NOT EXISTS idx_ccc_log_unique
        ON ccc_resident_logs(session_id, resident_id);

    CREATE TABLE IF NOT EXISTS ccc_contributions (
        id TEXT PRIMARY KEY,
        resident_log_id TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        contribution_type TEXT CHECK(contribution_type IN
            ('todo_surfaced', 'eval_content', 'pattern_trend', 'discrepancy')),
        todo_status TEXT CHECK(todo_status IN ('open', 'done', 'not_relevant')),
        detail TEXT,
        outcome TEXT CHECK(outcome IN
            ('no_effect', 'added_detail', 'changed_assessment', 'new_action_item')),
        retrieval_seconds INTEGER,
        voided INTEGER DEFAULT 0,               -- one-tap undo for a mis-tapped card
        FOREIGN KEY (resident_log_id) REFERENCES ccc_resident_logs(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_ccc_contrib_log
        ON ccc_contributions(resident_log_id);

    CREATE TABLE IF NOT EXISTS ccc_action_items (
        id TEXT PRIMARY KEY,
        resident_id TEXT,
        study_code TEXT,
        created_session_id TEXT,
        item_text TEXT,                         -- not `text`: safer in a dynamic UPDATE
        owner TEXT,
        status TEXT CHECK(status IN ('open', 'done', 'no_longer_relevant')) DEFAULT 'open',
        created_at TEXT DEFAULT (datetime('now')),
        resolved_at TEXT,
        FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE SET NULL,
        FOREIGN KEY (created_session_id) REFERENCES ccc_sessions(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ccc_item_resident
        ON ccc_action_items(resident_id, status);

    CREATE TABLE IF NOT EXISTS ccc_action_item_checks (
        id TEXT PRIMARY KEY,
        action_item_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        checked_at TEXT DEFAULT (datetime('now')),
        recalled_by_room INTEGER DEFAULT 0,
        surfaced_by_auc INTEGER DEFAULT 0,
        FOREIGN KEY (action_item_id) REFERENCES ccc_action_items(id) ON DELETE CASCADE,
        FOREIGN KEY (session_id) REFERENCES ccc_sessions(id) ON DELETE CASCADE
    );
    -- One check row per item per session; the endpoint ORs the two flags into it, so
    -- tapping "Room remembered" and then "I surfaced it" records both.
    CREATE UNIQUE INDEX IF NOT EXISTS idx_ccc_check_unique
        ON ccc_action_item_checks(action_item_id, session_id);
"""

# Joins app.py's existing try/except ALTER loop.
COLUMN_MIGRATIONS = [
    "ALTER TABLE residents ADD COLUMN study_code TEXT",
]

# Runs after COLUMN_MIGRATIONS, in the same try/except style.
POST_MIGRATION_SQL = [
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_residents_study_code
       ON residents(study_code) WHERE study_code IS NOT NULL""",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROLES = ("continuity_preceptor", "inpatient_attending", "chief", "pd", "other")

Role = Literal["continuity_preceptor", "inpatient_attending", "chief", "pd", "other"]
RoomInputLevel = Literal["none", "thin", "substantial"]
ContributionType = Literal["todo_surfaced", "eval_content", "pattern_trend", "discrepancy"]
TodoStatus = Literal["open", "done", "not_relevant"]
Outcome = Literal["no_effect", "added_detail", "changed_assessment", "new_action_item"]
ItemStatus = Literal["open", "done", "no_longer_relevant"]

# Fields arriving as JSON booleans that are stored as SQLite 0/1.
BOOL_FIELDS = {
    "referenced_written_eval",
    "group_read_shifted",
    "pushback",
    "voided",
    "recalled_by_room",
    "surfaced_by_auc",
}


def _new_id() -> str:
    return str(uuid.uuid4())[:8]


def _canonical_roles(roles) -> str:
    """Dedupe and fix the order, so exported pipe-delimited strings group cleanly."""
    chosen = set(roles or [])
    return json.dumps([r for r in ROLES if r in chosen])


def _patch(conn, table: str, row_id: str, updates: BaseModel, rename: dict = None) -> None:
    """Partial UPDATE from a Pydantic model.

    Same shape as app.py's update_resident: field names come from the model (so they
    cannot be attacker-controlled) and values are bound.
    """
    rename = rename or {}
    fields, values = [], []
    for field, val in updates.dict(exclude_unset=True).items():
        if field in BOOL_FIELDS:
            val = 1 if val else 0
        elif field == "roles_spoke":
            val = _canonical_roles(val)
        fields.append(f"{rename.get(field, field)} = ?")
        values.append(val)
    if not fields:
        return
    values.append(row_id)
    conn.execute(f"UPDATE {table} SET {', '.join(fields)} WHERE id = ?", values)


def _ensure_study_code(conn, resident_id: str) -> Optional[str]:
    """Return this resident's study code, minting the next one if they lack it.

    Codes are handed out in the order residents are first discussed in a CCC —
    deliberately not alphabetical, so the sequence doesn't leak name ordering to
    anyone holding an export.
    """
    row = conn.execute(
        "SELECT study_code FROM residents WHERE id = ?", (resident_id,)
    ).fetchone()
    if row is None:
        return None
    if row["study_code"]:
        return row["study_code"]

    # The high-water mark has to include codes on CCC rows, not just on residents.
    # A deleted resident's row is gone but their code survives on their logs and
    # action items, and handing it out again would silently merge two people in the
    # exports.
    highest = conn.execute(
        """SELECT COALESCE(MAX(n), 0) AS n FROM (
               SELECT CAST(SUBSTR(study_code, 2) AS INTEGER) AS n FROM residents
                   WHERE study_code LIKE 'R___'
               UNION ALL
               SELECT CAST(SUBSTR(study_code, 2) AS INTEGER) FROM ccc_resident_logs
                   WHERE study_code LIKE 'R___'
               UNION ALL
               SELECT CAST(SUBSTR(study_code, 2) AS INTEGER) FROM ccc_action_items
                   WHERE study_code LIKE 'R___'
           )"""
    ).fetchone()["n"]
    code = f"R{highest + 1:03d}"
    conn.execute(
        "UPDATE residents SET study_code = ? WHERE id = ? AND study_code IS NULL",
        (code, resident_id),
    )
    # Re-read: if a concurrent request won the race, that one is authoritative.
    return conn.execute(
        "SELECT study_code FROM residents WHERE id = ?", (resident_id,)
    ).fetchone()["study_code"]


def _ensure_all_study_codes(conn) -> None:
    """Give every resident who appears anywhere in the CCC data a study code.

    Belt-and-braces before an export: codes are normally minted when a resident's log
    or action item is first created, so this only catches rows written before that was
    true, or a resident whose code was cleared by hand.
    """
    rows = conn.execute(
        """SELECT DISTINCT r.id FROM residents r
           WHERE r.study_code IS NULL
             AND (EXISTS (SELECT 1 FROM ccc_resident_logs l WHERE l.resident_id = r.id)
               OR EXISTS (SELECT 1 FROM ccc_action_items i WHERE i.resident_id = r.id))
           ORDER BY r.created_at, r.id"""
    ).fetchall()
    for row in rows:
        _ensure_study_code(conn, row["id"])
    if rows:
        # Backfill the snapshots on rows written before their resident had a code.
        conn.execute(
            """UPDATE ccc_resident_logs SET study_code = (
                   SELECT study_code FROM residents WHERE residents.id = ccc_resident_logs.resident_id)
               WHERE study_code IS NULL AND resident_id IS NOT NULL"""
        )
        conn.execute(
            """UPDATE ccc_action_items SET study_code = (
                   SELECT study_code FROM residents WHERE residents.id = ccc_action_items.resident_id)
               WHERE study_code IS NULL AND resident_id IS NOT NULL"""
        )


def _row(conn, table: str, row_id: str, what: str = "Row"):
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return row


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SessionCreate(BaseModel):
    meeting_date: Optional[str] = None
    cycle_label: Optional[str] = None


class ResidentLogOpen(BaseModel):
    id: Optional[str] = None            # client id, honoured only on a real insert
    session_id: str
    resident_id: str


class ResidentLogUpdate(BaseModel):
    room_input_level: Optional[RoomInputLevel] = None
    roles_spoke: Optional[List[Role]] = None
    referenced_written_eval: Optional[bool] = None
    room_raised_notes: Optional[str] = None
    group_read_shifted: Optional[bool] = None
    pushback: Optional[bool] = None
    pushback_note: Optional[str] = None
    closing_notes: Optional[str] = None


class ContributionCreate(BaseModel):
    id: Optional[str] = None
    resident_log_id: str
    contribution_type: Optional[ContributionType] = None
    todo_status: Optional[TodoStatus] = None
    detail: Optional[str] = None
    outcome: Optional[Outcome] = None
    retrieval_seconds: Optional[int] = None


class ContributionUpdate(BaseModel):
    contribution_type: Optional[ContributionType] = None
    todo_status: Optional[TodoStatus] = None
    detail: Optional[str] = None
    outcome: Optional[Outcome] = None
    retrieval_seconds: Optional[int] = None
    voided: Optional[bool] = None


class ActionItemCreate(BaseModel):
    id: Optional[str] = None
    resident_id: str
    session_id: Optional[str] = None
    text: Optional[str] = None          # maps to the item_text column
    owner: Optional[str] = None


class ActionItemUpdate(BaseModel):
    text: Optional[str] = None          # maps to the item_text column
    owner: Optional[str] = None
    status: Optional[ItemStatus] = None


class ActionItemCheckCreate(BaseModel):
    id: Optional[str] = None
    action_item_id: str
    session_id: str
    recalled_by_room: Optional[bool] = None
    surfaced_by_auc: Optional[bool] = None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _active_session_row(conn):
    return conn.execute(
        """SELECT * FROM ccc_sessions WHERE closed_at IS NULL
           ORDER BY created_at DESC, id DESC LIMIT 1"""
    ).fetchone()


@router.post("/sessions")
def create_session(data: SessionCreate):
    """Start a meeting. If one is already open, return it rather than opening a second.

    That keeps "the active session" single-valued (without which /sessions/active is
    nondeterministic) and makes a double-clicked Start button harmless.
    """
    with _db_connection() as conn:
        existing = _active_session_row(conn)
        if existing:
            result = dict(existing)
            result["existing"] = True
            return result

        sid = _new_id()
        conn.execute(
            "INSERT INTO ccc_sessions (id, meeting_date, cycle_label) VALUES (?, ?, ?)",
            (sid, data.meeting_date or date.today().isoformat(), data.cycle_label),
        )
        result = dict(_row(conn, "ccc_sessions", sid, "Session"))
        result["existing"] = False
        return result


@router.get("/sessions/active")
def get_active_session():
    """The open session, or JSON null.

    Deliberately 200-with-null rather than 404: the frontend's request() helper throws
    on any non-2xx, so a 404 here would surface an error toast on every page load.
    """
    with _db_connection() as conn:
        row = _active_session_row(conn)
    return dict(row) if row else None


@router.get("/sessions")
def list_sessions():
    with _db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM ccc_sessions ORDER BY meeting_date DESC, created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/sessions/{session_id}/close")
def close_session(session_id: str):
    with _db_connection() as conn:
        _row(conn, "ccc_sessions", session_id, "Session")
        conn.execute(
            """UPDATE ccc_sessions SET closed_at = COALESCE(closed_at, datetime('now'))
               WHERE id = ?""",
            (session_id,),
        )
        return dict(_row(conn, "ccc_sessions", session_id, "Session"))


# ---------------------------------------------------------------------------
# Resident logs
# ---------------------------------------------------------------------------

@router.post("/resident-logs")
def open_resident_log(data: ResidentLogOpen):
    """Get-or-create the log for this session+resident.

    Returns the canonical row, which may already exist under a different id (the
    resident was discussed earlier in the same meeting, or the client retried with a
    fresh id). The frontend remaps any queued writes onto the id it gets back.
    """
    with _db_connection() as conn:
        resident = conn.execute(
            "SELECT pgy_year FROM residents WHERE id = ?", (data.resident_id,)
        ).fetchone()
        if resident is None:
            raise HTTPException(status_code=404, detail="Resident not found")
        _row(conn, "ccc_sessions", data.session_id, "Session")

        code = _ensure_study_code(conn, data.resident_id)
        conn.execute(
            """INSERT OR IGNORE INTO ccc_resident_logs
               (id, session_id, resident_id, study_code, pgy_year_at_session)
               VALUES (?, ?, ?, ?, ?)""",
            (
                data.id or _new_id(),
                data.session_id,
                data.resident_id,
                code,
                resident["pgy_year"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM ccc_resident_logs WHERE session_id = ? AND resident_id = ?",
            (data.session_id, data.resident_id),
        ).fetchone()
        result = dict(row)
        result["contributions"] = [
            dict(c)
            for c in conn.execute(
                """SELECT * FROM ccc_contributions
                   WHERE resident_log_id = ? AND voided = 0
                   ORDER BY created_at, id""",
                (row["id"],),
            ).fetchall()
        ]
        return result


@router.patch("/resident-logs/{log_id}")
def update_resident_log(log_id: str, updates: ResidentLogUpdate):
    with _db_connection() as conn:
        _row(conn, "ccc_resident_logs", log_id, "Resident log")
        _patch(conn, "ccc_resident_logs", log_id, updates)
        return dict(_row(conn, "ccc_resident_logs", log_id, "Resident log"))


@router.post("/resident-logs/{log_id}/close")
def close_resident_log(log_id: str):
    with _db_connection() as conn:
        _row(conn, "ccc_resident_logs", log_id, "Resident log")
        conn.execute(
            """UPDATE ccc_resident_logs SET closed_at = COALESCE(closed_at, datetime('now'))
               WHERE id = ?""",
            (log_id,),
        )
        return dict(_row(conn, "ccc_resident_logs", log_id, "Resident log"))


# ---------------------------------------------------------------------------
# Contributions
# ---------------------------------------------------------------------------

@router.post("/contributions")
def create_contribution(data: ContributionCreate):
    """Create a contribution, stamping the log's first_contribution_at on first insert.

    INSERT OR IGNORE on the client-supplied id: a double click or a retry after a lost
    response leaves exactly one row, and neither re-stamps first_contribution_at.
    """
    cid = data.id or _new_id()
    todo_status = data.todo_status if data.contribution_type == "todo_surfaced" else None
    with _db_connection() as conn:
        _row(conn, "ccc_resident_logs", data.resident_log_id, "Resident log")
        cur = conn.execute(
            """INSERT OR IGNORE INTO ccc_contributions
               (id, resident_log_id, contribution_type, todo_status, detail, outcome,
                retrieval_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                cid,
                data.resident_log_id,
                data.contribution_type,
                todo_status,
                data.detail,
                data.outcome,
                data.retrieval_seconds,
            ),
        )
        if cur.rowcount:
            conn.execute(
                """UPDATE ccc_resident_logs
                   SET first_contribution_at = COALESCE(first_contribution_at, datetime('now'))
                   WHERE id = ?""",
                (data.resident_log_id,),
            )
        return dict(_row(conn, "ccc_contributions", cid, "Contribution"))


@router.patch("/contributions/{contribution_id}")
def update_contribution(contribution_id: str, updates: ContributionUpdate):
    """Partial update — the card is filled in over several taps, each autosaved."""
    with _db_connection() as conn:
        _row(conn, "ccc_contributions", contribution_id, "Contribution")
        _patch(conn, "ccc_contributions", contribution_id, updates)
        # todo_status only means anything for a surfaced prior to-do.
        conn.execute(
            """UPDATE ccc_contributions SET todo_status = NULL
               WHERE id = ?
                 AND contribution_type IS NOT NULL
                 AND contribution_type != 'todo_surfaced'""",
            (contribution_id,),
        )
        return dict(_row(conn, "ccc_contributions", contribution_id, "Contribution"))


# ---------------------------------------------------------------------------
# Action items
# ---------------------------------------------------------------------------

@router.get("/residents/{resident_id}/action-items")
def list_action_items(
    resident_id: str,
    status: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
):
    """Action items for a resident.

    Pass session_id to get this session's check state alongside each item, so the
    drawer's checkmark-and-dim survives closing the drawer or reloading the page.
    """
    sql = """SELECT i.*,
                    c.id AS check_id, c.recalled_by_room, c.surfaced_by_auc, c.checked_at
             FROM ccc_action_items i
             LEFT JOIN ccc_action_item_checks c
                    ON c.action_item_id = i.id AND c.session_id = ?
             WHERE i.resident_id = ?"""
    params = [session_id, resident_id]
    if status:
        sql += " AND i.status = ?"
        params.append(status)
    sql += " ORDER BY i.created_at, i.id"
    with _db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.post("/action-items")
def create_action_item(data: ActionItemCreate):
    iid = data.id or _new_id()
    with _db_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM residents WHERE id = ?", (data.resident_id,)
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="Resident not found")
        code = _ensure_study_code(conn, data.resident_id)
        conn.execute(
            """INSERT OR IGNORE INTO ccc_action_items
               (id, resident_id, study_code, created_session_id, item_text, owner)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (iid, data.resident_id, code, data.session_id, data.text, data.owner),
        )
        return dict(_row(conn, "ccc_action_items", iid, "Action item"))


@router.patch("/action-items/{item_id}")
def update_action_item(item_id: str, updates: ActionItemUpdate):
    with _db_connection() as conn:
        _row(conn, "ccc_action_items", item_id, "Action item")
        _patch(conn, "ccc_action_items", item_id, updates, rename={"text": "item_text"})
        if updates.status is not None:
            if updates.status == "open":
                conn.execute(
                    "UPDATE ccc_action_items SET resolved_at = NULL WHERE id = ?",
                    (item_id,),
                )
            else:
                conn.execute(
                    """UPDATE ccc_action_items
                       SET resolved_at = COALESCE(resolved_at, datetime('now'))
                       WHERE id = ?""",
                    (item_id,),
                )
        return dict(_row(conn, "ccc_action_items", item_id, "Action item"))


@router.post("/action-item-checks")
def upsert_action_item_check(data: ActionItemCheckCreate):
    """Record whether the room recalled a prior item and/or the operator surfaced it.

    Both buttons post here, so the flags are ORed into a single row per item per
    session — tapping "Room remembered" then "I surfaced it" records both rather than
    the second overwriting the first. Written as select-then-update so it needs no
    particular SQLite version.
    """
    recalled = 1 if data.recalled_by_room else 0
    surfaced = 1 if data.surfaced_by_auc else 0
    with _db_connection() as conn:
        _row(conn, "ccc_action_items", data.action_item_id, "Action item")
        _row(conn, "ccc_sessions", data.session_id, "Session")
        existing = conn.execute(
            """SELECT * FROM ccc_action_item_checks
               WHERE action_item_id = ? AND session_id = ?""",
            (data.action_item_id, data.session_id),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE ccc_action_item_checks
                   SET recalled_by_room = MAX(recalled_by_room, ?),
                       surfaced_by_auc = MAX(surfaced_by_auc, ?)
                   WHERE id = ?""",
                (recalled, surfaced, existing["id"]),
            )
            check_id = existing["id"]
        else:
            check_id = data.id or _new_id()
            conn.execute(
                """INSERT INTO ccc_action_item_checks
                   (id, action_item_id, session_id, recalled_by_room, surfaced_by_auc)
                   VALUES (?, ?, ?, ?, ?)""",
                (check_id, data.action_item_id, data.session_id, recalled, surfaced),
            )
        return dict(_row(conn, "ccc_action_item_checks", check_id, "Check"))


# ---------------------------------------------------------------------------
# Study data
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats():
    """Totals to date, for the Study Data page."""
    with _db_connection() as conn:
        return ccc_export.totals(conn)


@router.get("/export/session/{session_id}")
def export_session(session_id: str):
    """Everything captured in one meeting, as JSON, for backup and debugging.

    Identified by study_code only — same rule as the CSVs, so this file is safe to
    hand to anyone.
    """
    with _db_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM ccc_sessions WHERE id = ?", (session_id,)
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="Session not found")
        _ensure_all_study_codes(conn)
        return ccc_export.session_json(conn, session_id)


@router.get("/export/resident-sessions.csv")
def export_resident_sessions(include_text: bool = False):
    with _db_connection() as conn:
        _ensure_all_study_codes(conn)
        header, rows = ccc_export.resident_sessions(conn, include_text)
    return ccc_export.csv_response("ccc-resident-sessions", header, rows)


@router.get("/export/contributions.csv")
def export_contributions(include_text: bool = False):
    with _db_connection() as conn:
        _ensure_all_study_codes(conn)
        header, rows = ccc_export.contributions(conn, include_text)
    return ccc_export.csv_response("ccc-contributions", header, rows)


@router.get("/export/action-items.csv")
def export_action_items(include_text: bool = False, include_unchecked: bool = False):
    with _db_connection() as conn:
        _ensure_all_study_codes(conn)
        header, rows = ccc_export.action_items(conn, include_text, include_unchecked)
    return ccc_export.csv_response("ccc-action-items", header, rows)
