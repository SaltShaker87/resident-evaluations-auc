"""Shared helpers for tests that need residents with dated evidence.

MedHub rows are written straight into the database rather than through an HTML
report: these tests are about which evidence a summary reads, not about reading
reports (test_medhub_import.py covers that), and a dozen hand-built reports would
bury the dates that matter here.
"""

import json
import re
import sqlite3
import uuid

import rag_retrieval
import summary_builder

COMMENT_FORM = "Inpatient Medicine - Attending Evaluation of Trainee"
COMMENT_QUESTION = "Please provide comments regarding the resident's strengths."
RATING_QUESTIONS = {
    "Patient Care": "Resident gathers a complete history.",
    "Medical Knowledge": "Resident shows appropriate medical knowledge.",
    "Professionalism": "Resident is punctual and responsive.",
}


def connect(app_module):
    conn = sqlite3.connect(str(app_module.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def add_resident(client, first="Ada", last="Lovelace", pgy=2, **fields):
    rid = client.post("/api/residents", json={
        "first_name": first, "last_name": last, "pgy_year": pgy,
    }).json()["id"]
    if fields:
        assert client.put(f"/api/residents/{rid}", json=fields).status_code == 200
    return rid


def map_questions(conn):
    conn.execute(
        "INSERT OR IGNORE INTO medhub_question_map (form_name, question, question_type, competency) "
        "VALUES (?, ?, 'comment', 'No single competency')",
        (COMMENT_FORM, COMMENT_QUESTION),
    )
    for competency, question in RATING_QUESTIONS.items():
        conn.execute(
            "INSERT OR IGNORE INTO medhub_question_map (form_name, question, question_type, competency) "
            "VALUES (?, ?, 'milestone', ?)",
            (COMMENT_FORM, question, competency),
        )
    conn.commit()


def add_import(conn, imported_at):
    """A MedHub import, logged at `imported_at` ('YYYY-MM-DD HH:MM:SS', UTC)."""
    import_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO medhub_import_log (id, imported_at, file_name, report_type) "
        "VALUES (?, ?, 'test.html', 'Evaluation Questions Summary')",
        (import_id, imported_at),
    )
    conn.commit()
    return import_id


def _answer(conn, resident_id, question, answer, kind, level, rotation_end, import_id,
            evaluator, rotation_start, service):
    conn.execute(
        """INSERT INTO medhub_answers
           (id, fingerprint, resident_id, medhub_name, evaluator, form_name, question,
            rotation_start, rotation_end, service, answer, answer_kind, level, import_id)
           VALUES (?, ?, ?, 'Name, Test', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, uuid.uuid4().hex, resident_id, evaluator, COMMENT_FORM, question,
         rotation_start or rotation_end, rotation_end, service, answer, kind, level, import_id),
    )
    conn.commit()


def add_comment(conn, resident_id, text, rotation_end, import_id, evaluator="Alvarez, Rosa",
                rotation_start=None, service="WARDS1"):
    _answer(conn, resident_id, COMMENT_QUESTION, text, "text", None, rotation_end, import_id,
            evaluator, rotation_start, service)


def add_rating(conn, resident_id, answer, rotation_end, import_id, competency="Patient Care",
               evaluator="Alvarez, Rosa"):
    from medhub_reports import milestone_level

    kind, level = milestone_level(answer)
    _answer(conn, resident_id, RATING_QUESTIONS[competency], answer, kind, level, rotation_end,
            import_id, evaluator, None, "WARDS1")


# ---------------------------------------------------------------------------
# A stand-in for retrieval and the model
# ---------------------------------------------------------------------------

class _Collection:
    def query(self, query_embeddings, n_results):
        return {"metadatas": [[{"id": "PC1"}]], "documents": [["descriptor"]]}


class StubModel:
    """Routes comments with the real keyword router (anything else goes to PC1) and
    answers every model call by quoting the first comment it was shown, so every
    section comes back "ok" with a quote that verifies.

    prompts holds every prompt sent; `seen(text)` says whether any prompt held it.
    competency_reply, if set, replaces the reply to six-competency prompts.
    """

    def __init__(self, monkeypatch):
        self.prompts = []
        self.competency_reply = None
        monkeypatch.setattr(rag_retrieval, "open_collection", lambda engine: _Collection())
        monkeypatch.setattr(
            rag_retrieval, "embed", lambda texts, engine, input_type: [[0.0] for _ in texts]
        )
        monkeypatch.setattr(rag_retrieval, "fetch_chunks", lambda collection, sub_id: ("", ""))
        monkeypatch.setattr(summary_builder, "call_ollama", self._call)

    async def _call(self, client, model, prompt):
        self.prompts.append(prompt)
        evidence = prompt
        for marker in ("This is what each part found", "COMMENTS about this resident"):
            if marker in prompt:
                evidence = prompt[prompt.index(marker):]
                break
        numbered = re.search(r"^\d+\. (.+)$", evidence, re.MULTILINE)
        bullet = re.search(r"^- (.+)$", evidence, re.MULTILINE)
        quote = (numbered or bullet).group(1)
        if "STRENGTHS" in prompt:
            reply = self.competency_reply or {
                "strengths": {"text": "Consistently strong.", "quotes": [quote]},
                "growth_areas": {"text": "Could grow further.", "quotes": [quote]},
            }
        else:
            reply = {"level": 3, "narrative": "Assessment.", "quotes": [quote]}
        return json.dumps(reply)

    def seen(self, text):
        return any(text in p for p in self.prompts)


def read_sse(response):
    """[(event, data), ...] from a summary-stream response body."""
    events = []
    for frame in response.text.split("\n\n"):
        event, data = None, None
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event:
            events.append((event, data))
    return events
