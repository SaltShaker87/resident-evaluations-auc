"""
AUC — MedHub report import.

Imports MedHub's Evaluation Questions Summary (read by medhub_reports.py) into
medhub_answers: one row per evaluator's answer to one question.

Design notes that are load-bearing:

- **Two stateless steps.** The browser keeps the chosen file and sends it to
  /preview, which saves nothing, and then to /import. The import reads the file
  again and re-checks it against the database as it is at that moment, so a
  name linked or ignored, or a question mapped, between the two steps is what
  the import uses — there is no half-finished import held on the server.

- **Residents are matched only on their MedHub names**, each compared as one
  whole string (medhub_reports.name_key): the resident's MedHub name plus any
  other spellings linked to them (medhub_name_aliases), since reports carry
  typos. No first/last-name fallback, no partial match, and residents are
  never created. A name with no match is either linked by the user to a
  resident, ignored (remembered in medhub_ignored_names), or left out of this
  import and counted. The preview suggests likely residents for a name, but
  only the user's link makes it a match.

- **Every row has a fingerprint** (medhub_reports.fingerprint): resident +
  evaluator + form + rotation dates + question. Importing overlapping date
  ranges therefore never stores a row twice. If the same fingerprint arrives
  with a different answer, the preview shows it as changed in MedHub and the
  import saves the new answer.

- **Every question is mapped before anything is saved.** medhub_question_map
  gives each form's question a type — milestone rating, comment, or ignore —
  and a competency area. Rows of an ignored question are not stored. Changing
  a question's type later re-reads the answers already stored for it.

- **Blank and N/A comments are stored as markers, never as text**, so the
  per-resident counts survive overlapping imports without double counting.
  Only comments with text reach summaries.
"""

import hashlib
import json
import re
import uuid
from collections import Counter, OrderedDict
from difflib import SequenceMatcher
from typing import List, Literal, Optional

import medhub_api
import medhub_reports
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/api/medhub", tags=["medhub"])

# Set by app.py via init_medhub_import() before the app starts serving requests.
_db_connection = None
_competency_areas: List[str] = []

NO_SINGLE_COMPETENCY = "No single competency"
QUESTION_TYPES = ("milestone", "comment", "ignore")
MAX_FILE_BYTES = 25 * 1024 * 1024


def init_medhub_import(db_connection_factory, competency_areas):
    """Receive the database connection context manager and the six ACGME
    competency areas from app.py."""
    global _db_connection, _competency_areas
    _db_connection = db_connection_factory
    _competency_areas = list(competency_areas)


def competency_choices() -> List[str]:
    return _competency_areas + [NO_SINGLE_COMPETENCY]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
#
# app.py's init_db() owns schema creation; this module only supplies the SQL.
# Row ids are full uuid hex rather than the 8-character ids used elsewhere:
# a few years of evaluations is tens of thousands of answers, enough for
# 8-character ids to collide.

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS medhub_import_log (
        id TEXT PRIMARY KEY,
        imported_at TEXT DEFAULT (datetime('now')),
        file_name TEXT,
        report_type TEXT NOT NULL,
        forms TEXT,                         -- JSON: [{name, date_range}]
        report_generated TEXT,
        file_sha256 TEXT,
        rows_in_file INTEGER DEFAULT 0,
        new_rows INTEGER DEFAULT 0,
        duplicates_skipped INTEGER DEFAULT 0,
        changed_rows INTEGER DEFAULT 0,
        unmatched_names INTEGER DEFAULT 0,
        unmatched_rows INTEGER DEFAULT 0,
        ignored_name_rows INTEGER DEFAULT 0,
        ignored_question_rows INTEGER DEFAULT 0,
        details TEXT                        -- JSON: unmatched names, per-resident counts
    );

    CREATE TABLE IF NOT EXISTS medhub_question_map (
        form_name TEXT NOT NULL COLLATE NOCASE,
        question TEXT NOT NULL COLLATE NOCASE,
        question_type TEXT NOT NULL CHECK(question_type IN ('milestone', 'comment', 'ignore')),
        competency TEXT,                    -- NULL only for 'ignore'
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (form_name, question)
    );

    CREATE TABLE IF NOT EXISTS medhub_ignored_names (
        name_key TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ignored_at TEXT DEFAULT (datetime('now'))
    );

    -- Other spellings of a resident's MedHub name, e.g. a typo in one report.
    -- A name key is unique here and never also a residents.medhub_name_key.
    CREATE TABLE IF NOT EXISTS medhub_name_aliases (
        name_key TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        resident_id TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_medhub_name_aliases_resident ON medhub_name_aliases(resident_id);

    CREATE TABLE IF NOT EXISTS medhub_answers (
        id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL UNIQUE,
        resident_id TEXT NOT NULL,
        medhub_name TEXT NOT NULL,          -- as printed in the report
        evaluator TEXT NOT NULL,
        form_name TEXT NOT NULL,
        question TEXT NOT NULL,
        rotation_start TEXT NOT NULL,
        rotation_end TEXT NOT NULL,         -- the date used for graphs
        service TEXT,
        answer TEXT NOT NULL,
        -- milestone: rated | unable | blank | unrecognised
        -- comment:   text | na | blank
        answer_kind TEXT NOT NULL,
        level INTEGER CHECK(level BETWEEN 1 AND 5),   -- set only when 'rated'
        import_id TEXT NOT NULL,            -- the import that first stored it
        changed_import_id TEXT,             -- the last import that changed its answer
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_medhub_answers_resident ON medhub_answers(resident_id);
    CREATE INDEX IF NOT EXISTS idx_medhub_answers_question ON medhub_answers(form_name, question);
"""

COLUMN_MIGRATIONS = [
    # The name exactly as typed, and the form it is compared in.
    "ALTER TABLE residents ADD COLUMN medhub_name TEXT",
    "ALTER TABLE residents ADD COLUMN medhub_name_key TEXT",
]

POST_MIGRATION_SQL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_residents_medhub_name_key "
    "ON residents(medhub_name_key) WHERE medhub_name_key IS NOT NULL",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def classify(answer: str, question_type: str):
    """(answer_kind, level) for an answer to a question of this type."""
    if question_type == "milestone":
        return medhub_reports.milestone_level(answer)
    return medhub_reports.comment_kind(answer), None


def name_owner(conn, key: str):
    """The resident whose MedHub name or alias this name key is, or None."""
    return conn.execute(
        """SELECT id, first_name, last_name FROM residents WHERE medhub_name_key = ?
           UNION ALL
           SELECT r.id, r.first_name, r.last_name FROM medhub_name_aliases a
           JOIN residents r ON r.id = a.resident_id WHERE a.name_key = ?""",
        (key, key),
    ).fetchone()


def _refuse_if_taken(conn, key: str, display: str, resident_id: str):
    owner = name_owner(conn, key)
    if owner and owner["id"] != resident_id:
        raise HTTPException(
            status_code=409,
            detail=f'"{display}" is already {owner["first_name"]} {owner["last_name"]}\'s MedHub name.',
        )


def set_resident_medhub_name(conn, resident_id: str, name: Optional[str]) -> Optional[str]:
    """Set or clear a resident's MedHub name. Raises 409 if another resident
    already has it, as a name or an alias. Returns the stored display form.
    Used by the resident edit form and by linking a name in the import preview."""
    display = medhub_reports.display_name(name or "")
    key = display.casefold() or None
    if key:
        _refuse_if_taken(conn, key, display, resident_id)
        # Promoting one of their own aliases: it is the main name now.
        conn.execute("DELETE FROM medhub_name_aliases WHERE name_key = ?", (key,))
    conn.execute(
        "UPDATE residents SET medhub_name = ?, medhub_name_key = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (display or None, key, resident_id),
    )
    if key:
        # A name that now belongs to a resident is no longer one to ignore.
        conn.execute("DELETE FROM medhub_ignored_names WHERE name_key = ?", (key,))
    return display or None


def add_resident_alias(conn, resident_id: str, name: str) -> str:
    """Add another spelling of a resident's MedHub name. Raises 409 if another
    resident already has it. Adding the resident's own name is a no-op."""
    display = medhub_reports.display_name(name)
    key = display.casefold()
    _refuse_if_taken(conn, key, display, resident_id)
    conn.execute(
        "INSERT OR IGNORE INTO medhub_name_aliases (name_key, name, resident_id) "
        "SELECT ?, ?, ? WHERE NOT EXISTS "
        "(SELECT 1 FROM residents WHERE id = ? AND medhub_name_key = ?)",
        (key, display, resident_id, resident_id, key),
    )
    conn.execute("DELETE FROM medhub_ignored_names WHERE name_key = ?", (key,))
    return display


def resident_aliases(conn, resident_id: str) -> List[str]:
    return [
        r["name"] for r in conn.execute(
            "SELECT name FROM medhub_name_aliases WHERE resident_id = ? ORDER BY created_at, name",
            (resident_id,),
        )
    ]


def _similarity(a: str, b: str) -> float:
    """How alike two names read, 0–1, ignoring word order and punctuation, so
    "Bradbury, Sarah" is close to "Sarah Mae Bradbury" and to "Bradbery, Sara"."""
    def words(s):
        return sorted(re.findall(r"[^\W_]+", s.casefold()))
    wa, wb = words(a), words(b)
    whole = SequenceMatcher(None, " ".join(wa), " ".join(wb)).ratio()
    # Every word of the shorter name closely matches some word of the longer
    # one: a middle name or second surname present on only one side.
    short, long_ = sorted((wa, wb), key=len)
    if short and all(max(SequenceMatcher(None, w, x).ratio() for x in long_) >= 0.8 for w in short):
        whole = max(whole, 0.85)
    return whole


def _suggest(name: str, residents, aliases_by_resident, limit: int = 3) -> List[str]:
    """Ids of the residents a report name most likely means, best first."""
    scored = []
    for r in residents:
        candidates = [f'{r["last_name"]}, {r["first_name"]}', r["medhub_name"] or ""]
        candidates += aliases_by_resident.get(r["id"], [])
        score = max(_similarity(name, c) for c in candidates if c)
        if score >= 0.75:
            scored.append((score, r["id"]))
    scored.sort(key=lambda s: -s[0])
    return [rid for _score, rid in scored[:limit]]


def _question_map(conn) -> dict:
    rows = conn.execute("SELECT * FROM medhub_question_map").fetchall()
    return {(r["form_name"].casefold(), r["question"].casefold()): dict(r) for r in rows}


def _existing_answers(conn, fingerprints) -> dict:
    found = {}
    fps = list(fingerprints)
    for i in range(0, len(fps), 500):
        chunk = fps[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT fingerprint, answer FROM medhub_answers WHERE fingerprint IN ({marks})", chunk
        ):
            found[r["fingerprint"]] = r["answer"]
    return found


def _resident_label(r) -> str:
    return f'{r["first_name"]} {r["last_name"]}'


async def _read_upload(file: UploadFile):
    name = file.filename or "report.html"
    if not name.lower().endswith((".html", ".htm")):
        raise HTTPException(
            status_code=400,
            detail="Choose the report saved from MedHub as a web page (.html).",
        )
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="That file is too large to be a MedHub report.")
    try:
        report = medhub_reports.read_report(data)
    except medhub_reports.MedhubReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return name, data, report


# ---------------------------------------------------------------------------
# Analysis — the one place that decides what happens to each row
# ---------------------------------------------------------------------------

def analyse(conn, report: medhub_reports.Report) -> dict:
    """Sort every row of the report into what an import would do with it,
    given the database as it is now. Saves nothing."""
    roster = {
        r["name_key"]: r
        for r in conn.execute(
            """SELECT id, first_name, last_name, medhub_name, medhub_name_key AS name_key
               FROM residents WHERE medhub_name_key IS NOT NULL
               UNION ALL
               SELECT r.id, r.first_name, r.last_name, r.medhub_name, a.name_key
               FROM medhub_name_aliases a JOIN residents r ON r.id = a.resident_id"""
        )
    }
    ignored = {r["name_key"] for r in conn.execute("SELECT name_key FROM medhub_ignored_names")}
    qmap = _question_map(conn)
    fingerprints = [medhub_reports.fingerprint(row) for row in report.rows]
    existing = _existing_answers(conn, set(fingerprints))

    to_insert, to_change = [], []
    counts = Counter()
    unmatched = OrderedDict()           # name -> rows
    ignored_in_file = OrderedDict()     # name -> rows
    unmapped = OrderedDict()            # (form, question) -> answers seen
    unrecognised = Counter()            # (form, question, answer) -> count
    per_resident = OrderedDict()        # resident_id -> counts
    seen = set()

    for row, fp in zip(report.rows, fingerprints, strict=True):
        key = medhub_reports.name_key(row.resident)
        resident = roster.get(key)
        is_ignored = resident is None and key in ignored
        mapping = qmap.get((row.form.casefold(), row.question.casefold()))
        # Unmapped questions are gathered from unmatched names too, so the
        # whole form can be mapped in one pass before any name is linked.
        if mapping is None and not is_ignored:
            unmapped.setdefault((row.form, row.question), []).append(row.answer)

        if resident is None:
            target = ignored_in_file if is_ignored else unmatched
            target[row.resident] = target.get(row.resident, 0) + 1
            counts["ignored_name_rows" if is_ignored else "unmatched_rows"] += 1
            continue
        if mapping is None:
            counts["unmapped_rows"] += 1
            continue
        if mapping["question_type"] == "ignore":
            counts["ignored_question_rows"] += 1
            continue

        stats = per_resident.setdefault(resident["id"], {
            "resident_id": resident["id"],
            "resident_name": _resident_label(resident),
            "medhub_names": [],        # as spelled in this file
            "new": 0, "duplicates": 0, "changed": 0, "blank": 0, "na": 0,
        })
        if row.resident not in stats["medhub_names"]:
            stats["medhub_names"].append(row.resident)
        kind, level = classify(row.answer, mapping["question_type"])
        if mapping["question_type"] == "comment" and kind in ("blank", "na"):
            stats[kind] += 1
        if kind == "unrecognised":
            unrecognised[(row.form, row.question, row.answer)] += 1

        if fp in seen:
            counts["duplicates"] += 1
            stats["duplicates"] += 1
            continue
        seen.add(fp)
        if fp not in existing:
            counts["new"] += 1
            stats["new"] += 1
            to_insert.append((fp, resident["id"], row, kind, level))
        elif existing[fp] != row.answer:
            counts["changed"] += 1
            stats["changed"] += 1
            to_change.append((fp, row, kind, level, existing[fp]))
        else:
            counts["duplicates"] += 1
            stats["duplicates"] += 1

    residents = conn.execute(
        "SELECT id, first_name, last_name, pgy_year, status, medhub_name FROM residents "
        "ORDER BY CASE WHEN status IN ('active', 'chief') THEN 0 ELSE 1 END, "
        "last_name COLLATE NOCASE, first_name COLLATE NOCASE"
    ).fetchall()
    aliases_by_resident = {}
    for a in conn.execute("SELECT resident_id, name FROM medhub_name_aliases ORDER BY created_at, name"):
        aliases_by_resident.setdefault(a["resident_id"], []).append(a["name"])

    blocking = None
    if not report.rows:
        blocking = "This report has no evaluations in it."
    elif unmapped:
        blocking = "Map every question below before saving."

    return {
        "report_type": report.report_type,
        "generated": report.generated,
        "forms": [{"name": f.name, "date_range": f.date_range} for f in report.forms],
        "rows_in_file": len(report.rows),
        "counts": {
            k: counts[k] for k in (
                "new", "duplicates", "changed", "unmatched_rows", "ignored_name_rows",
                "ignored_question_rows", "unmapped_rows",
            )
        },
        "residents": list(per_resident.values()),
        "not_in_roster": [
            {"name": n, "rows": c, "suggestions": _suggest(n, residents, aliases_by_resident)}
            for n, c in unmatched.items()
        ],
        "ignored_names_in_file": [{"name": n, "rows": c} for n, c in ignored_in_file.items()],
        "unmapped_questions": [
            {
                "form_name": form,
                "question": question,
                "suggested_type": "milestone" if medhub_reports.looks_like_milestone(answers) else None,
                "example_answers": list(OrderedDict.fromkeys(a for a in answers if a))[:3],
            }
            for (form, question), answers in unmapped.items()
        ],
        "unrecognised_answers": [
            {"form_name": f, "question": q, "answer": a, "count": c}
            for (f, q, a), c in unrecognised.items()
        ],
        "changed": [
            {
                "medhub_name": row.resident, "evaluator": row.evaluator,
                "form_name": row.form, "question": row.question,
                "rotation_start": row.rotation_start, "rotation_end": row.rotation_end,
                "old_answer": old, "new_answer": row.answer,
            }
            for _fp, row, _k, _l, old in to_change
        ],
        "roster": [
            {
                "id": r["id"], "name": _resident_label(r), "pgy_year": r["pgy_year"],
                "status": r["status"], "medhub_name": r["medhub_name"],
                "aliases": aliases_by_resident.get(r["id"], []),
            }
            for r in residents
        ],
        "competencies": competency_choices(),
        "can_save": blocking is None,
        "blocking_reason": blocking,
        "_to_insert": to_insert,
        "_to_change": to_change,
    }


def _public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Import endpoints
# ---------------------------------------------------------------------------

@router.post("/preview")
async def preview_report(file: UploadFile = File(...)):
    """What importing this file would do. Saves nothing."""
    name, _data, report = await _read_upload(file)
    with _db_connection() as conn:
        result = analyse(conn, report)
    return {"file_name": name, **_public(result)}


@router.post("/import")
async def import_report(file: UploadFile = File(...)):
    """Save the new and changed rows of this file and log the import."""
    name, data, report = await _read_upload(file)
    with _db_connection() as conn:
        result = analyse(conn, report)
        if not result["can_save"]:
            raise HTTPException(status_code=409, detail=result["blocking_reason"])

        import_id = uuid.uuid4().hex
        conn.executemany(
            """INSERT INTO medhub_answers
               (id, fingerprint, resident_id, medhub_name, evaluator, form_name, question,
                rotation_start, rotation_end, service, answer, answer_kind, level, import_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (uuid.uuid4().hex, fp, resident_id, row.resident, row.evaluator, row.form,
                 row.question, row.rotation_start, row.rotation_end, row.service, row.answer,
                 kind, level, import_id)
                for fp, resident_id, row, kind, level in result["_to_insert"]
            ],
        )
        conn.executemany(
            """UPDATE medhub_answers
               SET answer = ?, answer_kind = ?, level = ?, service = ?,
                   changed_import_id = ?, updated_at = datetime('now')
               WHERE fingerprint = ?""",
            [
                (row.answer, kind, level, row.service, import_id, fp)
                for fp, row, kind, level, _old in result["_to_change"]
            ],
        )

        counts = result["counts"]
        details = {
            "not_in_roster": result["not_in_roster"],
            "ignored_names": result["ignored_names_in_file"],
            "residents": result["residents"],
        }
        conn.execute(
            """INSERT INTO medhub_import_log
               (id, file_name, report_type, forms, report_generated, file_sha256,
                rows_in_file, new_rows, duplicates_skipped, changed_rows, unmatched_names,
                unmatched_rows, ignored_name_rows, ignored_question_rows, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                import_id, name, result["report_type"], json.dumps(result["forms"]),
                result["generated"], hashlib.sha256(data).hexdigest(), result["rows_in_file"],
                counts["new"], counts["duplicates"], counts["changed"],
                len(result["not_in_roster"]), counts["unmatched_rows"],
                counts["ignored_name_rows"], counts["ignored_question_rows"], json.dumps(details),
            ),
        )
    return {"import_id": import_id, "file_name": name, **_public(result)}


@router.get("/import-log")
def import_log(limit: int = Query(100, ge=1, le=1000)):
    with _db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM medhub_import_log ORDER BY imported_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    entries = []
    for r in rows:
        entry = dict(r)
        entry["forms"] = json.loads(entry["forms"] or "[]")
        entry["details"] = json.loads(entry["details"] or "{}")
        entries.append(entry)
    return entries


@router.get("/status")
def medhub_status():
    with _db_connection() as conn:
        totals = conn.execute(
            """SELECT
                 SUM(CASE WHEN m.question_type = 'milestone' THEN 1 ELSE 0 END) AS ratings,
                 SUM(CASE WHEN m.question_type = 'comment' AND a.answer_kind = 'text'
                          THEN 1 ELSE 0 END) AS comments
               FROM medhub_answers a
               JOIN medhub_question_map m
                 ON m.form_name = a.form_name AND m.question = a.question"""
        ).fetchone()
        last = conn.execute(
            "SELECT imported_at, file_name FROM medhub_import_log "
            "ORDER BY imported_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    return {
        "ratings": totals["ratings"] or 0,
        "comments": totals["comments"] or 0,
        "last_import": dict(last) if last else None,
        "api_configured": medhub_api.is_configured(),
    }


# ---------------------------------------------------------------------------
# Names: link to a resident, or ignore
# ---------------------------------------------------------------------------

class NameLink(BaseModel):
    name: str
    resident_id: str


class IgnoredName(BaseModel):
    name: str


@router.post("/link-name")
def link_name(data: NameLink):
    """"This is → resident": the name becomes the resident's MedHub name, or,
    if they already have one, another spelling of it. Their existing name is
    never replaced from here, so a slip of the dropdown only adds a spelling,
    which can be removed on the resident's page."""
    with _db_connection() as conn:
        resident = conn.execute(
            "SELECT medhub_name_key FROM residents WHERE id = ?", (data.resident_id,)
        ).fetchone()
        if not resident:
            raise HTTPException(status_code=404, detail="Resident not found")
        if not medhub_reports.name_key(data.name):
            raise HTTPException(status_code=400, detail="A MedHub name is required")
        if resident["medhub_name_key"]:
            return {"alias": add_resident_alias(conn, data.resident_id, data.name)}
        return {"medhub_name": set_resident_medhub_name(conn, data.resident_id, data.name)}


@router.delete("/aliases")
def remove_alias(name: str = Query(...)):
    """Unlink another spelling. Evaluations already imported under it stay
    with the resident; later reports with that spelling come up unmatched."""
    with _db_connection() as conn:
        conn.execute(
            "DELETE FROM medhub_name_aliases WHERE name_key = ?", (medhub_reports.name_key(name),)
        )
    return {"message": "Spelling removed"}


@router.get("/ignored-names")
def list_ignored_names():
    with _db_connection() as conn:
        rows = conn.execute(
            "SELECT name, name_key, ignored_at FROM medhub_ignored_names ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/ignored-names")
def ignore_name(data: IgnoredName):
    display = medhub_reports.display_name(data.name)
    if not display:
        raise HTTPException(status_code=400, detail="A MedHub name is required")
    with _db_connection() as conn:
        owner = name_owner(conn, display.casefold())
        if owner:
            raise HTTPException(
                status_code=409,
                detail=f'"{display}" is {owner["first_name"]} {owner["last_name"]}\'s MedHub name.',
            )
        conn.execute(
            "INSERT OR IGNORE INTO medhub_ignored_names (name_key, name) VALUES (?, ?)",
            (display.casefold(), display),
        )
    return {"name": display}


@router.delete("/ignored-names")
def unignore_name(name: str = Query(...)):
    with _db_connection() as conn:
        conn.execute(
            "DELETE FROM medhub_ignored_names WHERE name_key = ?", (medhub_reports.name_key(name),)
        )
    return {"message": "No longer ignored"}


# ---------------------------------------------------------------------------
# Question mapping
# ---------------------------------------------------------------------------

class QuestionMapping(BaseModel):
    form_name: str
    question: str
    question_type: Literal["milestone", "comment", "ignore"]
    competency: Optional[str] = None


class QuestionMappingBatch(BaseModel):
    items: List[QuestionMapping]


@router.get("/question-map")
def list_question_map():
    with _db_connection() as conn:
        rows = conn.execute(
            """SELECT m.*, (SELECT COUNT(*) FROM medhub_answers a
                            WHERE a.form_name = m.form_name AND a.question = m.question) AS stored
               FROM medhub_question_map m
               ORDER BY m.form_name COLLATE NOCASE, m.created_at, m.rowid"""
        ).fetchall()
    return {"items": [dict(r) for r in rows], "competencies": competency_choices()}


@router.put("/question-map")
def save_question_map(data: QuestionMappingBatch):
    """Add or change mappings. Answers already stored for a question whose
    type changes are re-read under the new type."""
    choices = competency_choices()
    with _db_connection() as conn:
        for item in data.items:
            form = " ".join(item.form_name.split())
            question = " ".join(item.question.split())
            if not form or not question:
                raise HTTPException(status_code=400, detail="Form and question are required")
            competency = item.competency
            if item.question_type == "ignore":
                competency = None
            elif competency not in choices:
                raise HTTPException(
                    status_code=400,
                    detail=f'Choose a competency area for "{question[:80]}"',
                )
            before = conn.execute(
                "SELECT question_type FROM medhub_question_map WHERE form_name = ? AND question = ?",
                (form, question),
            ).fetchone()
            conn.execute(
                """INSERT INTO medhub_question_map (form_name, question, question_type, competency)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(form_name, question) DO UPDATE SET
                     question_type = excluded.question_type,
                     competency = excluded.competency,
                     updated_at = datetime('now')""",
                (form, question, item.question_type, competency),
            )
            if before and before["question_type"] != item.question_type \
                    and item.question_type != "ignore":
                stored = conn.execute(
                    "SELECT id, answer FROM medhub_answers WHERE form_name = ? AND question = ?",
                    (form, question),
                ).fetchall()
                conn.executemany(
                    "UPDATE medhub_answers SET answer_kind = ?, level = ? WHERE id = ?",
                    [(*classify(r["answer"], item.question_type), r["id"]) for r in stored],
                )
    return {"saved": len(data.items)}


# ---------------------------------------------------------------------------
# One resident's MedHub data, for the resident page
# ---------------------------------------------------------------------------

def resident_comments(conn, resident_id: str) -> list:
    """Comments with text, oldest rotation first. What summaries read."""
    rows = conn.execute(
        """SELECT a.evaluator, a.form_name, a.question, a.rotation_start, a.rotation_end,
                  a.service, a.answer, m.competency
           FROM medhub_answers a
           JOIN medhub_question_map m ON m.form_name = a.form_name AND m.question = a.question
           WHERE a.resident_id = ? AND m.question_type = 'comment' AND a.answer_kind = 'text'
           ORDER BY a.rotation_end, a.evaluator, a.question""",
        (resident_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/residents/{resident_id}")
def resident_medhub(resident_id: str):
    with _db_connection() as conn:
        if not conn.execute("SELECT 1 FROM residents WHERE id = ?", (resident_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Resident not found")
        ratings = conn.execute(
            """SELECT a.evaluator, a.form_name, a.question, a.rotation_start, a.rotation_end,
                      a.service, a.answer, a.answer_kind, a.level, m.competency
               FROM medhub_answers a
               JOIN medhub_question_map m ON m.form_name = a.form_name AND m.question = a.question
               WHERE a.resident_id = ? AND m.question_type = 'milestone'
               ORDER BY a.rotation_end, a.evaluator, a.question""",
            (resident_id,),
        ).fetchall()
        markers = conn.execute(
            """SELECT a.answer_kind, COUNT(*) AS n
               FROM medhub_answers a
               JOIN medhub_question_map m ON m.form_name = a.form_name AND m.question = a.question
               WHERE a.resident_id = ? AND m.question_type = 'comment'
                 AND a.answer_kind IN ('blank', 'na')
               GROUP BY a.answer_kind""",
            (resident_id,),
        ).fetchall()
        comments = resident_comments(conn, resident_id)
    marker_counts = {r["answer_kind"]: r["n"] for r in markers}
    return {
        "ratings": [dict(r) for r in ratings],
        "comments": comments,
        "blank_comments": marker_counts.get("blank", 0),
        "na_comments": marker_counts.get("na", 0),
        "competencies": _competency_areas,
        "levels": {label.title(): n for label, n in medhub_reports.MILESTONE_LEVELS.items()},
    }
