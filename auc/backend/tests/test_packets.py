"""Advisor packets: meetings, randomization, generation, withholding, downloads,
and the study export."""

import csv
import io
import json
import time
import zipfile

import packets
import pdf_export
import pytest
from evidence_helpers import (
    StubModel,
    add_comment,
    add_import,
    add_rating,
    add_resident,
    connect,
    map_questions,
)

MEETING = "2027-01-12"   # cutoff 2027-01-05


def _advisor(client, name, in_study=True):
    aid = client.post("/api/advisors", json={"name": name}).json()["id"]
    if not in_study:
        assert client.put(f"/api/advisors/{aid}", json={"in_study": False}).status_code == 200
    return aid


def _advisees(client, aid, n, prefix):
    return [add_resident(client, first=f"{prefix}{i}", last=f"{prefix}son{i}", advisor_id=aid)
            for i in range(n)]


def _meeting(client, date=MEETING):
    body = client.post("/api/packets/meetings", json={"meeting_date": date}).json()
    return body["meeting"]["id"], body


def _present(client, mid, *advisor_ids):
    response = client.post(f"/api/packets/meetings/{mid}/presenters",
                           json={"advisor_ids": list(advisor_ids)})
    assert response.status_code == 200, response.text
    return response.json()


def _rows(state):
    return {r["resident_id"]: r for p in state["presenters"] for r in p["residents"]}


def _wait(client, mid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/packets/meetings/{mid}").json()
        if not {"queued", "running"} & set(state["status_counts"]):
            return state
        time.sleep(0.05)
    raise AssertionError("generation did not finish")


# ---------------------------------------------------------------------------
# Meetings and randomization
# ---------------------------------------------------------------------------

def test_a_meeting_gets_its_cutoff_from_the_lead_days(logged_in):
    _mid, body = _meeting(logged_in)
    assert body["meeting"]["cutoff_date"] == "2027-01-05"
    again = logged_in.post("/api/packets/meetings", json={"meeting_date": MEETING}).json()
    assert again["meeting"]["id"] == body["meeting"]["id"]
    assert logged_in.get("/api/packets/defaults?meeting_date=2027-03-10").json()[
        "cutoff_date"] == "2027-03-03"


def test_randomize_splits_each_advisor_evenly_and_skips_those_outside(logged_in):
    hopper = _advisor(logged_in, "Grace Hopper")
    turing = _advisor(logged_in, "Alan Turing")
    outside = _advisor(logged_in, "Ada Outside", in_study=False)
    _advisees(logged_in, hopper, 4, "H")
    _advisees(logged_in, turing, 5, "T")
    _advisees(logged_in, outside, 2, "O")
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, hopper, turing, outside)

    state = logged_in.post(f"/api/packets/meetings/{mid}/randomize").json()
    seed = state["meeting"]["randomization_seed"]
    assert seed and state["meeting"]["randomized_at"]
    by_advisor = {p["advisor_id"]: p for p in state["presenters"]}

    hopper_groups = [r["study_group"] for r in by_advisor[hopper]["residents"]]
    assert sorted(hopper_groups) == ["packet_delayed"] * 2 + ["packet_first"] * 2
    turing_groups = [r["study_group"] for r in by_advisor[turing]["residents"]]
    assert sorted(set(turing_groups)) == ["packet_delayed", "packet_first"]
    assert abs(turing_groups.count("packet_first") - turing_groups.count("packet_delayed")) == 1
    for r in by_advisor[outside]["residents"]:
        assert (r["study_group"], r["randomized_group"], r["randomization_seed"]) == (
            "none", None, None)

    # Repeatable from the stored seed, with the residents sorted by study code.
    for advisor in (hopper, turing):
        presenter = by_advisor[advisor]
        rows = presenter["residents"]
        assert all(r["randomization_seed"] == seed for r in rows)
        expected = packets.split_advisees(seed, presenter["advisor_code"],
                                          [r["study_code"] for r in rows])
        assert {r["study_code"]: r["randomized_group"] for r in rows} == expected


def test_split_advisees_is_deterministic_and_balanced():
    codes = [f"R{i:03d}" for i in range(1, 8)]
    first = packets.split_advisees("12345", "A01", codes)
    assert first == packets.split_advisees("12345", "A01", list(reversed(codes)))
    counts = sorted(list(first.values()).count(g) for g in ("packet_first", "packet_delayed"))
    assert counts == [3, 4]
    # Over many seeds the odd resident goes to each group, not always the same one.
    extras = {
        list(packets.split_advisees(str(s), "A01", codes).values()).count("packet_first")
        for s in range(40)
    }
    assert extras == {3, 4}


def test_randomize_cannot_run_twice(logged_in):
    aid = _advisor(logged_in, "Grace Hopper")
    _advisees(logged_in, aid, 2, "H")
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    first = logged_in.post(f"/api/packets/meetings/{mid}/randomize").json()
    again = logged_in.post(f"/api/packets/meetings/{mid}/randomize")
    assert again.status_code == 409
    after = logged_in.get(f"/api/packets/meetings/{mid}").json()
    assert after["meeting"]["randomization_seed"] == first["meeting"]["randomization_seed"]
    assert [r["study_group"] for r in _rows(after).values()] == [
        r["study_group"] for r in _rows(first).values()]


def test_a_manual_change_needs_a_reason_and_keeps_the_randomized_group(logged_in):
    aid = _advisor(logged_in, "Grace Hopper")
    _advisees(logged_in, aid, 2, "H")
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    state = logged_in.post(f"/api/packets/meetings/{mid}/randomize").json()
    row = next(iter(_rows(state).values()))
    other = "packet_first" if row["study_group"] == "packet_delayed" else "packet_delayed"

    refused = logged_in.put(f"/api/packets/assignments/{row['id']}/group",
                            json={"study_group": other})
    assert refused.status_code == 400

    changed = logged_in.put(f"/api/packets/assignments/{row['id']}/group",
                            json={"study_group": other, "reason": "Advisor swapped dates"}).json()
    after = _rows(changed)[row["resident_id"]]
    assert after["study_group"] == other
    assert after["randomized_group"] == row["randomized_group"]
    assert after["group_change_reason"] == "Advisor swapped dates"
    assert after["group_changed_at"]


def test_groups_set_by_hand_carry_no_seed_and_block_randomizing(logged_in):
    aid = _advisor(logged_in, "Grace Hopper")
    _advisees(logged_in, aid, 2, "H")
    mid, state = _meeting(logged_in)
    state = _present(logged_in, mid, aid)
    row = next(iter(_rows(state).values()))
    set_by_hand = logged_in.put(f"/api/packets/assignments/{row['id']}/group",
                                json={"study_group": "packet_first"}).json()
    after = _rows(set_by_hand)[row["resident_id"]]
    assert (after["study_group"], after["randomization_seed"], after["randomized_group"]) == (
        "packet_first", None, None)
    assert logged_in.post(f"/api/packets/meetings/{mid}/randomize").status_code == 409


def test_a_resident_added_late_gets_a_coin_flip_and_changes_nobody_else(logged_in):
    aid = _advisor(logged_in, "Grace Hopper")
    _advisees(logged_in, aid, 3, "H")
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    before = _rows(logged_in.post(f"/api/packets/meetings/{mid}/randomize").json())

    late = add_resident(logged_in, first="Late", last="Comer", advisor_id=aid)
    state = logged_in.get(f"/api/packets/meetings/{mid}").json()
    assert [r["id"] for r in state["presenters"][0]["advisees_not_in_meeting"]] == [late]
    after = _rows(logged_in.post(f"/api/packets/meetings/{mid}/assignments",
                                 json={"resident_id": late}).json())
    for rid, row in before.items():
        assert (after[rid]["study_group"], after[rid]["randomization_seed"]) == (
            row["study_group"], row["randomization_seed"])
    new = after[late]
    assert new["added_late"] == 1
    assert new["randomization_seed"] and new["randomization_seed"] != row["randomization_seed"]
    assert new["study_group"] == packets.coin_flip(new["randomization_seed"], new["study_code"])


def test_an_advisor_added_late_gets_their_own_even_split(logged_in):
    hopper = _advisor(logged_in, "Grace Hopper")
    turing = _advisor(logged_in, "Alan Turing")
    _advisees(logged_in, hopper, 2, "H")
    _advisees(logged_in, turing, 4, "T")
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, hopper)
    meeting_seed = logged_in.post(f"/api/packets/meetings/{mid}/randomize").json()[
        "meeting"]["randomization_seed"]
    state = _present(logged_in, mid, turing)
    late = next(p for p in state["presenters"] if p["advisor_id"] == turing)
    groups = [r["study_group"] for r in late["residents"]]
    assert sorted(groups) == ["packet_delayed"] * 2 + ["packet_first"] * 2
    assert late["randomization_seed"] and late["randomization_seed"] != meeting_seed


def test_moving_an_advisor_keeps_every_group_and_records_both_dates(logged_in, app_module):
    aid = _advisor(logged_in, "Grace Hopper")
    _advisees(logged_in, aid, 3, "H")
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    before = _rows(logged_in.post(f"/api/packets/meetings/{mid}/randomize").json())

    moved = logged_in.post(f"/api/packets/meetings/{mid}/presenters/{aid}/move",
                           json={"meeting_date": "2027-02-09"})
    assert moved.status_code == 200, moved.text
    target = moved.json()
    assert target["meeting"]["meeting_date"] == "2027-02-09"
    assert target["meeting"]["cutoff_date"] == "2027-02-02"
    after = _rows(target)
    for rid, row in before.items():
        assert after[rid]["study_group"] == row["study_group"]
        assert after[rid]["randomized_group"] == row["randomized_group"]
        assert after[rid]["group_change_reason"] is None
        assert after[rid]["moved_from_meeting_date"] == MEETING
    assert logged_in.get(f"/api/packets/meetings/{mid}").json()["presenters"] == []

    conn = connect(app_module)
    moves = conn.execute("SELECT old_value, new_value FROM packet_events WHERE event = 'moved'"
                         ).fetchall()
    assert {(m["old_value"], m["new_value"]) for m in moves} == {(MEETING, "2027-02-09")}


def test_a_randomized_advisor_cannot_be_removed_only_moved(logged_in):
    aid = _advisor(logged_in, "Grace Hopper")
    _advisees(logged_in, aid, 2, "H")
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    logged_in.post(f"/api/packets/meetings/{mid}/randomize")
    assert logged_in.delete(f"/api/packets/meetings/{mid}/presenters/{aid}").status_code == 409


# ---------------------------------------------------------------------------
# Generation, withholding, downloads
# ---------------------------------------------------------------------------

def _three_residents(client, app_module):
    """Many comments, few comments, and none in the period."""
    aid = _advisor(client, "Grace Brewster Murray-Hopper")
    many = add_resident(client, first="Maximiliana", last="Featherstonehaugh-Whittingham",
                        advisor_id=aid)
    few = add_resident(client, first="Ryan", last="Chen", advisor_id=aid)
    none_ = add_resident(client, first="Renée", last="Muñoz-Ødegård", advisor_id=aid)
    conn = connect(app_module)
    map_questions(conn)
    imported = add_import(conn, "2027-01-04 08:00:00")
    for i in range(14):
        add_comment(conn, many, f"Excellent history taking on admission {i}; real follow-through "
                    f"on discharge summaries and clear communication with the family.",
                    f"2026-{7 + i % 6:02d}-1{i % 9}", imported,
                    evaluator=f"Evaluator{i % 5}, Long Name With Several Parts {i % 5}",
                    service="INTERNAL MEDICINE WARDS TEAM BLUE (NIGHT FLOAT COVERAGE ROTATION)")
        add_rating(conn, many, "Junior Resident (competent learner)", f"2026-0{7 + i % 3}-15",
                   imported)
    add_rating(conn, many, "Unable to Assess", "2026-08-15", imported, competency="Professionalism")
    add_comment(conn, few, "Good history taking.", "2026-09-01", imported)
    add_comment(conn, none_, "Long ago.", "2026-01-15", imported)
    conn.close()
    return aid, many, few, none_


def test_packets_generate_download_and_withhold(logged_in, app_module, monkeypatch):
    StubModel(monkeypatch)
    aid, many, few, none_ = _three_residents(logged_in, app_module)
    mid, _ = _meeting(logged_in)
    state = _present(logged_in, mid, aid)
    previews = {rid: r["preview"] for rid, r in _rows(state).items()}
    assert previews[many]["counts"]["n_comments"] == 14
    assert previews[few]["thin_evidence"] is True
    assert previews[none_]["counts"]["n_comments"] == 0

    assert logged_in.post(f"/api/packets/meetings/{mid}/generate", json={}).json()["queued"] == 3
    state = _wait(logged_in, mid)
    rows = _rows(state)
    assert {r["status"] for r in rows.values()} == {"done"}, [r["status_detail"] for r in rows.values()]

    conn = connect(app_module)
    packet = conn.execute("SELECT * FROM packets WHERE id = ?", (rows[many]["packet_id"],)).fetchone()
    assert len(packet["id"]) == 8
    assert (packet["period_start"], packet["period_end"], packet["cutoff_date"]) == (
        "2026-06-23", "2027-01-05", "2027-01-05")
    assert packet["n_comments"] == 14 and packet["study_code"].startswith("R")
    assert packet["advisor_code"].startswith("A") and packet["packet_format_version"]
    assert packet["model"] and packet["app_version"] and packet["retrieval_engine"]
    summary = conn.execute("SELECT * FROM summaries WHERE id = ?", (packet["summary_id"],)).fetchone()
    assert summary["evidence_scope"] == "medhub_only" and summary["approved"] == 0
    report = json.loads(summary["ai_draft"])
    assert report["rating_tallies"]["PC"]["Junior resident"] == 14
    assert report["rating_tallies"]["PROF"]["Unable to assess"] == 1
    assert len(report["competencies"]) == 6

    # One advisor's zip, and the whole meeting's.
    zipped = logged_in.get(f"/api/packets/meetings/{mid}/download?advisor_id={aid}")
    assert zipped.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert "packet-Murray-Hopper-Chen-2027-01-05.pdf" in names
    assert "packet-Murray-Hopper-Munoz-degard-2027-01-05.pdf" in names
    assert len(names) == 3
    whole = zipfile.ZipFile(io.BytesIO(
        logged_in.get(f"/api/packets/meetings/{mid}/download").content)).namelist()
    assert all(n.startswith("Murray-Hopper/") for n in whole) and len(whole) == 3
    assert conn.execute("SELECT COUNT(*) FROM packets WHERE first_downloaded_at IS NULL"
                        ).fetchone()[0] == 0

    # Withheld: needs a reason, then is in no batch.
    pid = rows[few]["packet_id"]
    assert logged_in.post(f"/api/packets/{pid}/withhold", json={"reason": " "}).status_code == 400
    assert logged_in.post(f"/api/packets/{pid}/withhold",
                          json={"reason": "Advisor on leave"}).status_code == 200
    for url in (f"/api/packets/meetings/{mid}/download?advisor_id={aid}",
                f"/api/packets/meetings/{mid}/download"):
        listed = zipfile.ZipFile(io.BytesIO(logged_in.get(url).content)).namelist()
        assert len(listed) == 2 and not any("Chen" in n for n in listed)
    # It can still be opened on its own, to see what was withheld.
    single = logged_in.get(f"/api/packets/{pid}/pdf")
    assert single.status_code == 200 and single.content.startswith(b"%PDF")
    assert conn.execute("SELECT event, reason FROM packet_events WHERE event = 'withheld'"
                        ).fetchone()["reason"] == "Advisor on leave"


def test_a_later_import_does_not_change_a_generated_packet(logged_in, app_module, monkeypatch):
    StubModel(monkeypatch)
    aid, many, _few, _none = _three_residents(logged_in, app_module)
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    logged_in.post(f"/api/packets/meetings/{mid}/generate", json={})
    rows = _rows(_wait(logged_in, mid))
    conn = connect(app_module)
    packet_id = rows[many]["packet_id"]
    before = dict(conn.execute("SELECT * FROM packets WHERE id = ?", (packet_id,)).fetchone())
    draft = conn.execute("SELECT ai_draft FROM summaries WHERE id = ?",
                         (before["summary_id"],)).fetchone()[0]

    later = add_import(conn, "2027-01-08 08:00:00")
    add_comment(conn, many, "Arrived in a later import.", "2026-12-20", later)

    after = dict(conn.execute("SELECT * FROM packets WHERE id = ?", (packet_id,)).fetchone())
    assert after == before
    assert conn.execute("SELECT ai_draft FROM summaries WHERE id = ?",
                        (before["summary_id"],)).fetchone()[0] == draft
    assert "Arrived in a later import." not in draft
    # A new packet would read it.
    preview = _rows(logged_in.get(f"/api/packets/meetings/{mid}").json())[many]["preview"]
    assert preview["counts"]["n_comments"] == 15


def test_a_failed_resident_does_not_stop_the_rest(logged_in, app_module, monkeypatch):
    model = StubModel(monkeypatch)
    aid, many, few, none_ = _three_residents(logged_in, app_module)
    original = model._call

    async def failing(client, model_name, prompt):
        if "Ryan Chen" in prompt:
            raise RuntimeError("boom")
        return await original(client, model_name, prompt)

    import summary_builder
    monkeypatch.setattr(summary_builder, "call_ollama", failing)
    import rag_retrieval

    real_load = rag_retrieval.load_ontology
    calls = {"n": 0}

    def flaky_ontology():
        calls["n"] += 1
        if calls["n"] == 2:   # the second resident in the queue
            raise rag_retrieval.RagUnavailable("index missing")
        return real_load()

    monkeypatch.setattr(rag_retrieval, "load_ontology", flaky_ontology)
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    logged_in.post(f"/api/packets/meetings/{mid}/generate", json={})
    rows = _rows(_wait(logged_in, mid))
    statuses = sorted(r["status"] for r in rows.values())
    assert statuses == ["done", "done", "failed"]
    failed = next(r for r in rows.values() if r["status"] == "failed")
    assert "index missing" in failed["status_detail"]


def test_interrupted_generation_is_marked_failed_on_restart(logged_in, app_module):
    from fastapi.testclient import TestClient

    aid = _advisor(logged_in, "Grace Hopper")
    _advisees(logged_in, aid, 1, "H")
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    conn = connect(app_module)
    conn.execute("UPDATE packet_assignments SET status = 'running'")
    conn.commit()
    with TestClient(app_module.app):
        pass
    row = conn.execute("SELECT status, status_detail FROM packet_assignments").fetchone()
    assert row["status"] == "failed" and "restarted" in row["status_detail"]


# ---------------------------------------------------------------------------
# The study export
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("include_text", [False, True])
def test_the_packets_csv_has_no_forbidden_column(logged_in, app_module, monkeypatch,
                                                 include_text):
    import ccc_export

    StubModel(monkeypatch)
    aid, _many, few, _none = _three_residents(logged_in, app_module)
    mid, _ = _meeting(logged_in)
    _present(logged_in, mid, aid)
    logged_in.post(f"/api/packets/meetings/{mid}/randomize")
    logged_in.post(f"/api/packets/meetings/{mid}/generate", json={})
    rows = _rows(_wait(logged_in, mid))
    logged_in.post(f"/api/packets/{rows[few]['packet_id']}/withhold",
                   json={"reason": "Advisor on leave"})

    response = logged_in.get(
        f"/api/ccc/export/packets.csv?include_text={'true' if include_text else 'false'}")
    assert response.status_code == 200
    assert 'filename="ccc-packets-' in response.headers["content-disposition"]
    table = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header, body = table[0], table[1:]
    assert not ccc_export.FORBIDDEN_COLUMNS.intersection(header)
    assert "study_code" in header and "advisor_code" in header
    assert len(body) == 3
    text = response.content.decode("utf-8-sig")
    for leak in ("Hopper", "Chen", "Muñoz", aid, few):
        assert leak not in text
    assert ("withheld_reason" in header) is include_text
    assert ("Advisor on leave" in text) is include_text
    assert ("group_change_reason" in header) is include_text


def test_the_export_guard_refuses_free_text_without_include_text():
    import ccc_export

    with pytest.raises(RuntimeError):
        ccc_export._check_columns(["study_code", "withheld_reason"], include_text=False)
    with pytest.raises(RuntimeError):
        ccc_export._check_columns(["study_code", "advisor_name"])


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------

V1 = {
    "schema": "auc.summary.v1", "resident": "Ada Lovelace, PGY-2", "model": "m",
    "sections": [
        {"id": "PC1", "name": "History", "domain": "PC", "domain_name": "Patient Care",
         "status": "ok", "level": 3, "narrative": "Careful.",
         "quotes": [{"text": "thorough", "source": "MedHub comment"}]},
        {"id": "MK1", "name": "Knowledge", "domain": "MK", "domain_name": "Medical Knowledge",
         "status": "no_evidence", "level": None, "narrative": "No evidence this cycle",
         "quotes": []},
    ],
}


@pytest.mark.parametrize("schema", ["auc.summary.v1", "auc.summary.v2"])
def test_both_report_schemas_render_to_pdf(schema):
    report = dict(V1, schema=schema)
    if schema == "auc.summary.v2":
        report.update(period={"start": "2026-06-23", "end": "2027-01-05",
                              "basis_description": "Starts at the beginning of the academic year"},
                      counts={"n_comments": 3, "n_evaluators": 2, "n_rotations": 2},
                      thin_evidence=True)
    assert pdf_export._parse_structured(json.dumps(report)) is not None
    pdf = pdf_export.render_summary_pdf(first_name="Ada", last_name="Lovelace", pgy_year=2,
                                        summary_text=json.dumps(report),
                                        approved_date="2027-01-06")
    assert pdf.startswith(b"%PDF")


def _packet_report(n_comments, thin):
    return dict(
        V1, schema="auc.summary.v2", thin_evidence=thin,
        period={"start": "2026-06-23", "end": "2027-01-05",
                "basis_description": "Starts at the beginning of the academic year"},
        counts={"n_comments": n_comments, "n_evaluators": 1, "n_rotations": 1, "n_late": 0},
        rating_tallies={}, previously=None,
        competencies=[{"id": "PC", "name": "Patient Care", "status": "no_evidence",
                       "strengths": None, "growth_areas": None,
                       "note": "Not enough evidence in this review period"}],
    )


@pytest.mark.parametrize("n_comments,thin", [(4, True), (5, False)])
def test_the_thin_evidence_notice_prints_only_below_the_threshold(monkeypatch, n_comments, thin):
    printed = []
    real = pdf_export._notice

    def recording(pdf, text, *args, **kwargs):
        printed.append(text)
        return real(pdf, text, *args, **kwargs)

    monkeypatch.setattr(pdf_export, "_notice", recording)
    pdf_export.render_packet_pdf(
        packet={"id": "abcd1234", "cutoff_date": "2027-01-05", "advisor_name": "Grace Hopper",
                "pgy_year_at_cutoff": 2, "packet_format_version": "1"},
        report=_packet_report(n_comments, thin))
    assert (pdf_export.THIN_EVIDENCE_NOTICE in printed) is thin
    assert pdf_export.PACKET_DISCLAIMER in printed


def test_the_packet_never_prints_the_study_code(monkeypatch):
    written = []
    real = pdf_export._para

    def recording(pdf, text, *args, **kwargs):
        written.append(text)
        return real(pdf, text, *args, **kwargs)

    monkeypatch.setattr(pdf_export, "_para", recording)
    packet = {"id": "abcd1234", "cutoff_date": "2027-01-05", "advisor_name": "Grace Hopper",
              "pgy_year_at_cutoff": 2, "packet_format_version": "1", "study_code": "R042"}
    pdf = pdf_export.render_packet_pdf(packet=packet, report=_packet_report(4, True))
    assert pdf.startswith(b"%PDF")
    assert not any("R042" in t for t in written)
    assert "Not enough evidence in this review period" in written
