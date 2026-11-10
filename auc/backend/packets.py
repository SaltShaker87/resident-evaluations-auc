"""
AUC — advisor packets for the CCC study.

An advisor packet is a PDF about one advisee, built from an AI draft of their
MedHub comments in one review period (review_period.py), for an advisor to read
before filling in the Advisee Report Out Form. This module holds:

- **Meetings**, one per date, each with a cutoff date (7 days before by default).
- **Presenters**: the advisors presenting at a meeting. Adding one brings in their
  active advisees as **assignments**, one row per resident per meeting.
- **Study groups**: packet_first, packet_delayed or none, per assignment.
  "Randomize within each advisor" assigns them once per meeting from a seed drawn
  from the operating system's secure random source. It can never be re-run, a
  change by hand afterwards needs a reason and leaves randomized_group untouched,
  and every change is logged in packet_events.
- **Generation**: a queue worked one resident at a time in the background, so it
  survives a page reload. One failure does not stop the rest.
- **Packets**: one row per generated packet, with everything needed to re-render
  the same PDF and to analyse the study (ccc_export.packets).

Nothing here sends anything to anyone. The operator downloads the files.
See CCC.md, "Advisor packets", for every table and the rules in full.
"""

import asyncio
import io
import json
import random
import re
import secrets
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone
from typing import List, Literal, Optional

import ccc
import config
import pdf_export
import rag_retrieval
import retrieval_engine
import review_period
import summary_builder
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/packets", tags=["packets"])

# Bump on ANY change to packet layout, prompts, or generation settings. Printed in
# every packet's footer and stored on every packets row. During the randomized phase
# of the study it must not change; CCC.md says what to record if it has to.
PACKET_FORMAT_VERSION = "1"

GROUP_FIRST = "packet_first"
GROUP_DELAYED = "packet_delayed"
GROUP_NONE = "none"
GROUPS = (GROUP_FIRST, GROUP_DELAYED, GROUP_NONE)

_db_connection = None
_app_version = None


def init_packets(db_connection_factory, app_version_fn):
    global _db_connection, _app_version
    _db_connection = db_connection_factory
    _app_version = app_version_fn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS packet_meetings (
        id TEXT PRIMARY KEY,
        meeting_date TEXT NOT NULL UNIQUE,
        cutoff_date TEXT NOT NULL,
        randomization_seed TEXT,            -- drawn by the app, never typed
        randomized_at TEXT,                 -- set once; the button is then disabled
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS packet_presenters (
        id TEXT PRIMARY KEY,
        meeting_id TEXT NOT NULL REFERENCES packet_meetings(id),
        advisor_id TEXT REFERENCES advisors(id) ON DELETE SET NULL,
        advisor_code TEXT,
        randomization_seed TEXT,            -- the seed this advisor's split used
        randomized_at TEXT,
        added_at TEXT DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_packet_presenter_unique
        ON packet_presenters(meeting_id, advisor_id);

    CREATE TABLE IF NOT EXISTS packet_assignments (
        id TEXT PRIMARY KEY,
        meeting_id TEXT NOT NULL REFERENCES packet_meetings(id),
        resident_id TEXT REFERENCES residents(id) ON DELETE SET NULL,
        study_code TEXT,
        advisor_id TEXT REFERENCES advisors(id) ON DELETE SET NULL,
        advisor_code TEXT,
        study_group TEXT CHECK(study_group IN ('packet_first', 'packet_delayed', 'none')),
        randomized_group TEXT,              -- never overwritten; NULL when set by hand
        randomization_seed TEXT,            -- NULL when set by hand
        randomized_at TEXT,
        added_late INTEGER DEFAULT 0,
        group_change_reason TEXT,
        group_changed_at TEXT,
        override_start TEXT,
        override_end TEXT,
        status TEXT DEFAULT 'idle'
            CHECK(status IN ('idle', 'queued', 'running', 'done', 'failed')),
        status_detail TEXT,
        requested_model TEXT,
        queued_at TEXT,
        packet_id TEXT,                     -- the packet in force for this row
        moved_from_meeting_date TEXT,
        moved_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_packet_assignment_unique
        ON packet_assignments(meeting_id, resident_id);

    CREATE TABLE IF NOT EXISTS packets (
        id TEXT PRIMARY KEY,
        summary_id TEXT,
        meeting_id TEXT,
        assignment_id TEXT,
        resident_id TEXT REFERENCES residents(id) ON DELETE SET NULL,
        study_code TEXT,
        advisor_id TEXT REFERENCES advisors(id) ON DELETE SET NULL,
        advisor_code TEXT,
        advisor_name TEXT,                  -- printed on the packet; never exported
        meeting_date TEXT,
        cutoff_date TEXT,
        study_group TEXT,
        randomized_group TEXT,
        randomization_seed TEXT,
        group_change_reason TEXT,
        group_changed_at TEXT,
        period_start TEXT,
        period_end TEXT,
        period_basis TEXT,
        n_comments INTEGER,
        n_evaluators INTEGER,
        n_rotations INTEGER,
        n_late INTEGER,
        medhub_import_id TEXT,              -- the MedHub snapshot it was built from
        pgy_year_at_cutoff INTEGER,
        model TEXT,
        app_version TEXT,
        retrieval_engine TEXT,
        packet_format_version TEXT,
        show_levels INTEGER DEFAULT 0,
        generated_at TEXT DEFAULT (datetime('now')),
        first_downloaded_at TEXT,
        withheld INTEGER DEFAULT 0,
        withheld_reason TEXT,
        withheld_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_packets_resident ON packets(resident_id);

    -- Everything that happens to a group, a packet or a presenter, in order.
    CREATE TABLE IF NOT EXISTS packet_events (
        id TEXT PRIMARY KEY,
        at TEXT DEFAULT (datetime('now')),
        meeting_id TEXT,
        assignment_id TEXT,
        packet_id TEXT,
        advisor_id TEXT,
        event TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        reason TEXT
    );
"""

COLUMN_MIGRATIONS = [
    "ALTER TABLE advisors ADD COLUMN in_study INTEGER DEFAULT 1",
    "ALTER TABLE advisors ADD COLUMN advisor_code TEXT",
]

POST_MIGRATION_SQL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_advisors_advisor_code "
    "ON advisors(advisor_code) WHERE advisor_code IS NOT NULL",
]


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _id():
    return uuid.uuid4().hex[:8]


def _event(conn, event, *, meeting_id=None, assignment_id=None, packet_id=None,
           advisor_id=None, old=None, new=None, reason=None):
    conn.execute(
        """INSERT INTO packet_events
           (id, meeting_id, assignment_id, packet_id, advisor_id, event, old_value, new_value, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, meeting_id, assignment_id, packet_id, advisor_id, event,
         old, new, reason),
    )


def recover_interrupted():
    """A restart ends the generation queue. Say so on every row it left behind,
    rather than leaving them "running" forever."""
    if _db_connection is None:
        return
    with _db_connection() as conn:
        conn.execute(
            "UPDATE packet_assignments SET status = 'failed', status_detail = ? "
            "WHERE status IN ('queued', 'running')",
            ("Interrupted: the app restarted before this packet was finished. Generate it again.",),
        )


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------

def ensure_advisor_code(conn, advisor_id) -> Optional[str]:
    """This advisor's stable study code (A01, A02, …), minted on first use.

    Minted like study codes: in the order advisors first appear in the study, never
    reused, counting codes that survive only on packet rows.
    """
    row = conn.execute("SELECT advisor_code FROM advisors WHERE id = ?", (advisor_id,)).fetchone()
    if row is None:
        return None
    if row["advisor_code"]:
        return row["advisor_code"]
    highest = conn.execute(
        """SELECT COALESCE(MAX(n), 0) AS n FROM (
               SELECT CAST(SUBSTR(advisor_code, 2) AS INTEGER) AS n FROM advisors
                   WHERE advisor_code LIKE 'A%'
               UNION ALL
               SELECT CAST(SUBSTR(advisor_code, 2) AS INTEGER) FROM packet_assignments
                   WHERE advisor_code LIKE 'A%'
               UNION ALL
               SELECT CAST(SUBSTR(advisor_code, 2) AS INTEGER) FROM packets
                   WHERE advisor_code LIKE 'A%'
           )"""
    ).fetchone()["n"]
    conn.execute(
        "UPDATE advisors SET advisor_code = ? WHERE id = ? AND advisor_code IS NULL",
        (f"A{highest + 1:02d}", advisor_id),
    )
    return conn.execute(
        "SELECT advisor_code FROM advisors WHERE id = ?", (advisor_id,)
    ).fetchone()["advisor_code"]


# ---------------------------------------------------------------------------
# Randomization — pure, so it can be re-checked from the stored seed
# ---------------------------------------------------------------------------

def draw_seed() -> str:
    """A seed from the operating system's secure random source. Never chosen."""
    return str(secrets.randbits(64))


def split_advisees(seed, advisor_code, study_codes) -> dict:
    """{study_code: group} for one advisor's advisees.

    Sort by study code, shuffle with Python's random.Random seeded with
    "<seed>:<advisor code>", the first half are packet_first and the rest
    packet_delayed. With an odd number, a coin flip from the same generator decides
    which group gets the extra resident. The same seed and the same residents always
    give the same answer.
    """
    rng = random.Random(f"{seed}:{advisor_code}")
    codes = sorted(study_codes)
    rng.shuffle(codes)
    first_n = len(codes) // 2
    if len(codes) % 2 and rng.random() < 0.5:
        first_n += 1
    return {code: GROUP_FIRST if i < first_n else GROUP_DELAYED for i, code in enumerate(codes)}


def coin_flip(seed, study_code) -> str:
    """The group for one resident added after their advisor was randomized."""
    return GROUP_FIRST if random.Random(f"{seed}:{study_code}").random() < 0.5 else GROUP_DELAYED


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def _meeting(conn, meeting_id):
    row = conn.execute("SELECT * FROM packet_meetings WHERE id = ?", (meeting_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return row


def _assignment(conn, assignment_id):
    row = conn.execute(
        "SELECT * FROM packet_assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Resident not found in this meeting")
    return row


def _packet(conn, packet_id):
    row = conn.execute("SELECT * FROM packets WHERE id = ?", (packet_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Packet not found")
    return row


def _get_or_create_meeting(conn, meeting_date, cutoff_date=None):
    meeting_date = review_period.parse_date(meeting_date, "meeting date")
    row = conn.execute(
        "SELECT * FROM packet_meetings WHERE meeting_date = ?", (meeting_date.isoformat(),)
    ).fetchone()
    if row:
        return row
    cutoff = (review_period.parse_date(cutoff_date, "cutoff date") if cutoff_date
              else review_period.default_cutoff(meeting_date))
    if cutoff > meeting_date:
        raise review_period.PeriodError("The cutoff date cannot be after the meeting.")
    mid = _id()
    conn.execute(
        "INSERT INTO packet_meetings (id, meeting_date, cutoff_date) VALUES (?, ?, ?)",
        (mid, meeting_date.isoformat(), cutoff.isoformat()),
    )
    return _meeting(conn, mid)


def _advisees(conn, advisor_id):
    return conn.execute(
        "SELECT * FROM residents WHERE advisor_id = ? AND active = 1 "
        "ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE",
        (advisor_id,),
    ).fetchall()


def _add_assignment(conn, meeting, presenter, resident, late=False):
    """One resident into a meeting. A resident whose advisor was already randomized
    gets a fresh coin flip from a new seed; outside the study they get "No packet"."""
    code = ccc._ensure_study_code(conn, resident["id"])
    aid = _id()
    conn.execute(
        """INSERT INTO packet_assignments
           (id, meeting_id, resident_id, study_code, advisor_id, advisor_code, added_late)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (aid, meeting["id"], resident["id"], code, presenter["advisor_id"],
         presenter["advisor_code"], 1 if late else 0),
    )
    if presenter["randomized_at"]:
        advisor = conn.execute(
            "SELECT in_study FROM advisors WHERE id = ?", (presenter["advisor_id"],)
        ).fetchone()
        if advisor and advisor["in_study"]:
            seed = draw_seed()
            group = coin_flip(seed, code)
            conn.execute(
                """UPDATE packet_assignments SET study_group = ?, randomized_group = ?,
                   randomization_seed = ?, randomized_at = ? WHERE id = ?""",
                (group, group, seed, _now(), aid),
            )
            _event(conn, "randomized", meeting_id=meeting["id"], assignment_id=aid,
                   advisor_id=presenter["advisor_id"], new=group,
                   reason="Added after randomization: coin flip from a new seed")
        else:
            conn.execute(
                "UPDATE packet_assignments SET study_group = 'none', randomized_at = ? "
                "WHERE id = ?", (_now(), aid),
            )
            _event(conn, "group_set", meeting_id=meeting["id"], assignment_id=aid,
                   advisor_id=presenter["advisor_id"], new=GROUP_NONE,
                   reason="Advisor is outside the study")
    return aid


def _randomize_presenter(conn, meeting, presenter, seed):
    """Assign one presenter's advisees: split evenly if the advisor is in the study,
    "No packet" if not. Rows already randomized are left alone."""
    advisor = conn.execute(
        "SELECT in_study FROM advisors WHERE id = ?", (presenter["advisor_id"],)
    ).fetchone()
    rows = conn.execute(
        "SELECT * FROM packet_assignments WHERE meeting_id = ? AND advisor_id = ? "
        "AND randomized_at IS NULL",
        (meeting["id"], presenter["advisor_id"]),
    ).fetchall()
    now = _now()
    if advisor is None or not advisor["in_study"]:
        for r in rows:
            conn.execute(
                "UPDATE packet_assignments SET study_group = 'none', randomized_at = ? WHERE id = ?",
                (now, r["id"]),
            )
            _event(conn, "group_set", meeting_id=meeting["id"], assignment_id=r["id"],
                   advisor_id=presenter["advisor_id"], old=r["study_group"], new=GROUP_NONE,
                   reason="Advisor is outside the study")
    else:
        groups = split_advisees(seed, presenter["advisor_code"], [r["study_code"] for r in rows])
        for r in rows:
            group = groups[r["study_code"]]
            conn.execute(
                """UPDATE packet_assignments SET study_group = ?, randomized_group = ?,
                   randomization_seed = ?, randomized_at = ? WHERE id = ?""",
                (group, group, seed, now, r["id"]),
            )
            _event(conn, "randomized", meeting_id=meeting["id"], assignment_id=r["id"],
                   advisor_id=presenter["advisor_id"], new=group)
    conn.execute(
        "UPDATE packet_presenters SET randomization_seed = ?, randomized_at = ? WHERE id = ?",
        (seed if advisor and advisor["in_study"] else None, now, presenter["id"]),
    )


# ---------------------------------------------------------------------------
# Meeting state, for the Packets page
# ---------------------------------------------------------------------------

def _last_name(full_name):
    parts = (full_name or "").split()
    return parts[-1] if parts else ""


def _file_part(text):
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9-]+", "", ascii_text.replace(" ", "-")) or "x"


def packet_filename(advisor_name, resident_last_name, cutoff):
    return (f"packet-{_file_part(_last_name(advisor_name))}-"
            f"{_file_part(resident_last_name)}-{cutoff}.pdf")


def _preview(conn, meeting, row, resident):
    """The period and counts a packet generated now would have."""
    try:
        per = review_period.compute_period(
            conn, row["resident_id"], meeting["cutoff_date"],
            override_start=row["override_start"], override_end=row["override_end"],
            exclude_meeting_id=meeting["id"],
        )
    except review_period.PeriodError as e:
        return {"error": str(e)}
    snap = review_period.latest_import(conn)
    counts = review_period.load_evidence(
        conn, row["resident_id"], per, review_period.SCOPE_MEDHUB_ONLY,
        snap["id"] if snap else None,
    )["counts"]
    pgy = review_period.pgy_at(conn, resident, meeting["cutoff_date"])
    return {
        "period_start": per["period_start"], "period_end": per["period_end"],
        "period_basis": per["period_basis"],
        "basis_description": review_period.BASIS_DESCRIPTIONS[per["period_basis"]],
        "counts": counts, "pgy_year_at_cutoff": pgy["pgy_year"], "pgy_note": pgy["note"],
        "thin_evidence": counts["n_comments"] < config.PACKET_THIN_EVIDENCE,
    }


def meeting_state(conn, meeting_id) -> dict:
    meeting = _meeting(conn, meeting_id)
    presenters = conn.execute(
        """SELECT p.*, a.name AS advisor_name, a.in_study, a.active AS advisor_active
           FROM packet_presenters p LEFT JOIN advisors a ON a.id = p.advisor_id
           WHERE p.meeting_id = ? ORDER BY a.name COLLATE NOCASE""",
        (meeting_id,),
    ).fetchall()
    rows = conn.execute(
        """SELECT pa.*, r.first_name, r.last_name, r.pgy_year, r.start_date
           FROM packet_assignments pa LEFT JOIN residents r ON r.id = pa.resident_id
           WHERE pa.meeting_id = ?
           ORDER BY r.last_name COLLATE NOCASE, r.first_name COLLATE NOCASE""",
        (meeting_id,),
    ).fetchall()

    by_advisor = {}
    for r in rows:
        item = {k: r[k] for k in r.keys() if k not in ("start_date",)}
        packet = conn.execute("SELECT * FROM packets WHERE id = ?", (r["packet_id"],)).fetchone() \
            if r["packet_id"] else None
        item["packet"] = dict(packet) if packet else None
        if packet:
            item["filename"] = packet_filename(
                packet["advisor_name"], r["last_name"] or "resident", packet["cutoff_date"]
            )
        if r["resident_id"] and r["first_name"] is not None:
            resident = {"id": r["resident_id"], "pgy_year": r["pgy_year"],
                        "start_date": r["start_date"]}
            item["preview"] = _preview(conn, meeting, r, resident)
        else:
            item["preview"] = None
        by_advisor.setdefault(r["advisor_id"], []).append(item)

    presenter_list = []
    for p in presenters:
        present = {r["resident_id"] for r in by_advisor.get(p["advisor_id"], [])}
        missing = [
            {"id": a["id"], "first_name": a["first_name"], "last_name": a["last_name"]}
            for a in (_advisees(conn, p["advisor_id"]) if p["advisor_id"] else [])
            if a["id"] not in present
        ]
        presenter_list.append({
            **dict(p),
            "residents": by_advisor.get(p["advisor_id"], []),
            "advisees_not_in_meeting": missing,
        })

    statuses = {}
    for r in rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    return {
        "meeting": dict(meeting),
        "presenters": presenter_list,
        "status_counts": statuses,
        "staleness_warning": review_period.staleness_warning(conn, meeting["cutoff_date"]),
        "settings": {
            "lead_days": config.PACKET_LEAD_DAYS,
            "thin_evidence": config.PACKET_THIN_EVIDENCE,
            "show_levels": config.PACKET_SHOW_LEVELS,
            "packet_format_version": PACKET_FORMAT_VERSION,
        },
    }


# ---------------------------------------------------------------------------
# Meeting endpoints
# ---------------------------------------------------------------------------

class MeetingCreate(BaseModel):
    meeting_date: str
    cutoff_date: Optional[str] = None


class MeetingUpdate(BaseModel):
    cutoff_date: str


class PresentersAdd(BaseModel):
    advisor_ids: List[str]


class PresenterMove(BaseModel):
    meeting_date: str


class AssignmentAdd(BaseModel):
    resident_id: str


class GroupChange(BaseModel):
    study_group: Optional[Literal["packet_first", "packet_delayed", "none"]] = None
    reason: Optional[str] = None


class PeriodOverride(BaseModel):
    override_start: Optional[str] = None
    override_end: Optional[str] = None


class GenerateRequest(BaseModel):
    assignment_ids: Optional[List[str]] = None
    model: Optional[str] = None


class Withhold(BaseModel):
    reason: str


def _period_error(fn):
    try:
        return fn()
    except review_period.PeriodError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/defaults")
def defaults(meeting_date: Optional[str] = None):
    """The cutoff a meeting date would get, and the settings in force."""
    out = {
        "lead_days": config.PACKET_LEAD_DAYS,
        "academic_year_start": config.ACADEMIC_YEAR_START,
        "packet_format_version": PACKET_FORMAT_VERSION,
    }
    if meeting_date:
        out["cutoff_date"] = _period_error(
            lambda: review_period.default_cutoff(meeting_date).isoformat()
        )
    return out


@router.get("/meetings")
def list_meetings():
    with _db_connection() as conn:
        rows = conn.execute(
            """SELECT m.*,
                      (SELECT COUNT(*) FROM packet_presenters p WHERE p.meeting_id = m.id)
                          AS presenters,
                      (SELECT COUNT(*) FROM packet_assignments a WHERE a.meeting_id = m.id)
                          AS residents
               FROM packet_meetings m ORDER BY m.meeting_date DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/meetings")
def create_meeting(data: MeetingCreate):
    """Get or create the meeting on this date."""
    with _db_connection() as conn:
        meeting = _period_error(
            lambda: _get_or_create_meeting(conn, data.meeting_date, data.cutoff_date)
        )
        return meeting_state(conn, meeting["id"])


@router.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: str):
    with _db_connection() as conn:
        return meeting_state(conn, meeting_id)


@router.put("/meetings/{meeting_id}")
def update_meeting(meeting_id: str, data: MeetingUpdate):
    """Change the cutoff. Packets already generated keep the cutoff they were built
    with; the page marks them as built for a different cutoff."""
    with _db_connection() as conn:
        meeting = _meeting(conn, meeting_id)
        cutoff = _period_error(lambda: review_period.parse_date(data.cutoff_date, "cutoff date"))
        if cutoff.isoformat() > meeting["meeting_date"]:
            raise HTTPException(status_code=400, detail="The cutoff date cannot be after the meeting.")
        if conn.execute(
            "SELECT 1 FROM packet_assignments WHERE meeting_id = ? AND status IN ('queued', 'running')",
            (meeting_id,),
        ).fetchone():
            raise HTTPException(status_code=409, detail="Wait for generation to finish first.")
        conn.execute("UPDATE packet_meetings SET cutoff_date = ? WHERE id = ?",
                     (cutoff.isoformat(), meeting_id))
        _event(conn, "cutoff_changed", meeting_id=meeting_id, old=meeting["cutoff_date"],
               new=cutoff.isoformat())
        return meeting_state(conn, meeting_id)


@router.post("/meetings/{meeting_id}/presenters")
def add_presenters(meeting_id: str, data: PresentersAdd):
    """Add presenting advisors and their active advisees. An advisor added after the
    meeting was randomized gets their own one-time even split, from a new seed."""
    with _db_connection() as conn:
        meeting = _meeting(conn, meeting_id)
        for advisor_id in data.advisor_ids:
            advisor = conn.execute(
                "SELECT * FROM advisors WHERE id = ?", (advisor_id,)
            ).fetchone()
            if advisor is None or not advisor["active"]:
                raise HTTPException(status_code=400, detail="No active advisor with that id")
            if conn.execute(
                "SELECT 1 FROM packet_presenters WHERE meeting_id = ? AND advisor_id = ?",
                (meeting_id, advisor_id),
            ).fetchone():
                continue
            code = ensure_advisor_code(conn, advisor_id)
            pid = _id()
            conn.execute(
                "INSERT INTO packet_presenters (id, meeting_id, advisor_id, advisor_code) "
                "VALUES (?, ?, ?, ?)",
                (pid, meeting_id, advisor_id, code),
            )
            _event(conn, "presenter_added", meeting_id=meeting_id, advisor_id=advisor_id)
            presenter = conn.execute(
                "SELECT * FROM packet_presenters WHERE id = ?", (pid,)
            ).fetchone()
            for resident in _advisees(conn, advisor_id):
                if conn.execute(
                    "SELECT 1 FROM packet_assignments WHERE meeting_id = ? AND resident_id = ?",
                    (meeting_id, resident["id"]),
                ).fetchone():
                    continue
                _add_assignment(conn, meeting, presenter, resident)
            if meeting["randomized_at"]:
                _randomize_presenter(conn, meeting, presenter, draw_seed())
        return meeting_state(conn, meeting_id)


@router.delete("/meetings/{meeting_id}/presenters/{advisor_id}")
def remove_presenter(meeting_id: str, advisor_id: str):
    """Only before anything about them is study data: no randomized group and no
    packet. After that, move them to another meeting instead."""
    with _db_connection() as conn:
        _meeting(conn, meeting_id)
        rows = conn.execute(
            "SELECT * FROM packet_assignments WHERE meeting_id = ? AND advisor_id = ?",
            (meeting_id, advisor_id),
        ).fetchall()
        if any(r["randomized_at"] or r["packet_id"] or r["status"] in ("queued", "running")
               for r in rows):
            raise HTTPException(
                status_code=409,
                detail="This advisor's advisees already have groups or packets. Move the "
                       "advisor to another meeting date instead of removing them.",
            )
        conn.execute("DELETE FROM packet_assignments WHERE meeting_id = ? AND advisor_id = ?",
                     (meeting_id, advisor_id))
        conn.execute("DELETE FROM packet_presenters WHERE meeting_id = ? AND advisor_id = ?",
                     (meeting_id, advisor_id))
        _event(conn, "presenter_removed", meeting_id=meeting_id, advisor_id=advisor_id)
        return meeting_state(conn, meeting_id)


@router.post("/meetings/{meeting_id}/presenters/{advisor_id}/move")
def move_presenter(meeting_id: str, advisor_id: str, data: PresenterMove):
    """Move an advisor's presentation to another meeting date. Every advisee keeps
    the group already assigned; the old and new dates are recorded. Not a group
    change, so no reason is needed."""
    with _db_connection() as conn:
        source = _meeting(conn, meeting_id)
        presenter = conn.execute(
            "SELECT * FROM packet_presenters WHERE meeting_id = ? AND advisor_id = ?",
            (meeting_id, advisor_id),
        ).fetchone()
        if presenter is None:
            raise HTTPException(status_code=404, detail="That advisor is not presenting at this meeting")
        target = _period_error(lambda: _get_or_create_meeting(conn, data.meeting_date))
        if target["id"] == source["id"]:
            return meeting_state(conn, meeting_id)
        if conn.execute(
            "SELECT 1 FROM packet_presenters WHERE meeting_id = ? AND advisor_id = ?",
            (target["id"], advisor_id),
        ).fetchone():
            raise HTTPException(status_code=409, detail="That advisor is already presenting on that date.")
        rows = conn.execute(
            "SELECT * FROM packet_assignments WHERE meeting_id = ? AND advisor_id = ?",
            (meeting_id, advisor_id),
        ).fetchall()
        if any(r["status"] in ("queued", "running") for r in rows):
            raise HTTPException(status_code=409, detail="Wait for generation to finish first.")
        for r in rows:
            if r["resident_id"] and conn.execute(
                "SELECT 1 FROM packet_assignments WHERE meeting_id = ? AND resident_id = ?",
                (target["id"], r["resident_id"]),
            ).fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="One of this advisor's advisees is already in the meeting on that date.",
                )

        now = _now()
        conn.execute("UPDATE packet_presenters SET meeting_id = ? WHERE id = ?",
                     (target["id"], presenter["id"]))
        for r in rows:
            # A packet built for the old cutoff no longer matches this meeting, so the
            # row needs a new one; the old packet stays in the packets table.
            conn.execute(
                """UPDATE packet_assignments SET meeting_id = ?, moved_from_meeting_date = ?,
                   moved_at = ?, packet_id = NULL, status = 'idle', status_detail = NULL
                   WHERE id = ?""",
                (target["id"], source["meeting_date"], now, r["id"]),
            )
            _event(conn, "moved", meeting_id=target["id"], assignment_id=r["id"],
                   advisor_id=advisor_id, old=source["meeting_date"], new=target["meeting_date"])
        # Arriving unrandomized in a meeting that has been randomized: a late advisor.
        if target["randomized_at"] and not presenter["randomized_at"]:
            moved = conn.execute(
                "SELECT * FROM packet_presenters WHERE id = ?", (presenter["id"],)
            ).fetchone()
            _randomize_presenter(conn, target, moved, draw_seed())
        return meeting_state(conn, target["id"])


@router.post("/meetings/{meeting_id}/assignments")
def add_resident(meeting_id: str, data: AssignmentAdd):
    """Add one resident to a meeting, under their current advisor, who must be
    presenting. After randomization this is a late addition: a fresh coin flip."""
    with _db_connection() as conn:
        meeting = _meeting(conn, meeting_id)
        resident = conn.execute(
            "SELECT * FROM residents WHERE id = ?", (data.resident_id,)
        ).fetchone()
        if resident is None:
            raise HTTPException(status_code=404, detail="Resident not found")
        presenter = conn.execute(
            "SELECT * FROM packet_presenters WHERE meeting_id = ? AND advisor_id = ?",
            (meeting_id, resident["advisor_id"]),
        ).fetchone()
        if presenter is None:
            raise HTTPException(status_code=400,
                                detail="This resident's advisor is not presenting at this meeting.")
        if conn.execute(
            "SELECT 1 FROM packet_assignments WHERE meeting_id = ? AND resident_id = ?",
            (meeting_id, data.resident_id),
        ).fetchone():
            raise HTTPException(status_code=409, detail="This resident is already in the meeting.")
        aid = _add_assignment(conn, meeting, presenter, resident,
                              late=bool(presenter["randomized_at"]))
        _event(conn, "resident_added", meeting_id=meeting_id, assignment_id=aid,
               advisor_id=resident["advisor_id"])
        return meeting_state(conn, meeting_id)


@router.post("/meetings/{meeting_id}/randomize")
def randomize(meeting_id: str):
    """Randomize within each advisor. Once per meeting; there is no re-roll."""
    with _db_connection() as conn:
        meeting = _meeting(conn, meeting_id)
        if meeting["randomized_at"]:
            raise HTTPException(status_code=409, detail="This meeting has already been randomized.")
        presenters = conn.execute(
            "SELECT * FROM packet_presenters WHERE meeting_id = ? AND randomized_at IS NULL "
            "ORDER BY advisor_code",
            (meeting_id,),
        ).fetchall()
        if not presenters and not conn.execute(
            "SELECT 1 FROM packet_presenters WHERE meeting_id = ?", (meeting_id,)
        ).fetchone():
            raise HTTPException(status_code=400, detail="Add the presenting advisors first.")
        hand_set = conn.execute(
            """SELECT COUNT(*) AS n FROM packet_assignments pa
               JOIN advisors a ON a.id = pa.advisor_id
               WHERE pa.meeting_id = ? AND pa.randomized_at IS NULL
                 AND pa.study_group IS NOT NULL AND a.in_study = 1""",
            (meeting_id,),
        ).fetchone()["n"]
        if hand_set:
            raise HTTPException(
                status_code=409,
                detail=f"{hand_set} resident(s) already have a group set by hand. Clear those "
                       f"groups before randomizing, or set every group by hand.",
            )
        seed = draw_seed()
        conn.execute(
            "UPDATE packet_meetings SET randomization_seed = ?, randomized_at = ? WHERE id = ?",
            (seed, _now(), meeting_id),
        )
        for presenter in presenters:
            _randomize_presenter(conn, meeting, presenter, seed)
        _event(conn, "meeting_randomized", meeting_id=meeting_id, new=seed)
        return meeting_state(conn, meeting_id)


@router.put("/assignments/{assignment_id}/group")
def change_group(assignment_id: str, data: GroupChange):
    """Set a group by hand. After randomization this needs a one-line reason, and
    records the change without ever touching randomized_group."""
    with _db_connection() as conn:
        row = _assignment(conn, assignment_id)
        if data.study_group == row["study_group"]:
            return meeting_state(conn, row["meeting_id"])
        reason = " ".join((data.reason or "").split())
        now = _now()
        if row["randomized_at"]:
            if not reason:
                raise HTTPException(
                    status_code=400,
                    detail="Say in one line why this group is changing after randomization.",
                )
            if data.study_group is None:
                raise HTTPException(status_code=400,
                                    detail="A group cannot be cleared after randomization.")
            conn.execute(
                """UPDATE packet_assignments SET study_group = ?, group_change_reason = ?,
                   group_changed_at = ? WHERE id = ?""",
                (data.study_group, reason, now, assignment_id),
            )
            conn.execute(
                """UPDATE packets SET study_group = ?, group_change_reason = ?, group_changed_at = ?
                   WHERE assignment_id = ?""",
                (data.study_group, reason, now, assignment_id),
            )
            _event(conn, "group_changed", meeting_id=row["meeting_id"],
                   assignment_id=assignment_id, advisor_id=row["advisor_id"],
                   old=row["study_group"], new=data.study_group, reason=reason)
        else:
            conn.execute("UPDATE packet_assignments SET study_group = ? WHERE id = ?",
                         (data.study_group, assignment_id))
            conn.execute("UPDATE packets SET study_group = ? WHERE assignment_id = ?",
                         (data.study_group, assignment_id))
            _event(conn, "group_set", meeting_id=row["meeting_id"], assignment_id=assignment_id,
                   advisor_id=row["advisor_id"], old=row["study_group"], new=data.study_group,
                   reason=reason or "Set by hand")
        return meeting_state(conn, row["meeting_id"])


@router.put("/assignments/{assignment_id}/period")
def override_period(assignment_id: str, data: PeriodOverride):
    """Override the review period's dates for one resident before generating. Both
    empty clears the override. Stored, and printed on the packet."""
    with _db_connection() as conn:
        row = _assignment(conn, assignment_id)
        start = data.override_start or None
        end = data.override_end or None
        meeting = _meeting(conn, row["meeting_id"])
        if start or end:
            _period_error(lambda: review_period.compute_period(
                conn, row["resident_id"], meeting["cutoff_date"],
                override_start=start, override_end=end, exclude_meeting_id=meeting["id"],
            ))
        conn.execute(
            "UPDATE packet_assignments SET override_start = ?, override_end = ? WHERE id = ?",
            (start, end, assignment_id),
        )
        _event(conn, "period_override", meeting_id=row["meeting_id"], assignment_id=assignment_id,
               old=json.dumps([row["override_start"], row["override_end"]]),
               new=json.dumps([start, end]))
        return meeting_state(conn, row["meeting_id"])


# ---------------------------------------------------------------------------
# Generation queue
# ---------------------------------------------------------------------------

_worker_task = None


@router.post("/meetings/{meeting_id}/generate")
async def generate(meeting_id: str, data: GenerateRequest):
    """Queue packets for generation, one resident at a time in the background. The
    page polls the meeting for progress, so a reload does not stop anything."""
    with _db_connection() as conn:
        _meeting(conn, meeting_id)
        if data.assignment_ids:
            marks = ",".join("?" * len(data.assignment_ids))
            rows = conn.execute(
                f"SELECT id, status FROM packet_assignments WHERE meeting_id = ? AND id IN ({marks})",
                [meeting_id, *data.assignment_ids],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, status FROM packet_assignments WHERE meeting_id = ? "
                "AND resident_id IS NOT NULL",
                (meeting_id,),
            ).fetchall()
        now = _now()
        queued = 0
        for r in rows:
            if r["status"] in ("queued", "running"):
                continue
            conn.execute(
                """UPDATE packet_assignments SET status = 'queued', status_detail = NULL,
                   requested_model = ?, queued_at = ? WHERE id = ?""",
                (data.model, now, r["id"]),
            )
            queued += 1
    _start_worker()
    return {"queued": queued}


def _start_worker():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.get_running_loop().create_task(_run_queue())


async def _run_queue():
    while True:
        with _db_connection() as conn:
            row = conn.execute(
                "SELECT * FROM packet_assignments WHERE status = 'queued' "
                "ORDER BY queued_at, rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return
            conn.execute(
                "UPDATE packet_assignments SET status = 'running', status_detail = ? WHERE id = ?",
                ("Starting", row["id"]),
            )
        try:
            packet_id = await build_packet(row["id"])
        except Exception as e:  # noqa: BLE001 — one failure must not stop the rest
            print(f"[packets] generation failed for assignment {row['id']}: {e}")
            with _db_connection() as conn:
                conn.execute(
                    "UPDATE packet_assignments SET status = 'failed', status_detail = ? WHERE id = ?",
                    (str(e) or e.__class__.__name__, row["id"]),
                )
            continue
        with _db_connection() as conn:
            conn.execute(
                "UPDATE packet_assignments SET status = 'done', status_detail = NULL, "
                "packet_id = ? WHERE id = ?",
                (packet_id, row["id"]),
            )


class PacketError(Exception):
    """Generation could not finish. The message is shown on the Packets page."""


def _first_sentence(text, limit=220):
    text = " ".join((text or "").split())
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    sentence = match.group(1) if match else text
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def _previously(conn, previous_packet_id):
    """The period and headline points of the previous packet. Read from what that
    packet said, never re-summarized from old comments."""
    if not previous_packet_id:
        return None
    prev = conn.execute("SELECT * FROM packets WHERE id = ?", (previous_packet_id,)).fetchone()
    if prev is None:
        return None
    summary = conn.execute(
        "SELECT ai_draft FROM summaries WHERE id = ?", (prev["summary_id"],)
    ).fetchone()
    points = []
    try:
        report = json.loads(summary["ai_draft"]) if summary else {}
    except (TypeError, ValueError):
        report = {}
    for block in report.get("competencies") or []:
        if block.get("status") != summary_builder.STATUS_OK:
            continue
        points.append({
            "competency": block.get("name"),
            "strengths": _first_sentence((block.get("strengths") or {}).get("text")),
            "growth_areas": _first_sentence((block.get("growth_areas") or {}).get("text")),
        })
    return {
        "packet_id": prev["id"],
        "period_start": prev["period_start"],
        "period_end": prev["period_end"],
        "cutoff_date": prev["cutoff_date"],
        "points": points,
    }


async def build_packet(assignment_id) -> str:
    """Generate one packet: the AI draft for its review period (MedHub comments
    only), the six-competency statements, and the packets row. Returns its id."""
    engine = retrieval_engine.get_active()
    version = _app_version()
    with _db_connection() as conn:
        row = _assignment(conn, assignment_id)
        meeting = _meeting(conn, row["meeting_id"])
        resident = conn.execute(
            "SELECT * FROM residents WHERE id = ?", (row["resident_id"],)
        ).fetchone()
        if resident is None:
            raise PacketError("This resident is no longer in AUC.")
        advisor = conn.execute(
            "SELECT name FROM advisors WHERE id = ?", (row["advisor_id"],)
        ).fetchone()
        try:
            per = review_period.compute_period(
                conn, resident["id"], meeting["cutoff_date"],
                override_start=row["override_start"], override_end=row["override_end"],
                exclude_meeting_id=meeting["id"],
            )
        except review_period.PeriodError as e:
            raise PacketError(str(e)) from e
        snap = review_period.latest_import(conn)
        snapshot_id = snap["id"] if snap else None
        evidence = review_period.load_evidence(
            conn, resident["id"], per, review_period.SCOPE_MEDHUB_ONLY, snapshot_id
        )
        pgy = review_period.pgy_at(conn, resident, per["period_end"])
        previously = _previously(conn, per["previous_packet_id"])

    try:
        domains = rag_retrieval.load_ontology()["domains"]
    except rag_retrieval.RagUnavailable as e:
        raise PacketError(f"The ACGME ontology could not be read: {e}") from e

    info = {
        "period": per, "scope": review_period.SCOPE_MEDHUB_ONLY,
        "counts": evidence["counts"], "snapshot_id": snapshot_id, "pgy": pgy,
    }
    metadata = review_period.report_metadata(info, version, engine)
    metadata["rating_tallies"] = review_period.rating_tallies(evidence["ratings"], domains)
    metadata["previously"] = previously
    metadata["packet_format_version"] = PACKET_FORMAT_VERSION
    label = f"{resident['first_name']} {resident['last_name']}, PGY-{pgy['pgy_year']}"

    report = summary_id = None
    async for event, payload in summary_builder.generate_report(
        resident["id"], label, evidence["comments"], model=row["requested_model"],
        engine=engine, metadata=metadata, competencies=True,
    ):
        if event == "error":
            raise PacketError(payload.get("message") or "Generation failed")
        if event in ("subcompetency", "competency"):
            with _db_connection() as conn:
                conn.execute(
                    "UPDATE packet_assignments SET status_detail = ? WHERE id = ?",
                    (f"{payload['completed']} of {payload['total']} sections", assignment_id),
                )
        elif event == "done":
            report, summary_id = payload["report"], payload["summary_id"]
    if report is None:
        raise PacketError("Generation ended early.")

    with _db_connection() as conn:
        review_period.insert_summary(conn, summary_id, resident["id"], report, info, version, engine)
        row = _assignment(conn, assignment_id)  # the group may have changed meanwhile
        packet_id = _id()
        while conn.execute("SELECT 1 FROM packets WHERE id = ?", (packet_id,)).fetchone():
            packet_id = _id()
        counts = evidence["counts"]
        conn.execute(
            """INSERT INTO packets
               (id, summary_id, meeting_id, assignment_id, resident_id, study_code, advisor_id,
                advisor_code, advisor_name, meeting_date, cutoff_date, study_group,
                randomized_group, randomization_seed, group_change_reason, group_changed_at,
                period_start, period_end, period_basis, n_comments, n_evaluators, n_rotations,
                n_late, medhub_import_id, pgy_year_at_cutoff, model, app_version,
                retrieval_engine, packet_format_version, show_levels)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?)""",
            (
                packet_id, summary_id, meeting["id"], assignment_id, resident["id"],
                row["study_code"] or ccc._ensure_study_code(conn, resident["id"]),
                row["advisor_id"], row["advisor_code"], advisor["name"] if advisor else None,
                meeting["meeting_date"], meeting["cutoff_date"], row["study_group"],
                row["randomized_group"], row["randomization_seed"], row["group_change_reason"],
                row["group_changed_at"], per["period_start"], per["period_end"],
                per["period_basis"], counts["n_comments"], counts["n_evaluators"],
                counts["n_rotations"], counts["n_late"], snapshot_id, pgy["pgy_year"],
                report.get("model"),
                version, engine, PACKET_FORMAT_VERSION, 1 if config.PACKET_SHOW_LEVELS else 0,
            ),
        )
        _event(conn, "generated", meeting_id=meeting["id"], assignment_id=assignment_id,
               packet_id=packet_id, advisor_id=row["advisor_id"])
    return packet_id


# ---------------------------------------------------------------------------
# Withholding and downloads
# ---------------------------------------------------------------------------

@router.post("/{packet_id}/withhold")
def withhold(packet_id: str, data: Withhold):
    """Mark a packet as not to be given out. It is left out of every batch download."""
    reason = " ".join((data.reason or "").split())
    if not reason:
        raise HTTPException(status_code=400, detail="Say in one line why this packet is withheld.")
    with _db_connection() as conn:
        packet = _packet(conn, packet_id)
        if packet["withheld"]:
            raise HTTPException(status_code=409, detail="This packet is already withheld.")
        conn.execute(
            "UPDATE packets SET withheld = 1, withheld_reason = ?, withheld_at = ? WHERE id = ?",
            (reason, _now(), packet_id),
        )
        _event(conn, "withheld", meeting_id=packet["meeting_id"],
               assignment_id=packet["assignment_id"], packet_id=packet_id,
               advisor_id=packet["advisor_id"], reason=reason)
        return dict(_packet(conn, packet_id))


def _render(conn, packet):
    summary = conn.execute(
        "SELECT ai_draft FROM summaries WHERE id = ?", (packet["summary_id"],)
    ).fetchone()
    if summary is None:
        raise HTTPException(status_code=404, detail="The summary behind this packet is gone.")
    resident = conn.execute(
        "SELECT first_name, last_name FROM residents WHERE id = ?", (packet["resident_id"],)
    ).fetchone()
    return pdf_export.render_packet_pdf(
        packet=dict(packet),
        report=json.loads(summary["ai_draft"]),
        resident_name=(f"{resident['first_name']} {resident['last_name']}" if resident else None),
    ), (resident["last_name"] if resident else "resident")


def _mark_downloaded(conn, packet_ids):
    now = _now()
    for pid in packet_ids:
        conn.execute(
            "UPDATE packets SET first_downloaded_at = ? WHERE id = ? AND first_downloaded_at IS NULL",
            (now, pid),
        )


@router.get("/{packet_id}/pdf")
def download_packet(packet_id: str):
    """One packet. Withheld ones can still be opened here, to see why."""
    with _db_connection() as conn:
        packet = _packet(conn, packet_id)
        pdf, last_name = _render(conn, packet)
        _mark_downloaded(conn, [packet_id])
    name = packet_filename(packet["advisor_name"], last_name, packet["cutoff_date"])
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/meetings/{meeting_id}/download")
def download_batch(meeting_id: str, advisor_id: Optional[str] = None):
    """A zip of packets: one advisor's, or the whole meeting's in a folder per
    advisor. Only each resident's current packet, and never a withheld one."""
    with _db_connection() as conn:
        meeting = _meeting(conn, meeting_id)
        sql = """SELECT p.* FROM packet_assignments pa JOIN packets p ON p.id = pa.packet_id
                 WHERE pa.meeting_id = ? AND p.withheld = 0"""
        args = [meeting_id]
        if advisor_id:
            sql += " AND pa.advisor_id = ?"
            args.append(advisor_id)
        packets_rows = conn.execute(sql, args).fetchall()
        if not packets_rows:
            raise HTTPException(status_code=404, detail="No packets ready to download.")

        buf = io.BytesIO()
        used = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for packet in packets_rows:
                pdf, last_name = _render(conn, packet)
                name = packet_filename(packet["advisor_name"], last_name, packet["cutoff_date"])
                if not advisor_id:
                    name = f"{_file_part(_last_name(packet['advisor_name']))}/{name}"
                stem, n = name[:-4], 2
                while name in used:
                    name = f"{stem}-{n}.pdf"
                    n += 1
                used.add(name)
                zf.writestr(name, pdf)
        _mark_downloaded(conn, [p["id"] for p in packets_rows])
        if advisor_id:
            first = packets_rows[0]
            zip_name = (f"packets-{_file_part(_last_name(first['advisor_name']))}-"
                        f"{meeting['cutoff_date']}.zip")
        else:
            zip_name = f"packets-meeting-{meeting['meeting_date']}.zip"
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})
