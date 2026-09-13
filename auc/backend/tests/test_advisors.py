"""Advisors, and each resident's assigned advisor.

An advisor is organizational — who presents whom at CCC and QI — not evidence
about the resident, so the last test here holds the line that it never reaches
summary generation.
"""

from conftest import TEST_PASSWORD
from fastapi.testclient import TestClient


def _advisor(client, name):
    response = client.post("/api/advisors", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _resident(client):
    response = client.post("/api/residents", json={
        "first_name": "Ada", "last_name": "Lovelace", "pgy_year": 2,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _advisor_of(client, resident_id):
    return client.get(f"/api/residents/{resident_id}").json()["advisor_id"]


def test_advisors_can_be_added_renamed_deactivated_and_reactivated(logged_in):
    aid = _advisor(logged_in, "  Grace Hopper  ")
    assert logged_in.get("/api/advisors").json()[0]["name"] == "Grace Hopper"

    assert logged_in.put(f"/api/advisors/{aid}", json={"name": "Adm. Grace Hopper"}).status_code == 200
    assert logged_in.put(f"/api/advisors/{aid}", json={"active": False}).status_code == 200
    advisor = logged_in.get("/api/advisors").json()[0]
    assert advisor["name"] == "Adm. Grace Hopper"
    assert advisor["active"] == 0

    logged_in.put(f"/api/advisors/{aid}", json={"active": True})
    assert logged_in.get("/api/advisors").json()[0]["active"] == 1


def test_an_advisor_needs_a_name(logged_in):
    assert logged_in.post("/api/advisors", json={"name": "   "}).status_code == 400
    aid = _advisor(logged_in, "Grace Hopper")
    assert logged_in.put(f"/api/advisors/{aid}", json={"name": ""}).status_code == 400
    assert logged_in.put("/api/advisors/nope", json={"name": "X"}).status_code == 404


def test_a_resident_can_be_assigned_an_advisor_and_cleared(logged_in):
    aid = _advisor(logged_in, "Grace Hopper")
    rid = _resident(logged_in)
    assert _advisor_of(logged_in, rid) is None

    assert logged_in.put(f"/api/residents/{rid}", json={"advisor_id": aid}).status_code == 200
    assert _advisor_of(logged_in, rid) == aid

    assert logged_in.put(f"/api/residents/{rid}", json={"advisor_id": None}).status_code == 200
    assert _advisor_of(logged_in, rid) is None


def test_an_unknown_or_inactive_advisor_is_refused_not_a_500(logged_in):
    rid = _resident(logged_in)
    assert logged_in.put(f"/api/residents/{rid}", json={"advisor_id": "nope"}).status_code == 400

    aid = _advisor(logged_in, "Grace Hopper")
    logged_in.put(f"/api/advisors/{aid}", json={"active": False})
    assert logged_in.put(f"/api/residents/{rid}", json={"advisor_id": aid}).status_code == 400
    assert _advisor_of(logged_in, rid) is None


def test_editing_a_resident_leaves_the_advisor_alone(logged_in):
    """The Edit Resident form does not send advisor_id; saving it must not clear one."""
    aid = _advisor(logged_in, "Grace Hopper")
    rid = _resident(logged_in)
    logged_in.put(f"/api/residents/{rid}", json={"advisor_id": aid})

    logged_in.put(f"/api/residents/{rid}", json={"interests": "cardiology"})
    assert _advisor_of(logged_in, rid) == aid


def test_deactivating_keeps_the_assignment_and_deleting_clears_it(logged_in):
    aid = _advisor(logged_in, "Grace Hopper")
    rid = _resident(logged_in)
    logged_in.put(f"/api/residents/{rid}", json={"advisor_id": aid})

    logged_in.put(f"/api/advisors/{aid}", json={"active": False})
    assert _advisor_of(logged_in, rid) == aid

    assert logged_in.delete(f"/api/advisors/{aid}").status_code == 200
    assert logged_in.get("/api/advisors").json() == []
    resident = logged_in.get(f"/api/residents/{rid}")
    assert resident.status_code == 200
    assert resident.json()["advisor_id"] is None


def test_advisors_and_assignments_survive_a_restart(logged_in, app_module):
    """A second startup against the same database re-runs every migration,
    the advisor_id ALTER included, and must keep what was saved."""
    aid = _advisor(logged_in, "Grace Hopper")
    inactive = _advisor(logged_in, "Alan Turing")
    logged_in.put(f"/api/advisors/{inactive}", json={"active": False})
    rid = _resident(logged_in)
    logged_in.put(f"/api/residents/{rid}", json={"advisor_id": aid})

    with TestClient(app_module.app) as restarted:
        restarted.post("/api/auth/login", json={"password": TEST_PASSWORD})
        advisors = {a["id"]: a for a in restarted.get("/api/advisors").json()}
        assert advisors[aid]["name"] == "Grace Hopper"
        assert advisors[inactive]["active"] == 0
        assert _advisor_of(restarted, rid) == aid


def test_the_advisor_never_reaches_summary_generation(logged_in, app_module, monkeypatch):
    """Both summary endpoints get only the resident label and the notes and
    MedHub comments. Neither gets the advisor's name or id."""
    import rag_retrieval
    import summary_builder

    aid = _advisor(logged_in, "Hypatia Advisordottir")
    rid = _resident(logged_in)
    logged_in.put(f"/api/residents/{rid}", json={"advisor_id": aid})
    logged_in.post(f"/api/residents/{rid}/notes", json={"content": "Strong on rounds."})

    seen = []

    async def recording_report(resident_id, resident_label, comments, model=None, *, engine):
        seen.append((resident_label, comments))
        yield "done", {"summary_id": "s1", "report": {}}

    def recording_compose(resident_label, comments, engine):
        seen.append((resident_label, comments))
        return "prompt", {}

    monkeypatch.setattr(summary_builder, "generate_report", recording_report)
    monkeypatch.setattr(rag_retrieval, "compose_prompt", recording_compose)
    # Nothing listens here, so the legacy endpoint fails fast instead of
    # reaching a real Ollama on this machine.
    monkeypatch.setattr(app_module, "OLLAMA_URL", "http://127.0.0.1:9")

    assert logged_in.post(f"/api/residents/{rid}/summary-stream").status_code == 200
    assert logged_in.post(f"/api/residents/{rid}/generate-summary").status_code == 200

    assert len(seen) == 2
    for label, comments in seen:
        assert "Ada Lovelace" in label
        assert "Strong on rounds." in str(comments)
        assert "Advisordottir" not in f"{label} {comments}"
        assert aid not in f"{label} {comments}"
