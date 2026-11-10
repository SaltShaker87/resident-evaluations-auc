"""
AUC — review periods: which evidence a summary or advisor packet covers.

A review period is a date range [period_start, period_end] for one resident.
period_end is the cutoff date, the day the packet is built. period_start is,
first match wins:

  1. override             — the operator typed the dates.
  2. previous_packet      — the day after the cutoff of this resident's previous
                            packet (not withheld, from an earlier meeting).
  3. last_review          — the day after the last CCC meeting where this
                            resident was actually discussed (a "touched" log, the
                            same rule as ccc-resident-sessions.csv), if that
                            meeting is in the cutoff's academic year.
  4. academic_year_start  — the most recent academic year start (23 June by
                            default) on or before the cutoff.

all_time is the fifth basis: no dates at all, which is what every summary read
before review periods existed, and what the "All time" choice reproduces exactly.

What is in a period:

  - MedHub comments (medhub_answers): rotation_end inside the period, inclusive
    at both ends, and first imported at or before the snapshot import — the
    newest import when the summary was generated. A later import cannot change
    what an already generated packet was based on.
  - Late arrivals: a comment whose rotation ended before period_start (but in the
    same academic year) that was first imported after the previous packet's
    snapshot, or after the last review meeting. Evaluations are often submitted
    after a rotation ends; without this, one submitted after a packet's cutoff for
    a rotation inside its period would never appear in any packet. They are
    counted (n_late) and labeled.
  - The older MedHub table (medhub_evaluations): evaluation_date inside the
    period, unless the same comment text for the same resident already arrives
    through medhub_answers.
  - Committee notes: only with evidence scope "all", created inside the period.

Nothing here talks to the network or the model. Dates never reach the model:
they appear only in the quote source labels, as they always have.
"""

import json
from datetime import date, datetime, timedelta

import config
import medhub_import

BASIS_OVERRIDE = "override"
BASIS_PREVIOUS_PACKET = "previous_packet"
BASIS_LAST_REVIEW = "last_review"
BASIS_ACADEMIC_YEAR = "academic_year_start"
BASIS_ALL_TIME = "all_time"
BASES = (BASIS_OVERRIDE, BASIS_PREVIOUS_PACKET, BASIS_LAST_REVIEW, BASIS_ACADEMIC_YEAR,
         BASIS_ALL_TIME)

SCOPE_MEDHUB_ONLY = "medhub_only"
SCOPE_ALL = "all"
SCOPES = (SCOPE_MEDHUB_ONLY, SCOPE_ALL)

BASIS_DESCRIPTIONS = {
    BASIS_OVERRIDE: "Dates set by hand",
    BASIS_PREVIOUS_PACKET: "Starts the day after the previous packet's cutoff",
    BASIS_LAST_REVIEW: "Starts the day after the last committee review",
    BASIS_ACADEMIC_YEAR: "Starts at the beginning of the academic year",
    BASIS_ALL_TIME: "All evidence ever recorded, with no date limit",
}

# The "touched" rule from ccc_export._RESIDENT_SESSIONS_SQL, written per row: a log
# is a real discussion, not an incidental page visit. test_review_period checks the
# two agree, so a change to one without the other fails.
TOUCHED_SQL = """(
        l.room_input_level IS NOT NULL
     OR (l.roles_spoke IS NOT NULL AND TRIM(l.roles_spoke) NOT IN ('', '[]'))
     OR l.referenced_written_eval IS NOT NULL
     OR l.group_read_shifted IS NOT NULL
     OR l.pushback IS NOT NULL
     OR (l.room_raised_notes IS NOT NULL AND TRIM(l.room_raised_notes) != '')
     OR (l.pushback_note IS NOT NULL AND TRIM(l.pushback_note) != '')
     OR (l.closing_notes IS NOT NULL AND TRIM(l.closing_notes) != '')
     OR l.closed_at IS NOT NULL
     OR EXISTS (SELECT 1 FROM ccc_contributions c
                WHERE c.resident_log_id = l.id AND c.voided = 0)
)"""


class PeriodError(ValueError):
    """Dates that cannot make a review period. The message is shown to the user."""


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def parse_date(value, what="date") -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError) as exc:
        raise PeriodError(f"The {what} must be a date, YYYY-MM-DD.") from exc


def _year_start_setting():
    try:
        month, day = (int(p) for p in config.ACADEMIC_YEAR_START.split("-"))
        date(2001, month, day)
    except (ValueError, TypeError):
        month, day = 6, 23
    return month, day


def academic_year_start(on) -> date:
    """The most recent academic year start on or before `on`."""
    on = parse_date(on)
    month, day = _year_start_setting()
    start = date(on.year, month, day)
    return start if start <= on else date(on.year - 1, month, day)


def default_cutoff(meeting_date) -> date:
    return parse_date(meeting_date, "meeting date") - timedelta(days=config.PACKET_LEAD_DAYS)


def _db_time(value) -> datetime:
    """A 'YYYY-MM-DD HH:MM:SS' (UTC) timestamp from the database."""
    return datetime.fromisoformat(str(value).replace("T", " ")[:19])


# ---------------------------------------------------------------------------
# Where a period starts
# ---------------------------------------------------------------------------

def previous_packet(conn, resident_id, cutoff, exclude_meeting_id=None):
    """This resident's most recent packet with an earlier cutoff, not withheld."""
    cutoff = parse_date(cutoff)
    sql = ("SELECT * FROM packets WHERE resident_id = ? AND withheld = 0 "
           "AND cutoff_date < ?")
    args = [resident_id, cutoff.isoformat()]
    if exclude_meeting_id:
        sql += " AND (meeting_id IS NULL OR meeting_id != ?)"
        args.append(exclude_meeting_id)
    sql += " ORDER BY period_end DESC, generated_at DESC, rowid DESC LIMIT 1"
    return conn.execute(sql, args).fetchone()


def last_review(conn, resident_id, cutoff):
    """Date of the last CCC meeting, before the cutoff, where this resident was
    actually discussed. None if there was none."""
    row = conn.execute(
        f"""SELECT MAX(s.meeting_date) AS d
            FROM ccc_resident_logs l JOIN ccc_sessions s ON s.id = l.session_id
            WHERE l.resident_id = ? AND s.meeting_date < ? AND {TOUCHED_SQL}""",
        (resident_id, parse_date(cutoff).isoformat()),
    ).fetchone()
    return parse_date(row["d"]) if row and row["d"] else None


def compute_period(conn, resident_id, cutoff, *, override_start=None, override_end=None,
                   exclude_meeting_id=None) -> dict:
    """The review period for one resident at one cutoff.

    Returns {period_start, period_end, period_basis, previous_packet_id,
    last_review_date, late_after} — dates as YYYY-MM-DD. late_after is the moment
    after which an import counts as a late arrival ("YYYY-MM-DD HH:MM:SS"), or None
    when late arrivals do not apply.
    """
    end = parse_date(override_end or cutoff, "cutoff date")
    year_start = academic_year_start(end)
    prev = previous_packet(conn, resident_id, end, exclude_meeting_id)
    review = last_review(conn, resident_id, end)
    late_after = None

    if override_start or override_end:
        basis = BASIS_OVERRIDE
        if override_start:
            start = parse_date(override_start, "start date")
        elif prev:
            start = parse_date(prev["period_end"]) + timedelta(days=1)
        else:
            start = year_start
    elif prev:
        basis = BASIS_PREVIOUS_PACKET
        start = parse_date(prev["period_end"]) + timedelta(days=1)
        late_after = _import_time(conn, _packet_snapshot(conn, prev)) or prev["generated_at"]
    elif review and review >= year_start:
        basis = BASIS_LAST_REVIEW
        start = review + timedelta(days=1)
        # Anything imported by the end of the review day could have been discussed.
        late_after = f"{review.isoformat()} 23:59:59"
    else:
        basis = BASIS_ACADEMIC_YEAR
        start = year_start

    if start > end:
        raise PeriodError("The review period would start after its cutoff date.")

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "period_basis": basis,
        "previous_packet_id": prev["id"] if prev else None,
        "last_review_date": review.isoformat() if review else None,
        "late_after": late_after,
        "academic_year_start": year_start.isoformat(),
    }


def all_time_period() -> dict:
    return {
        "period_start": None, "period_end": None, "period_basis": BASIS_ALL_TIME,
        "previous_packet_id": None, "last_review_date": None, "late_after": None,
        "academic_year_start": None,
    }


# ---------------------------------------------------------------------------
# The MedHub snapshot
# ---------------------------------------------------------------------------

def _import_time(conn, import_id):
    if not import_id:
        return None
    row = conn.execute(
        "SELECT imported_at FROM medhub_import_log WHERE id = ?", (import_id,)
    ).fetchone()
    return row["imported_at"] if row else None


def _packet_snapshot(conn, packet):
    """The MedHub import a packet was built from: on the packet, else its summary."""
    if packet["medhub_import_id"]:
        return packet["medhub_import_id"]
    if packet["summary_id"]:
        row = conn.execute(
            "SELECT medhub_import_id FROM summaries WHERE id = ?", (packet["summary_id"],)
        ).fetchone()
        return row["medhub_import_id"] if row else None
    return None


def latest_import(conn):
    """The newest MedHub import, or None."""
    return conn.execute(
        "SELECT id, imported_at, file_name FROM medhub_import_log "
        "ORDER BY imported_at DESC, rowid DESC LIMIT 1"
    ).fetchone()


def staleness_warning(conn, cutoff):
    """A sentence to show before generating when MedHub data may be older than
    what the advisor can see in MedHub, else None."""
    cutoff = parse_date(cutoff)
    last = latest_import(conn)
    if last is None:
        return "No MedHub report has been imported yet, so there are no MedHub evaluations to use."
    imported = _db_time(last["imported_at"]).date()
    days = (cutoff - imported).days
    if days > config.MEDHUB_STALE_DAYS:
        return (
            f"The newest MedHub import is from {imported.isoformat()}, {days} days before the "
            f"cutoff date. Advisors may see evaluations in MedHub that this packet will not "
            f"include. Import a fresh MedHub report first if you can."
        )
    return None


def _snapshot_clause(alias, snapshot_id):
    """SQL restricting medhub_answers `alias` to rows first imported at or before
    the snapshot import. Imports are ordered by time, then by insertion order."""
    if not snapshot_id:
        return "", []
    return (
        f""" AND EXISTS (
            SELECT 1 FROM medhub_import_log li, medhub_import_log ls
            WHERE li.id = {alias}.import_id AND ls.id = ?
              AND (li.imported_at < ls.imported_at
                   OR (li.imported_at = ls.imported_at AND li.rowid <= ls.rowid)))""",
        [snapshot_id],
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def _medhub_label(c, late=False):
    question = c["question"] if len(c["question"]) <= 90 else c["question"][:87] + "..."
    label = (f"MedHub comment, {c['form_name']}, evaluator={c['evaluator']}, "
             f"rotation {c['rotation_start']} to {c['rotation_end']}, "
             f"service={c['service'] or 'unknown'}, question: {question}")
    return label + (", arrived after the previous review" if late else "")


def _note_item(n):
    return {
        "text": n["content"],
        "label": f"committee note, source={n['source'] or 'unknown'}, "
                 f"tagged domain={n['acgme_domain'] or 'General'}, {n['created_at']}",
    }


def _legacy_item(r):
    return {
        "text": r["comments"],
        "label": f"MedHub eval, rotation={r['rotation_name']}, "
                 f"evaluator={r['evaluator_name']}, score={r['score']}, {r['evaluation_date']}",
    }


def _all_time_evidence(conn, resident_id, scope):
    """Exactly what every summary read before review periods: every note, every
    older-table MedHub row with comments, every MedHub comment, in that order."""
    notes = conn.execute(
        "SELECT * FROM notes WHERE resident_id = ? ORDER BY created_at", (resident_id,)
    ).fetchall() if scope == SCOPE_ALL else []
    legacy = conn.execute(
        "SELECT * FROM medhub_evaluations WHERE resident_id = ? ORDER BY evaluation_date",
        (resident_id,),
    ).fetchall()
    answers = medhub_import.resident_comments(conn, resident_id)
    comments = (
        [_note_item(n) for n in notes]
        + [_legacy_item(r) for r in legacy if r["comments"]]
        + [{"text": c["answer"], "label": _medhub_label(c)} for c in answers]
    )
    medhub = [dict(c, late=False) for c in answers]
    legacy_used = [dict(r) for r in legacy if r["comments"]]
    return comments, medhub, legacy_used, len(notes)


def _period_answers(conn, resident_id, start, end, snapshot_id, question_type, late_after,
                    late_from):
    """MedHub answers of one question type in the period, plus late arrivals."""
    snap_sql, snap_args = _snapshot_clause("a", snapshot_id)
    kind = "AND a.answer_kind = 'text'" if question_type == "comment" else ""
    base = f"""SELECT a.evaluator, a.form_name, a.question, a.rotation_start, a.rotation_end,
                      a.service, a.answer, a.answer_kind, a.level, m.competency
               FROM medhub_answers a
               JOIN medhub_question_map m
                 ON m.form_name = a.form_name AND m.question = a.question
               WHERE a.resident_id = ? AND m.question_type = ? {kind}"""
    order = " ORDER BY a.rotation_end, a.evaluator, a.question"
    rows = [
        dict(r, late=False) for r in conn.execute(
            base + " AND a.rotation_end BETWEEN ? AND ?" + snap_sql + order,
            [resident_id, question_type, start, end] + snap_args,
        )
    ]
    if late_after and late_from and late_from < start:
        late = conn.execute(
            base + """ AND a.rotation_end >= ? AND a.rotation_end < ?
                       AND EXISTS (SELECT 1 FROM medhub_import_log li
                                   WHERE li.id = a.import_id AND li.imported_at > ?)"""
            + snap_sql + order,
            [resident_id, question_type, late_from, start, late_after] + snap_args,
        ).fetchall()
        rows = [dict(r, late=True) for r in late] + rows
    return rows


def _norm(text):
    return " ".join((text or "").split()).casefold()


def load_evidence(conn, resident_id, period, scope=SCOPE_ALL, snapshot_id=None) -> dict:
    """Everything one summary reads, for a period from compute_period() or
    all_time_period().

    Returns {comments, ratings, counts}. comments are in rag_retrieval's
    {"text", "label"} shape and are all the model ever sees. ratings are MedHub
    milestone answers in the period (for the packet's tallies; never sent to the
    model). counts: n_comments, n_evaluators, n_rotations, n_late, n_notes.
    """
    if scope not in SCOPES:
        raise PeriodError("Choose which evidence to use.")

    if period["period_basis"] == BASIS_ALL_TIME:
        comments, medhub, legacy, n_notes = _all_time_evidence(conn, resident_id, scope)
        ratings = []
    else:
        start, end = period["period_start"], period["period_end"]
        late_from = period.get("academic_year_start")
        medhub = _period_answers(conn, resident_id, start, end, snapshot_id, "comment",
                                 period.get("late_after"), late_from)
        ratings = _period_answers(conn, resident_id, start, end, snapshot_id, "milestone",
                                  period.get("late_after"), late_from)

        # The older table, minus any comment medhub_answers already carries.
        known = {
            _norm(r["answer"]) for r in conn.execute(
                "SELECT answer FROM medhub_answers WHERE resident_id = ? AND answer_kind = 'text'",
                (resident_id,),
            )
        }
        legacy = [
            dict(r) for r in conn.execute(
                """SELECT * FROM medhub_evaluations WHERE resident_id = ?
                   AND substr(evaluation_date, 1, 10) BETWEEN ? AND ?
                   ORDER BY evaluation_date""",
                (resident_id, start, end),
            )
            if r["comments"] and _norm(r["comments"]) not in known
        ]

        notes = conn.execute(
            """SELECT * FROM notes WHERE resident_id = ?
               AND substr(created_at, 1, 10) BETWEEN ? AND ? ORDER BY created_at""",
            (resident_id, start, end),
        ).fetchall() if scope == SCOPE_ALL else []
        n_notes = len(notes)

        comments = (
            [_note_item(n) for n in notes]
            + [_legacy_item(r) for r in legacy]
            + [{"text": c["answer"], "label": _medhub_label(c, c["late"])} for c in medhub]
        )

    evaluators = {_norm(c["evaluator"]) for c in medhub} | {
        _norm(r["evaluator_name"]) for r in legacy if r.get("evaluator_name")
    }
    rotations = {(c["rotation_start"], c["rotation_end"], _norm(c["service"])) for c in medhub} | {
        ("legacy", _norm(r.get("rotation_name")), (r.get("evaluation_date") or "")[:10])
        for r in legacy
    }
    return {
        "comments": comments,
        "ratings": ratings,
        "counts": {
            "n_comments": len(comments),
            "n_evaluators": len(evaluators),
            "n_rotations": len(rotations),
            "n_late": sum(1 for c in medhub if c["late"]),
            "n_notes": n_notes,
        },
    }


# ---------------------------------------------------------------------------
# Milestone tallies
# ---------------------------------------------------------------------------

# MedHub's own labels, from medhub_reports.MILESTONE_LEVELS, in level order.
TALLY_LABELS = [
    "Conditional on improvement", "Intern", "Junior resident", "Senior resident", "Aspirational",
]
UNABLE_LABEL = "Unable to assess"


def _domain_key(name):
    return _norm((name or "").replace("&", "and"))


def rating_tallies(ratings, domains) -> dict:
    """{domain_code: {label: count}} — counts only, never an average.

    domains is the ontology's {code: name}; a MedHub competency area is matched to
    it by name ("&" and "and" read alike). Ratings mapped to "No single competency"
    belong to none of the six and are not counted.
    """
    from medhub_reports import MILESTONE_LEVELS

    by_level = {n: label for label, n in zip(TALLY_LABELS, sorted(MILESTONE_LEVELS.values()),
                                             strict=True)}
    by_name = {_domain_key(name): code for code, name in domains.items()}
    tallies = {code: {label: 0 for label in TALLY_LABELS + [UNABLE_LABEL]} for code in domains}
    for r in ratings:
        code = by_name.get(_domain_key(r.get("competency")))
        if code is None:
            continue
        if r["answer_kind"] == "rated" and r["level"] in by_level:
            tallies[code][by_level[r["level"]]] += 1
        elif r["answer_kind"] == "unable":
            tallies[code][UNABLE_LABEL] += 1
    return tallies


# ---------------------------------------------------------------------------
# PGY year at the cutoff
# ---------------------------------------------------------------------------

def pgy_at(conn, resident, cutoff) -> dict:
    """The resident's PGY year on the cutoff date: {pgy_year, note}.

    note is None when the answer is sound, else a sentence saying why it may not be.

    Every advancement (Settings, "Advance residents") saves a snapshot of every
    resident first, so the earliest snapshot taken after the cutoff holds the PGY year
    the resident had at the cutoff. With no snapshot after the cutoff, nothing has
    been advanced since, and the current value is the one in force at the cutoff.
    """
    cutoff = parse_date(cutoff)
    current = resident["pgy_year"]
    if resident["start_date"]:
        try:
            if parse_date(resident["start_date"]) > cutoff:
                return {"pgy_year": current,
                        "note": "The cutoff date is before this resident's start date."}
        except PeriodError:
            pass

    snapshots = conn.execute(
        "SELECT snapshot_data FROM advancement_snapshots "
        "WHERE substr(created_at, 1, 10) > ? ORDER BY created_at, rowid",
        (cutoff.isoformat(),),
    ).fetchall()
    for snap in snapshots:
        try:
            people = json.loads(snap["snapshot_data"])
        except (TypeError, ValueError):
            continue
        for person in people:
            if person.get("id") == resident["id"] and person.get("pgy_year") is not None:
                return {"pgy_year": int(person["pgy_year"]), "note": None}

    if not snapshots and academic_year_start(cutoff) == academic_year_start(date.today()):
        return {"pgy_year": current, "note": None}

    # The cutoff is in an earlier academic year with no advancement recorded since,
    # or this resident is missing from the snapshots: count academic years from the
    # start date if there is one.
    if resident["start_date"]:
        try:
            began = academic_year_start(parse_date(resident["start_date"]))
            years = academic_year_start(cutoff).year - began.year + 1
            if 1 <= years <= 7:
                return {"pgy_year": years,
                        "note": "Worked out from the start date; there is no advancement record."}
        except PeriodError:
            pass
    return {"pgy_year": current,
            "note": "Could not tell the PGY year at the cutoff date; showing the current one."}


# ---------------------------------------------------------------------------
# What a summary records about itself
# ---------------------------------------------------------------------------

def report_metadata(info, app_version, engine) -> dict:
    """The keys a v2 report carries so it describes itself. Never sent to the model.

    info is what app._load_resident_evidence (or the packet builder) gathered:
    {period, scope, counts, snapshot_id, pgy}.
    """
    per, counts = info["period"], info["counts"]
    return {
        "period": {
            "start": per["period_start"],
            "end": per["period_end"],
            "cutoff": per["period_end"],
            "basis": per["period_basis"],
            "basis_description": BASIS_DESCRIPTIONS[per["period_basis"]],
            "previous_packet_id": per.get("previous_packet_id"),
            "last_review_date": per.get("last_review_date"),
        },
        "evidence_scope": info["scope"],
        "counts": counts,
        "thin_evidence": counts["n_comments"] < config.PACKET_THIN_EVIDENCE,
        "thin_evidence_threshold": config.PACKET_THIN_EVIDENCE,
        "medhub_import_id": info["snapshot_id"],
        "pgy_year_at_cutoff": info["pgy"]["pgy_year"],
        "pgy_note": info["pgy"]["note"],
        "app_version": app_version,
        "retrieval_engine": engine,
    }


def insert_summary(conn, summary_id, resident_id, report, info, app_version, engine):
    """Store a generated summary with the columns that say what it covers."""
    per, counts = info["period"], info["counts"]
    bound = per["period_basis"] != BASIS_ALL_TIME
    conn.execute(
        """INSERT INTO summaries
           (id, resident_id, ai_draft, period_start, period_end, period_basis, evidence_scope,
            n_comments, n_evaluators, n_rotations, medhub_import_id, pgy_year_at_cutoff,
            app_version, retrieval_engine, model)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            summary_id, resident_id, json.dumps(report), per["period_start"], per["period_end"],
            per["period_basis"], info["scope"], counts["n_comments"], counts["n_evaluators"],
            counts["n_rotations"], info["snapshot_id"],
            info["pgy"]["pgy_year"] if bound else None,
            app_version, engine, report.get("model"),
        ),
    )
