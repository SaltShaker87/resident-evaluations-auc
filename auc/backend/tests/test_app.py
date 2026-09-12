"""End-to-end checks against the real application, over HTTP.

These are deliberately about the things that would actually break on a new
machine — the app starting at all, the database being writable, authentication
working — rather than about business logic.
"""

import io
import zipfile


def test_fresh_database_asks_for_a_password(client):
    body = client.get("/api/auth/status").json()
    assert body["setup_required"] is True
    assert body["authenticated"] is False


def test_setup_returns_a_recovery_key_and_logs_you_in(client):
    body = client.post("/api/auth/setup", json={"password": "correct-horse-battery-staple"}).json()
    assert body["recovery_key"], "setup must hand back a recovery key"

    status = client.get("/api/auth/status").json()
    assert status["setup_required"] is False
    assert status["authenticated"] is True


def test_resident_data_requires_authentication(client):
    # A fresh client has no session cookie.
    assert client.get("/api/residents").status_code in (401, 403)


def test_login_rejects_the_wrong_password(logged_in):
    logged_in.post("/api/auth/logout")
    response = logged_in.post("/api/auth/login", json={"password": "not the password"})
    assert response.status_code == 401


def test_a_resident_and_a_note_survive_a_round_trip(logged_in):
    """The write path, which is what proves the database is not read-only."""
    created = logged_in.post("/api/residents", json={
        "first_name": "Ada", "last_name": "Lovelace", "pgy_year": 2,
    })
    assert created.status_code == 200, created.text
    resident_id = created.json()["id"]

    note = logged_in.post(f"/api/residents/{resident_id}/notes", json={
        "content": "Led a difficult family meeting with real skill.",
        "acgme_domain": "ICS",
    })
    assert note.status_code == 200, note.text

    notes = logged_in.get(f"/api/residents/{resident_id}/notes").json()
    assert len(notes) == 1
    assert notes[0]["content"] == "Led a difficult family meeting with real skill."

    listing = logged_in.get("/api/residents").json()
    assert [r["id"] for r in listing] == [resident_id]
    assert listing[0]["total_notes"] == 1


def test_non_latin_names_round_trip(logged_in):
    """Names with accents must survive storage, not come back mangled."""
    created = logged_in.post("/api/residents", json={
        "first_name": "Renée", "last_name": "Muñoz-Ødegård", "pgy_year": 1,
    })
    resident = logged_in.get(f"/api/residents/{created.json()['id']}").json()
    assert resident["first_name"] == "Renée"
    assert resident["last_name"] == "Muñoz-Ødegård"


def test_the_backup_contains_the_database(logged_in):
    """A backup you have never opened is a hope, not a backup."""
    logged_in.post("/api/residents", json={
        "first_name": "Ada", "last_name": "Lovelace", "pgy_year": 2,
    })

    response = logged_in.get("/api/backup")
    assert response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "auc.db" in archive.namelist()
        assert archive.getinfo("auc.db").file_size > 0


def test_rag_status_endpoint_answers_without_an_index(logged_in):
    """It must report, not raise. A status check that can take the app down is
    worse than no status check."""
    body = logged_in.get("/api/rag/status").json()
    assert body["level"] in ("ok", "warning", "error")
    assert body["message"]
    assert body["configured_model"]


def test_the_path_traversal_hole_stays_closed(logged_in):
    """SECURITY.md records this one: /../data/auc.db once served the database."""
    response = logged_in.get("/../data/auc.db")
    assert b"SQLite format" not in response.content
