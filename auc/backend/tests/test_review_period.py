"""Review periods: which evidence a summary reads.

Before review periods every summary read every note and MedHub comment ever stored,
so a PGY-3 summary mixed in intern-year comments. The first test was written against
that behaviour (it asserted all 18 months were read) before the code changed; it now
asserts the period limit. The "All time" choice must still reproduce the old evidence
set exactly, which the second test checks against a frozen copy of the old code.
"""

import json
from datetime import date

import pytest
import review_period
from evidence_helpers import (
    StubModel,
    add_comment,
    add_import,
    add_rating,
    add_resident,
    connect,
    map_questions,
    read_sse,
)

# 18 months of comments, one per rotation, the 15th of each month.
MONTHS = [date(2025, 5 + i, 15) if 5 + i <= 12 else date(2026, i - 7, 15) for i in range(18)]


def _text(when):
    return f"Rotation ending {when.isoformat()} went well."


def _eighteen_months(logged_in, app_module):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    map_questions(conn)
    imported = add_import(conn, "2026-11-01 09:00:00")
    for when in MONTHS:
        add_comment(conn, rid, _text(when), when.isoformat(), imported)
    conn.close()
    return rid


def test_a_summary_reads_only_its_review_period(logged_in, app_module, monkeypatch):
    rid = _eighteen_months(logged_in, app_module)
    model = StubModel(monkeypatch)

    response = logged_in.post(f"/api/residents/{rid}/summary-stream?cutoff=2026-11-03")
    assert response.status_code == 200
    events = read_sse(response)
    assert events[-1][0] == "done"

    # The academic year started on 23 June 2026: July to October are in, the rest out.
    inside = [m for m in MONTHS if date(2026, 6, 23) <= m <= date(2026, 11, 3)]
    assert [m.month for m in inside] == [7, 8, 9, 10]
    for when in MONTHS:
        assert model.seen(_text(when)) == (when in inside), when

    conn = connect(app_module)
    row = conn.execute("SELECT * FROM summaries WHERE resident_id = ?", (rid,)).fetchone()
    assert (row["period_start"], row["period_end"]) == ("2026-06-23", "2026-11-03")
    assert row["period_basis"] == "academic_year_start"
    assert row["evidence_scope"] == "all"
    assert (row["n_comments"], row["n_evaluators"], row["n_rotations"]) == (4, 1, 4)
    assert row["medhub_import_id"] and row["app_version"] and row["retrieval_engine"]
    assert row["pgy_year_at_cutoff"] == 2
    assert row["model"]
    report = json.loads(row["ai_draft"])
    assert report["schema"] == "auc.summary.v2"
    assert report["period"]["start"] == "2026-06-23"
    assert report["counts"]["n_comments"] == 4
    assert report["resident"] == "Ada Lovelace, PGY-2"
    # Dates never reach the model, beyond the source labels the model is not shown.
    assert not any("2026-06-23" in p or "2026-11-03" in p for p in model.prompts)


def test_all_time_reads_every_month_and_stays_v1(logged_in, app_module, monkeypatch):
    rid = _eighteen_months(logged_in, app_module)
    model = StubModel(monkeypatch)
    response = logged_in.post(f"/api/residents/{rid}/summary-stream?period=all_time")
    assert read_sse(response)[-1][0] == "done"
    for when in MONTHS:
        assert model.seen(_text(when))
    conn = connect(app_module)
    row = conn.execute("SELECT * FROM summaries WHERE resident_id = ?", (rid,)).fetchone()
    assert row["period_basis"] == "all_time" and row["period_start"] is None
    assert json.loads(row["ai_draft"])["schema"] == "auc.summary.v1"


def _old_load_evidence(app_module, resident_id):
    """app._load_resident_evidence as it was before review periods, frozen here."""
    with app_module.db_connection() as conn:
        notes = conn.execute(
            "SELECT * FROM notes WHERE resident_id = ? ORDER BY created_at", (resident_id,)
        ).fetchall()
        medhub_rows = conn.execute(
            "SELECT * FROM medhub_evaluations WHERE resident_id = ? ORDER BY evaluation_date",
            (resident_id,),
        ).fetchall()
        medhub_comments = app_module._medhub_comment_evidence(conn, resident_id)
    return [
        {
            "text": n["content"],
            "label": f"committee note, source={n['source'] or 'unknown'}, "
                     f"tagged domain={n['acgme_domain'] or 'General'}, {n['created_at']}",
        }
        for n in notes
    ] + [
        {
            "text": r["comments"],
            "label": f"MedHub eval, rotation={r['rotation_name']}, "
                     f"evaluator={r['evaluator_name']}, score={r['score']}, {r['evaluation_date']}",
        }
        for r in medhub_rows if r["comments"]
    ] + medhub_comments


def test_all_time_is_exactly_the_old_evidence_set(logged_in, app_module):
    rid = _eighteen_months(logged_in, app_module)
    logged_in.post(f"/api/residents/{rid}/notes", json={
        "content": "Ran a family meeting well.", "source": "CCC Meeting",
        "note_date": "2024-02-01 10:00:00",
    })
    logged_in.post(f"/api/residents/{rid}/notes", json={"content": "Strong on rounds."})
    conn = connect(app_module)
    conn.execute(
        "INSERT INTO medhub_evaluations (id, resident_id, rotation_name, evaluator_name, "
        "evaluation_date, competency_domain, score, comments) VALUES "
        "('e1', ?, 'Wards', 'Dr Old', '2024-01-10', 'PC', 4, 'Older table comment.')",
        (rid,),
    )
    conn.commit()

    _row, _label, comments, info = app_module._load_resident_evidence(rid, "all_time")
    assert comments == _old_load_evidence(app_module, rid)
    assert info["period"]["period_basis"] == "all_time"


# ---------------------------------------------------------------------------
# Where a period starts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cutoff,start", [
    ("2026-06-22", "2025-06-23"),
    ("2026-06-23", "2026-06-23"),
    ("2026-06-24", "2026-06-23"),
])
def test_the_first_period_starts_at_the_academic_year(logged_in, app_module, cutoff, start):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    period = review_period.compute_period(conn, rid, cutoff)
    assert (period["period_start"], period["period_end"]) == (start, cutoff)
    assert period["period_basis"] == "academic_year_start"


def _packet_row(conn, rid, period_end, withheld=0, meeting_id="old"):
    conn.execute(
        """INSERT INTO packets (id, resident_id, meeting_id, period_start, period_end,
                                cutoff_date, withheld)
           VALUES (?, ?, ?, '2026-06-23', ?, ?, ?)""",
        (f"p{period_end[-5:]}{withheld}", rid, meeting_id, period_end, period_end, withheld),
    )
    conn.commit()


def test_a_later_period_starts_after_the_previous_packet(logged_in, app_module):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    _packet_row(conn, rid, "2026-09-30")
    _packet_row(conn, rid, "2026-11-15", withheld=1)  # withheld: never given out
    period = review_period.compute_period(conn, rid, "2026-12-01")
    assert period["period_start"] == "2026-10-01"
    assert period["period_basis"] == "previous_packet"

    # A packet of the meeting being built is not "previous".
    again = review_period.compute_period(conn, rid, "2026-12-01", exclude_meeting_id="old")
    assert again["period_basis"] == "academic_year_start"


def _ccc_review(conn, rid, meeting_date, touched=True):
    sid = f"s{meeting_date}"
    conn.execute("INSERT INTO ccc_sessions (id, meeting_date, cycle_label, closed_at) "
                 "VALUES (?, ?, 'Fall', datetime('now'))", (sid, meeting_date))
    conn.execute(
        "INSERT INTO ccc_resident_logs (id, session_id, resident_id, study_code, room_input_level) "
        "VALUES (?, ?, ?, 'R001', ?)",
        (f"l{meeting_date}", sid, rid, "thin" if touched else None),
    )
    conn.commit()


def test_a_first_packet_starts_after_the_last_review(logged_in, app_module):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    _ccc_review(conn, rid, "2026-10-15")
    _ccc_review(conn, rid, "2026-11-20", touched=False)  # page opened, nothing recorded
    _ccc_review(conn, rid, "2026-05-01")                 # last academic year: ignored
    period = review_period.compute_period(conn, rid, "2027-01-05")
    assert period["period_start"] == "2026-10-16"
    assert period["period_basis"] == "last_review"


def test_last_review_uses_the_same_touched_rule_as_the_export(logged_in, app_module):
    import ccc_export

    rid = add_resident(logged_in)
    conn = connect(app_module)
    _ccc_review(conn, rid, "2026-10-15")
    _ccc_review(conn, rid, "2026-11-20", touched=False)
    conn.execute("UPDATE ccc_resident_logs SET room_input_level = NULL, closed_at = datetime('now') "
                 "WHERE id = 'l2026-10-15'")
    conn.commit()
    header, rows = ccc_export.resident_sessions(conn)
    exported = {r[header.index("session_date")]: r[header.index("touched")] for r in rows}
    for meeting_date, touched in exported.items():
        found = conn.execute(
            f"SELECT COUNT(*) FROM ccc_resident_logs l JOIN ccc_sessions s ON s.id = l.session_id "
            f"WHERE s.meeting_date = ? AND {review_period.TOUCHED_SQL}", (meeting_date,),
        ).fetchone()[0]
        assert found == touched, meeting_date


def test_an_override_wins_and_is_recorded(logged_in, app_module):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    _packet_row(conn, rid, "2026-09-30")
    period = review_period.compute_period(conn, rid, "2026-12-01",
                                          override_start="2026-08-01", override_end="2026-11-01")
    assert (period["period_start"], period["period_end"], period["period_basis"]) == (
        "2026-08-01", "2026-11-01", "override")
    with pytest.raises(review_period.PeriodError):
        review_period.compute_period(conn, rid, "2026-12-01",
                                     override_start="2026-12-02", override_end="2026-12-01")


def test_all_time_has_no_dates():
    period = review_period.all_time_period()
    assert period["period_basis"] == "all_time"
    assert period["period_start"] is None and period["period_end"] is None


# ---------------------------------------------------------------------------
# What is in a period
# ---------------------------------------------------------------------------

def _texts(evidence):
    return [c["text"] for c in evidence["comments"]]


def test_period_boundaries_are_inclusive(logged_in, app_module):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    map_questions(conn)
    imported = add_import(conn, "2026-11-04 08:00:00")
    for day in ("2026-06-22", "2026-06-23", "2026-11-03", "2026-11-04"):
        add_comment(conn, rid, f"ended {day}", day, imported)
    period = review_period.compute_period(conn, rid, "2026-11-03")
    evidence = review_period.load_evidence(conn, rid, period, "medhub_only", imported)
    assert _texts(evidence) == ["ended 2026-06-23", "ended 2026-11-03"]


def test_a_later_import_does_not_change_a_snapshot(logged_in, app_module):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    map_questions(conn)
    first = add_import(conn, "2026-10-20 08:00:00")
    add_comment(conn, rid, "In the first import.", "2026-10-01", first)
    period = review_period.compute_period(conn, rid, "2026-11-03")
    before = review_period.load_evidence(conn, rid, period, "medhub_only", first)

    later = add_import(conn, "2026-11-10 08:00:00")
    add_comment(conn, rid, "Imported after the packet.", "2026-10-15", later)
    after = review_period.load_evidence(conn, rid, period, "medhub_only", first)
    assert _texts(before) == _texts(after) == ["In the first import."]
    # Without the snapshot, it would have been read.
    assert "Imported after the packet." in _texts(
        review_period.load_evidence(conn, rid, period, "medhub_only", later))


def test_the_same_comment_in_both_medhub_tables_is_counted_once(logged_in, app_module):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    map_questions(conn)
    imported = add_import(conn, "2026-11-01 08:00:00")
    add_comment(conn, rid, "Calm under pressure.", "2026-09-20", imported, evaluator="Brennan, Tom")
    conn.execute(
        "INSERT INTO medhub_evaluations (id, resident_id, rotation_name, evaluator_name, "
        "evaluation_date, competency_domain, score, comments) VALUES "
        "('dup', ?, 'ICU', 'Brennan, Tom', '2026-09-21', 'PC', 4, '  calm under   pressure. '), "
        "('own', ?, 'ICU', 'Brennan, Tom', '2026-09-21', 'MK', 4, 'Reads around cases.')",
        (rid, rid),
    )
    conn.commit()
    period = review_period.compute_period(conn, rid, "2026-11-03")
    evidence = review_period.load_evidence(conn, rid, period, "medhub_only", imported)
    assert sorted(_texts(evidence)) == ["Calm under pressure.", "Reads around cases."]
    assert evidence["counts"]["n_comments"] == 2


def test_notes_are_read_only_with_scope_all(logged_in, app_module):
    rid = add_resident(logged_in)
    logged_in.post(f"/api/residents/{rid}/notes", json={
        "content": "In the period.", "note_date": "2026-09-01 10:00:00"})
    logged_in.post(f"/api/residents/{rid}/notes", json={
        "content": "Before it.", "note_date": "2026-05-01 10:00:00"})
    conn = connect(app_module)
    period = review_period.compute_period(conn, rid, "2026-11-03")
    assert _texts(review_period.load_evidence(conn, rid, period, "all")) == ["In the period."]
    assert _texts(review_period.load_evidence(conn, rid, period, "medhub_only")) == []


def test_late_arrivals_are_carried_into_the_next_packet(logged_in, app_module):
    """A rotation ending before the previous packet's cutoff, evaluated after it,
    would otherwise fall between the two packets and never appear in either."""
    rid = add_resident(logged_in)
    conn = connect(app_module)
    map_questions(conn)
    first = add_import(conn, "2026-11-02 08:00:00")
    add_comment(conn, rid, "Seen by packet one.", "2026-10-20", first)
    conn.execute(
        "INSERT INTO packets (id, resident_id, meeting_id, period_start, period_end, cutoff_date, "
        "withheld, generated_at, medhub_import_id) VALUES ('pk1', ?, 'm1', '2026-06-23', "
        "'2026-11-03', '2026-11-03', 0, '2026-11-03 12:00:00', ?)", (rid, first))
    conn.commit()
    later = add_import(conn, "2026-11-10 08:00:00")
    add_comment(conn, rid, "Submitted late for October.", "2026-11-01", later)
    add_comment(conn, rid, "In the new period.", "2026-12-01", later)

    period = review_period.compute_period(conn, rid, "2027-01-05")
    assert period["period_start"] == "2026-11-04"
    evidence = review_period.load_evidence(conn, rid, period, "medhub_only", later)
    assert _texts(evidence) == ["Submitted late for October.", "In the new period."]
    assert evidence["counts"]["n_late"] == 1
    assert "arrived after the previous review" in evidence["comments"][0]["label"]


def test_counts_of_comments_evaluators_and_rotations(logged_in, app_module):
    rid = add_resident(logged_in)
    conn = connect(app_module)
    map_questions(conn)
    imported = add_import(conn, "2026-11-01 08:00:00")
    add_comment(conn, rid, "One.", "2026-07-19", imported, evaluator="Alvarez, Rosa",
                rotation_start="2026-07-06")
    add_comment(conn, rid, "Two.", "2026-07-19", imported, evaluator="Brennan, Tom",
                rotation_start="2026-07-06")
    add_comment(conn, rid, "Three.", "2026-09-20", imported, evaluator="Alvarez, Rosa",
                rotation_start="2026-09-14", service="ICU1")
    add_rating(conn, rid, "Junior Resident (competent learner)", "2026-07-19", imported)
    period = review_period.compute_period(conn, rid, "2026-11-03")
    counts = review_period.load_evidence(conn, rid, period, "medhub_only", imported)["counts"]
    assert (counts["n_comments"], counts["n_evaluators"], counts["n_rotations"]) == (3, 2, 2)


def test_rating_tallies_use_medhub_labels_and_count_only(logged_in, app_module):
    import rag_retrieval

    rid = add_resident(logged_in)
    conn = connect(app_module)
    map_questions(conn)
    imported = add_import(conn, "2026-11-01 08:00:00")
    for answer in ("Junior Resident (competent learner)", "Junior Resident", "Unable to Assess",
                   "Aspirational"):
        add_rating(conn, rid, answer, "2026-08-01", imported)
    add_rating(conn, rid, "Intern", "2026-08-01", imported, competency="Professionalism")
    period = review_period.compute_period(conn, rid, "2026-11-03")
    ratings = review_period.load_evidence(conn, rid, period, "medhub_only", imported)["ratings"]
    tallies = review_period.rating_tallies(ratings, rag_retrieval.load_ontology()["domains"])
    assert tallies["PC"] == {
        "Conditional on improvement": 0, "Intern": 0, "Junior resident": 2,
        "Senior resident": 0, "Aspirational": 1, "Unable to assess": 1,
    }
    assert tallies["PROF"]["Intern"] == 1
    assert sum(tallies["MK"].values()) == 0


# ---------------------------------------------------------------------------
# PGY year at the cutoff, staleness, thin evidence
# ---------------------------------------------------------------------------

def test_pgy_year_at_the_cutoff_across_a_23_june_advancement(logged_in, app_module):
    rid = add_resident(logged_in, pgy=1)
    # Advance on 23 June 2026: the snapshot holds the year before advancing.
    logged_in.post("/api/advancement/execute", json={"advance_to_pgy2": [rid]})
    conn = connect(app_module)
    conn.execute("UPDATE advancement_snapshots SET created_at = '2026-06-23 09:00:00'")
    conn.commit()
    resident = conn.execute("SELECT * FROM residents WHERE id = ?", (rid,)).fetchone()
    assert resident["pgy_year"] == 2
    assert review_period.pgy_at(conn, resident, "2026-06-22") == {"pgy_year": 1, "note": None}
    assert review_period.pgy_at(conn, resident, "2026-06-23")["pgy_year"] == 2
    assert review_period.pgy_at(conn, resident, "2026-06-24")["pgy_year"] == 2


def test_pgy_year_falls_back_to_the_current_one_with_a_note(logged_in, app_module):
    rid = add_resident(logged_in, pgy=3)
    conn = connect(app_module)
    resident = conn.execute("SELECT * FROM residents WHERE id = ?", (rid,)).fetchone()
    old = review_period.pgy_at(conn, resident, "2024-01-10")
    assert old["pgy_year"] == 3 and old["note"]


def test_a_stale_medhub_import_is_warned_about(logged_in, app_module):
    conn = connect(app_module)
    assert review_period.staleness_warning(conn, "2026-11-03")  # nothing imported
    add_import(conn, "2026-10-31 23:00:00")
    assert review_period.staleness_warning(conn, "2026-11-03") is None   # 3 days
    assert "4 days" in review_period.staleness_warning(conn, "2026-11-04")


@pytest.mark.parametrize("n_comments,thin", [(4, True), (5, False)])
def test_thin_evidence_is_below_the_threshold_not_at_it(n_comments, thin):
    info = {
        "period": {**review_period.all_time_period(), "period_start": "2026-06-23",
                   "period_end": "2026-11-03", "period_basis": "academic_year_start"},
        "scope": "medhub_only", "snapshot_id": None, "pgy": {"pgy_year": 2, "note": None},
        "counts": {"n_comments": n_comments, "n_evaluators": 1, "n_rotations": 1, "n_late": 0},
    }
    assert review_period.report_metadata(info, "dev", "ollama")["thin_evidence"] is thin


def test_the_review_period_endpoint_previews_what_a_summary_covers(logged_in, app_module):
    rid = _eighteen_months(logged_in, app_module)
    body = logged_in.get(f"/api/residents/{rid}/review-period?cutoff=2026-11-03").json()
    assert body["period_start"] == "2026-06-23"
    assert body["counts"]["n_comments"] == 4
    assert body["basis_description"]
    assert body["staleness_warning"] is None

    everything = logged_in.get(f"/api/residents/{rid}/review-period?period=all_time").json()
    assert everything["counts"]["n_comments"] == 18

    bad = logged_in.get(f"/api/residents/{rid}/review-period?period=custom&period_start=2026-12-01"
                        f"&period_end=2026-11-01")
    assert bad.status_code == 400


def test_an_empty_period_is_refused_with_a_plain_message(logged_in, app_module, monkeypatch):
    rid = _eighteen_months(logged_in, app_module)
    StubModel(monkeypatch)
    response = logged_in.post(
        f"/api/residents/{rid}/summary-stream?period=custom&period_start=2027-01-01"
        f"&period_end=2027-01-31")
    assert response.status_code == 400
    assert response.json()["detail"] == "No evidence in this review period"
