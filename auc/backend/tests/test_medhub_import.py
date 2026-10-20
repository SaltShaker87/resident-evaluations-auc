"""MedHub Evaluation Questions Summary import.

Reads the look-alike reports in fixtures/medhub (every name invented; see
build_fixtures.py) and checks the values against tables written out by hand
below — not derived from the fixture builder — so a reader that drifts from
what the files say fails here.
"""

from pathlib import Path

import medhub_reports as mr
import pytest
from conftest import TEST_PASSWORD
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "medhub"

INPATIENT = "Inpatient Medicine - Attending Evaluation of Trainee"
SUBSPECIALTY = "Subspecialty Clinic - Attending Evaluation of Trainee"

Q1 = ("Resident gathers a complete history and examines patients carefully. "
      "Keeps the medication list accurate.")
Q2 = "Resident builds a sound differential diagnosis and management plan."
Q3 = "Resident works with patients and families on decisions. Hands off patients safely."
Q4 = "Resident asks for feedback and changes practice in response."
Q5 = "Resident communicates clearly with the team"
Q6 = "Please provide comments or specific examples regarding the resident's strengths."
Q7 = "Please provide actionable feedback regarding areas for improvement for this resident."
INPATIENT_QUESTIONS = [Q1, Q2, Q3, Q4, Q5, Q6, Q7]

S1 = "The resident is punctual and responsive."
S2 = "The resident shows appropriate medical knowledge."
S3 = "What stood out about the resident's performance during this rotation?"
S4 = "Are there any specific areas where the resident could improve?"

JUNIOR = "Junior Resident (competent learner)"
SENIOR = "Senior Resident (proficient learner)"
INTERN = "Intern (advanced beginner)"

# (resident, evaluator, rotation start, rotation end, service, answers to Q1..Q7)
HADDAD_JUL = ("Haddad, Noor Ul Ain", "Alvarez, Rosa", "2026-07-06", "2026-07-19", "WARDS1", (
    INTERN, JUNIOR, "Intern", "Unable to Assess", "Conditional on improvement (novice)",
    "Thorough histories and follows up on every lab result before rounds.", "",
))
HADDAD_SEP = ("Haddad, Noor Ul Ain", "Brennan, Tom", "2026-09-14", "2026-09-20", "ICU1", (
    JUNIOR, JUNIOR, SENIOR, "Aspirational", JUNIOR,
    'Calm under pressure & kind to families. Said "let\'s pause" before a hard '
    "conversation, which helped.",
    "N/A",
))
VAN_DER_BERG_AUG = ("Van der Berg, Anna", "Chen, Wei Ling", "2026-08-03", "2026-08-16", "WARDS2", (
    SENIOR, SENIOR, "Aspirational", SENIOR, "Meets expectations",
    "Leads rounds well.", "Delegate more to the intern.\n\nNotes could be shorter.",
))
LINDQVIST_JUL = ("Lindqvist, Erik", "Alvarez, Rosa", "2026-07-20", "2026-08-02", "WARDS1",
                 (SENIOR,) * 5 + ("Excellent.", ""))
HADDAD_NOOR_SEP = ("Haddad, Noor", "Brennan, Tom", "2026-09-01", "2026-09-07", "NIGHTS",
                   (INTERN,) * 5 + ("Good start.", "Read more."))
# The same evaluation as HADDAD_SEP, with its first answer changed in MedHub.
HADDAD_SEP_CHANGED = HADDAD_SEP[:5] + ((SENIOR,) + HADDAD_SEP[5][1:],)
HADDAD_OCT = ("Haddad, Noor Ul Ain", "Alvarez, Rosa", "2026-10-05", "2026-10-18", "WARDS1", (
    SENIOR, JUNIOR, SENIOR, SENIOR, "Aspirational",
    "Ran the team on her own for a week.", "Keep reading around each case.",
))
OKAFOR_OCT = ("Okafor, Chidi", "Chen, Wei Ling", "2026-10-12", "2026-10-18", "ICU2", (
    INTERN, "Unable to Assess", INTERN, JUNIOR, INTERN, "Eager to learn.", "n/a",
))

# The milestone numbers those answers must become (None = no number).
LEVELS = {
    HADDAD_JUL: [2, 3, 2, None, 1],
    HADDAD_SEP: [3, 3, 4, 5, 3],
    HADDAD_SEP_CHANGED: [4, 3, 4, 5, 3],
    VAN_DER_BERG_AUG: [4, 4, 5, 4, None],
    HADDAD_OCT: [4, 3, 4, 4, 5],
    OKAFOR_OCT: [2, None, 2, 3, 2],
}

INPATIENT_MAP = [
    (Q1, "milestone", "Patient Care"),
    (Q2, "milestone", "Medical Knowledge"),
    (Q3, "milestone", "Patient Care"),
    (Q4, "milestone", "Practice-Based Learning & Improvement"),
    (Q5, "milestone", "Interpersonal & Communication Skills"),
    (Q6, "comment", "No single competency"),
    (Q7, "comment", "No single competency"),
]


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _rows_of(form, sections):
    """Expected rows in report order: each resident's section, question by
    question, one row per evaluation."""
    rows = []
    for evaluations in sections:
        for qi, question in enumerate(INPATIENT_QUESTIONS):
            for resident, evaluator, start, end, service, answers in evaluations:
                rows.append((form, resident, evaluator, start, end, service, question, answers[qi]))
    return rows


def _as_tuples(report):
    return [
        (r.form, r.resident, r.evaluator, r.rotation_start, r.rotation_end, r.service,
         r.question, r.answer)
        for r in report.rows
    ]


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

def test_every_row_of_the_report_is_read_with_the_right_values():
    report = mr.read_report(_fixture("eqs_inpatient_jul_sep.html"))
    assert report.report_type == "Evaluation Questions Summary"
    assert report.generated == "10/01/2026 8:15am EDT"
    assert [(f.name, f.date_range) for f in report.forms] == [(INPATIENT, "07/01/26-09/30/26")]
    assert _as_tuples(report) == _rows_of(INPATIENT, [
        [HADDAD_JUL, HADDAD_SEP], [VAN_DER_BERG_AUG], [LINDQVIST_JUL], [HADDAD_NOOR_SEP],
    ])


def test_the_overlapping_report_is_read_and_dr_is_dropped_from_the_target():
    report = mr.read_report(_fixture("eqs_inpatient_sep_oct.html"))
    assert _as_tuples(report) == _rows_of(INPATIENT, [
        [HADDAD_SEP_CHANGED, HADDAD_OCT], [OKAFOR_OCT], [HADDAD_NOOR_SEP],
    ])


def test_a_second_form_with_its_own_questions_and_scale():
    report = mr.read_report(_fixture("eqs_subspecialty.html"))
    assert _as_tuples(report) == [
        (SUBSPECIALTY, "Van der Berg, Anna", "Dube, Thandi", "2026-10-05", "2026-10-09", "Y", q, a)
        for q, a in [(S1, "To a great extent"), (S2, "To some extent"),
                     (S3, "Curious and engaged in clinic."), (S4, "N/A")]
    ]


def test_one_file_can_hold_more_than_one_form():
    report = mr.read_report(_fixture("eqs_two_forms.html"))
    assert [f.name for f in report.forms] == [INPATIENT, SUBSPECIALTY]
    assert {r.form for r in report.rows[:7]} == {INPATIENT}
    assert {r.form for r in report.rows[7:]} == {SUBSPECIALTY}
    assert len(report.rows) == 11


def test_a_report_with_no_evaluations_reads_as_empty():
    report = mr.read_report(_fixture("eqs_empty.html"))
    assert [f.name for f in report.forms] == [INPATIENT]
    assert report.rows == []


def test_other_files_are_refused_with_a_reason():
    comments_report = (
        b"<html><head><title>Aggregate Comments Report</title></head><body><table>"
        b"<tr><th>Date Completed:</th><th>Evaluator:</th></tr></table></body></html>"
    )
    with pytest.raises(mr.MedhubReportError, match="Aggregate Comments Report"):
        mr.read_report(comments_report)
    with pytest.raises(mr.MedhubReportError, match="not a MedHub Evaluation Questions Summary"):
        mr.read_report(b"<html><body><p>Hello</p></body></html>")


def test_a_changed_layout_is_refused_rather_than_misread():
    page = _fixture("eqs_inpatient_jul_sep.html")
    with pytest.raises(mr.MedhubReportError, match="missing: service"):
        mr.read_report(page.replace(b"<th>Service:</th>", b"<th>Site:</th>"))
    with pytest.raises(mr.MedhubReportError, match="rotation dates"):
        mr.read_report(page.replace(b"7/6/2026-7/19/2026", b"early July"))


def test_answers_become_milestone_numbers():
    assert mr.milestone_level("Conditional on improvement (novice)") == ("rated", 1)
    assert mr.milestone_level("Intern") == ("rated", 2)
    assert mr.milestone_level("Intern (advanced beginner)") == ("rated", 2)
    assert mr.milestone_level("Junior Resident") == ("rated", 3)
    assert mr.milestone_level("Senior Resident (proficient learner)") == ("rated", 4)
    assert mr.milestone_level("Aspirational") == ("rated", 5)
    assert mr.milestone_level("Unable to Assess") == ("unable", None)
    assert mr.milestone_level("") == ("blank", None)
    assert mr.milestone_level("To a great extent") == ("unrecognised", None)


def test_only_an_empty_answer_is_blank_and_only_n_a_is_n_a():
    assert mr.comment_kind("") == "blank"
    assert mr.comment_kind("   ") == "blank"
    assert mr.comment_kind("N/A") == "na"
    assert mr.comment_kind("n/a.") == "na"
    assert mr.comment_kind("None") == "text"
    assert mr.comment_kind("N/A - great rotation") == "text"


def test_names_are_compared_whole_without_dr():
    assert mr.name_key("Dr. Abiddin, Zain Ul") == mr.name_key("Abiddin, Zain Ul")
    assert mr.name_key("dr abiddin ,  zain ul") == mr.name_key("Abiddin, Zain Ul")
    assert mr.display_name("Dr.  Van der Berg ,Anna") == "Van der Berg, Anna"
    assert mr.name_key("Abiddin, Zain") != mr.name_key("Abiddin, Zain Ul")
    assert mr.name_key("Haddad, Noor") != mr.name_key("Haddad, Noor Ul Ain")
    # "Dr" only as a title, never the start of a surname.
    assert mr.display_name("Drake, Nora") == "Drake, Nora"


def test_the_same_evaluation_has_the_same_fingerprint_in_every_export():
    first = mr.read_report(_fixture("eqs_inpatient_jul_sep.html")).rows
    second = mr.read_report(_fixture("eqs_inpatient_sep_oct.html")).rows
    sep_in_first = [r for r in first if r.evaluator == "Brennan, Tom" and r.resident.endswith("Ain")]
    sep_in_second = [r for r in second if r.evaluator == "Brennan, Tom" and r.resident.endswith("Ain")]
    assert [mr.fingerprint(r) for r in sep_in_first] == [mr.fingerprint(r) for r in sep_in_second]
    # Different rotation, different evaluation.
    oct_rows = [r for r in second if r.rotation_start == "2026-10-05"]
    assert not {mr.fingerprint(r) for r in oct_rows} & {mr.fingerprint(r) for r in first}


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------

def _resident(client, first, last, medhub_name=None, pgy=2):
    created = client.post("/api/residents", json={
        "first_name": first, "last_name": last, "pgy_year": pgy,
    })
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    if medhub_name is not None:
        updated = client.put(f"/api/residents/{rid}", json={"medhub_name": medhub_name})
        assert updated.status_code == 200, updated.text
    return rid


def _roster(client):
    return {
        "haddad": _resident(client, "Noor Ul Ain", "Haddad", "Haddad, Noor Ul Ain"),
        "vdb": _resident(client, "Anna", "Van der Berg", "Van der Berg, Anna"),
        "okafor": _resident(client, "Chidi", "Okafor", "Okafor, Chidi"),
    }


def _map(client, form, items):
    response = client.put("/api/medhub/question-map", json={"items": [
        {"form_name": form, "question": q, "question_type": t, "competency": c}
        for q, t, c in items
    ]})
    assert response.status_code == 200, response.text


def _send(client, step, name):
    return client.post(
        f"/api/medhub/{step}", files={"file": (name, _fixture(name), "text/html")}
    )


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _stored(app_module, resident_id=None):
    with app_module.db_connection() as conn:
        sql = ("SELECT medhub_name, evaluator, rotation_start, rotation_end, service, question, "
               "answer, answer_kind, level FROM medhub_answers")
        rows = conn.execute(sql + (" WHERE resident_id = ?" if resident_id else ""),
                            (resident_id,) if resident_id else ()).fetchall()
    return {tuple(r) for r in rows}


def _expected_stored(*evaluations):
    """What the database must hold for these evaluations under INPATIENT_MAP."""
    rows = set()
    for evaluation in evaluations:
        resident, evaluator, start, end, service, answers = evaluation
        for qi, question in enumerate(INPATIENT_QUESTIONS):
            answer = answers[qi]
            if qi < 5:
                level = LEVELS[evaluation][qi]
                kind = "rated" if level else mr.milestone_level(answer)[0]
            else:
                level, kind = None, mr.comment_kind(answer)
            rows.add((resident, evaluator, start, end, service, question, answer, kind, level))
    return rows


def test_the_import_endpoints_need_a_login(client):
    assert _send(client, "preview", "eqs_inpatient_jul_sep.html").status_code == 401
    assert client.get("/api/medhub/import-log").status_code == 401


def test_preview_saves_nothing_and_asks_for_every_unmapped_question(logged_in, app_module):
    _roster(logged_in)
    preview = _ok(_send(logged_in, "preview", "eqs_inpatient_jul_sep.html"))
    assert preview["can_save"] is False
    unmapped = {q["question"]: q["suggested_type"] for q in preview["unmapped_questions"]}
    # Q5 has a "Meets expectations" answer, so it is not pre-set as a milestone question.
    assert unmapped == {Q1: "milestone", Q2: "milestone", Q3: "milestone", Q4: "milestone",
                        Q5: None, Q6: None, Q7: None}
    assert _send(logged_in, "import", "eqs_inpatient_jul_sep.html").status_code == 409
    assert _stored(app_module) == set()
    assert logged_in.get("/api/medhub/import-log").json() == []


def test_every_score_and_comment_is_stored_with_the_right_values(logged_in, app_module):
    ids = _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)

    preview = _ok(_send(logged_in, "preview", "eqs_inpatient_jul_sep.html"))
    assert preview["can_save"] is True
    assert preview["counts"]["new"] == 21
    assert {n["name"]: n["rows"] for n in preview["not_in_roster"]} == {
        "Lindqvist, Erik": 7, "Haddad, Noor": 7,
    }
    assert _stored(app_module) == set(), "preview must not save"

    result = _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    assert result["counts"]["new"] == 21
    assert _stored(app_module, ids["haddad"]) == _expected_stored(HADDAD_JUL, HADDAD_SEP)
    assert _stored(app_module, ids["vdb"]) == _expected_stored(VAN_DER_BERG_AUG)
    assert _stored(app_module, ids["okafor"]) == set()

    haddad = _ok(logged_in.get(f"/api/medhub/residents/{ids['haddad']}"))
    assert haddad["blank_comments"] == 1
    assert haddad["na_comments"] == 1
    assert [c["answer"] for c in haddad["comments"]] == [HADDAD_JUL[5][5], HADDAD_SEP[5][5]]
    assert [c["rotation_end"] for c in haddad["comments"]] == ["2026-07-19", "2026-09-20"]
    assert len(haddad["ratings"]) == 10
    unable = [r for r in haddad["ratings"] if r["answer"] == "Unable to Assess"]
    assert [(r["level"], r["competency"]) for r in unable] == [
        (None, "Practice-Based Learning & Improvement")
    ]


def test_re_importing_the_same_file_adds_nothing(logged_in, app_module):
    _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    before = _stored(app_module)

    again = _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    assert again["counts"]["new"] == 0
    assert again["counts"]["changed"] == 0
    assert again["counts"]["duplicates"] == 21
    assert _stored(app_module) == before
    with app_module.db_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM medhub_answers").fetchone()[0] == 21


def test_an_overlapping_export_adds_only_what_is_new(logged_in, app_module):
    ids = _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))

    preview = _ok(_send(logged_in, "preview", "eqs_inpatient_sep_oct.html"))
    assert preview["changed"] == [{
        "medhub_name": "Haddad, Noor Ul Ain", "evaluator": "Brennan, Tom",
        "form_name": INPATIENT, "question": Q1,
        "rotation_start": "2026-09-14", "rotation_end": "2026-09-20",
        "old_answer": JUNIOR, "new_answer": SENIOR,
    }]
    result = _ok(_send(logged_in, "import", "eqs_inpatient_sep_oct.html"))
    assert result["counts"]["new"] == 14
    assert result["counts"]["changed"] == 1
    assert result["counts"]["duplicates"] == 6
    assert _stored(app_module, ids["haddad"]) == _expected_stored(
        HADDAD_JUL, HADDAD_SEP_CHANGED, HADDAD_OCT
    )
    assert _stored(app_module, ids["okafor"]) == _expected_stored(OKAFOR_OCT)
    okafor = _ok(logged_in.get(f"/api/medhub/residents/{ids['okafor']}"))
    assert okafor["na_comments"] == 1
    # Counts of blank and N/A comments survive the overlap without doubling.
    haddad = _ok(logged_in.get(f"/api/medhub/residents/{ids['haddad']}"))
    assert (haddad["blank_comments"], haddad["na_comments"]) == (1, 1)


def test_unmatched_names_are_listed_and_never_guessed(logged_in, app_module):
    _roster(logged_in)
    # Same first and last name as the report, but no MedHub name set: not a match.
    erik = _resident(logged_in, "Erik", "Lindqvist")
    _map(logged_in, INPATIENT, INPATIENT_MAP)

    result = _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    assert [n["name"] for n in result["not_in_roster"]] == ["Lindqvist, Erik", "Haddad, Noor"]
    assert result["counts"]["unmatched_rows"] == 14
    assert _stored(app_module, erik) == set()
    assert not any(row[0] == "Haddad, Noor" for row in _stored(app_module))


def test_a_name_linked_in_the_preview_is_remembered_and_imported(logged_in, app_module):
    _roster(logged_in)
    erik = _resident(logged_in, "Erik", "Lindqvist")
    _map(logged_in, INPATIENT, INPATIENT_MAP)

    linked = logged_in.post("/api/medhub/link-name", json={
        "name": "Lindqvist, Erik", "resident_id": erik,
    })
    assert linked.status_code == 200, linked.text
    assert logged_in.get(f"/api/residents/{erik}").json()["medhub_name"] == "Lindqvist, Erik"

    preview = _ok(_send(logged_in, "preview", "eqs_inpatient_jul_sep.html"))
    assert [n["name"] for n in preview["not_in_roster"]] == ["Haddad, Noor"]
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    assert len(_stored(app_module, erik)) == 7

    # A resident who already has a different MedHub name is not silently re-pointed.
    clash = logged_in.post("/api/medhub/link-name", json={"name": "Haddad, Noor", "resident_id": erik})
    assert clash.status_code == 409


def test_ignored_names_are_skipped_and_stay_ignored(logged_in, app_module):
    _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    assert logged_in.post("/api/medhub/ignored-names", json={"name": "Haddad, Noor"}).status_code == 200

    first = _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    assert [n["name"] for n in first["not_in_roster"]] == ["Lindqvist, Erik"]
    assert first["ignored_names_in_file"] == [{"name": "Haddad, Noor", "rows": 7}]
    assert first["counts"]["ignored_name_rows"] == 7

    # Still ignored on a later import of a different file.
    later = _ok(_send(logged_in, "preview", "eqs_inpatient_sep_oct.html"))
    assert later["not_in_roster"] == []
    assert later["ignored_names_in_file"] == [{"name": "Haddad, Noor", "rows": 7}]
    assert not any(row[0] == "Haddad, Noor" for row in _stored(app_module))

    assert [n["name"] for n in logged_in.get("/api/medhub/ignored-names").json()] == ["Haddad, Noor"]
    logged_in.delete("/api/medhub/ignored-names", params={"name": "haddad, noor"})
    back = _ok(_send(logged_in, "preview", "eqs_inpatient_sep_oct.html"))
    assert [n["name"] for n in back["not_in_roster"]] == ["Haddad, Noor"]


def test_a_roster_name_cannot_be_ignored(logged_in):
    _roster(logged_in)
    response = logged_in.post("/api/medhub/ignored-names", json={"name": "Dr. Haddad, Noor Ul Ain"})
    assert response.status_code == 409


def test_multi_part_names_match_only_in_full(logged_in, app_module):
    ids = _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    result = _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))

    # "Haddad, Noor Ul Ain" gets her two evaluations; "Haddad, Noor" — a
    # different person whose name is the start of hers — gets nothing.
    assert {r[0] for r in _stored(app_module, ids["haddad"])} == {"Haddad, Noor Ul Ain"}
    assert len(_stored(app_module, ids["haddad"])) == 14
    assert "Haddad, Noor" in [n["name"] for n in result["not_in_roster"]]
    # A three-word surname matches too.
    assert len(_stored(app_module, ids["vdb"])) == 7


def test_a_medhub_name_belongs_to_one_resident(logged_in):
    ids = _roster(logged_in)
    other = _resident(logged_in, "Noor", "Haddad")
    clash = logged_in.put(f"/api/residents/{other}", json={"medhub_name": "dr. HADDAD, Noor Ul Ain"})
    assert clash.status_code == 409
    cleared = logged_in.put(f"/api/residents/{ids['haddad']}", json={"medhub_name": ""})
    assert cleared.status_code == 200
    assert logged_in.get(f"/api/residents/{ids['haddad']}").json()["medhub_name"] is None


def test_ignored_questions_are_not_stored(logged_in, app_module):
    ids = _roster(logged_in)
    _map(logged_in, SUBSPECIALTY, [
        (S1, "ignore", None), (S2, "ignore", None),
        (S3, "comment", "No single competency"), (S4, "comment", "No single competency"),
    ])
    result = _ok(_send(logged_in, "import", "eqs_subspecialty.html"))
    assert result["counts"]["ignored_question_rows"] == 2
    assert result["counts"]["new"] == 2
    vdb = _ok(logged_in.get(f"/api/medhub/residents/{ids['vdb']}"))
    assert vdb["ratings"] == []
    assert [c["answer"] for c in vdb["comments"]] == ["Curious and engaged in clinic."]
    assert vdb["na_comments"] == 1


def test_changing_a_question_type_re_reads_its_stored_answers(logged_in):
    ids = _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))

    _map(logged_in, INPATIENT, [(Q5, "comment", "Interpersonal & Communication Skills")])
    vdb = _ok(logged_in.get(f"/api/medhub/residents/{ids['vdb']}"))
    assert "Meets expectations" in [c["answer"] for c in vdb["comments"]]
    assert all(r["question"] != Q5 for r in vdb["ratings"])

    _map(logged_in, INPATIENT, [(Q5, "milestone", "Interpersonal & Communication Skills")])
    haddad = _ok(logged_in.get(f"/api/medhub/residents/{ids['haddad']}"))
    assert sorted(r["level"] for r in haddad["ratings"] if r["question"] == Q5) == [1, 3]


def test_a_mapping_needs_a_competency_unless_ignored(logged_in):
    response = logged_in.put("/api/medhub/question-map", json={"items": [
        {"form_name": INPATIENT, "question": Q1, "question_type": "milestone", "competency": None},
    ]})
    assert response.status_code == 400
    mapping = logged_in.get("/api/medhub/question-map").json()
    assert mapping["items"] == []
    assert mapping["competencies"][-1] == "No single competency"
    assert len(mapping["competencies"]) == 7


def test_every_import_is_logged(logged_in):
    _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    _ok(_send(logged_in, "import", "eqs_inpatient_sep_oct.html"))

    log = logged_in.get("/api/medhub/import-log").json()
    assert [
        (e["file_name"], e["report_type"], e["new_rows"], e["duplicates_skipped"],
         e["changed_rows"], e["unmatched_names"])
        for e in log
    ] == [
        ("eqs_inpatient_sep_oct.html", "Evaluation Questions Summary", 14, 6, 1, 1),
        ("eqs_inpatient_jul_sep.html", "Evaluation Questions Summary", 0, 21, 0, 2),
        ("eqs_inpatient_jul_sep.html", "Evaluation Questions Summary", 21, 0, 0, 2),
    ]
    assert all(e["imported_at"] for e in log)
    assert log[0]["forms"] == [{"name": INPATIENT, "date_range": "09/01/26-10/19/26"}]
    status = logged_in.get("/api/medhub/status").json()
    assert status["last_import"]["file_name"] == "eqs_inpatient_sep_oct.html"


def test_an_empty_report_cannot_be_saved(logged_in):
    preview = _ok(_send(logged_in, "preview", "eqs_empty.html"))
    assert preview["can_save"] is False
    assert preview["blocking_reason"] == "This report has no evaluations in it."
    assert _send(logged_in, "import", "eqs_empty.html").status_code == 409


def test_only_html_files_are_accepted(logged_in):
    response = logged_in.post(
        "/api/medhub/preview", files={"file": ("report.csv", b"a,b\n1,2\n", "text/csv")}
    )
    assert response.status_code == 400


def test_comments_reach_summaries_but_scores_blanks_and_n_a_do_not(logged_in, app_module):
    ids = _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))

    # Haddad has no committee notes; her MedHub comments are enough.
    _resident_row, _label, evidence = app_module._load_resident_evidence(ids["haddad"])
    assert [e["text"] for e in evidence] == [HADDAD_JUL[5][5], HADDAD_SEP[5][5]]
    assert evidence[0]["label"].startswith(f"MedHub comment, {INPATIENT}, evaluator=Alvarez, Rosa, "
                                           "rotation 2026-07-06 to 2026-07-19, service=WARDS1")

    # Okafor has nothing at all, so there is nothing to summarize.
    with pytest.raises(app_module.HTTPException) as refused:
        app_module._load_resident_evidence(ids["okafor"])
    assert refused.value.status_code == 400


def test_deleting_a_resident_removes_their_medhub_rows(logged_in, app_module):
    ids = _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    logged_in.delete(f"/api/residents/{ids['haddad']}")
    assert _stored(app_module, ids["haddad"]) == set()
    assert len(_stored(app_module)) == 7


def test_medhub_data_survives_a_restart(logged_in, app_module):
    ids = _roster(logged_in)
    _map(logged_in, INPATIENT, INPATIENT_MAP)
    logged_in.post("/api/medhub/ignored-names", json={"name": "Haddad, Noor"})
    _ok(_send(logged_in, "import", "eqs_inpatient_jul_sep.html"))
    with TestClient(app_module.app) as restarted:
        restarted.post("/api/auth/login", json={"password": TEST_PASSWORD})
        assert len(restarted.get(f"/api/medhub/residents/{ids['haddad']}").json()["ratings"]) == 10
        assert len(restarted.get("/api/medhub/question-map").json()["items"]) == 7
        assert restarted.get("/api/medhub/ignored-names").json()[0]["name"] == "Haddad, Noor"
