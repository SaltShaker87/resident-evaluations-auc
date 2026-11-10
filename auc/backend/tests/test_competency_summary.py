"""Six-competency strengths and growth areas, for advisor packets.

Built the way the sub-competency sections are: the six come from the ontology, one
call per competency with only its routed comments, every quote checked verbatim, and
a statement whose quotes do not survive is never shown.
"""

import asyncio
import logging

import rag_retrieval
import summary_builder
from evidence_helpers import StubModel


def _run(comments, competencies=True, **kwargs):
    async def collect():
        events = []
        async for event, payload in summary_builder.generate_report(
            "r1", "Ada Lovelace, PGY-2", comments, model="stub", engine="ollama",
            metadata={"period": {"start": "2026-06-23"}}, competencies=competencies, **kwargs,
        ):
            events.append((event, payload))
        return events
    return asyncio.run(collect())


def _comment(text):
    return {"text": text, "label": f"MedHub comment, evaluator=Alvarez, Rosa, {text[:10]}"}


def _logger():
    logger = logging.getLogger("auc.test_competency")
    logger.addHandler(logging.NullHandler())
    return logger


def _entry():
    return {"id": "PC", "name": "Patient Care", "domain": "PC", "domain_name": "Patient Care"}


def test_the_six_come_from_the_ontology_in_order(monkeypatch):
    StubModel(monkeypatch)
    events = _run([_comment("Thorough history and physical on every admission.")])
    report = events[-1][1]["report"]
    domains = rag_retrieval.load_ontology()["domains"]
    assert [c["id"] for c in report["competencies"]] == rag_retrieval.DOMAIN_ORDER
    assert [c["name"] for c in report["competencies"]] == [
        domains[d] for d in rag_retrieval.DOMAIN_ORDER]
    assert report["schema"] == "auc.summary.v2"
    assert events[0][1]["total"] == 21 + 6
    assert [e for e, _ in events].count("competency") == 6


def test_a_competency_reads_only_its_own_comments(monkeypatch):
    model = StubModel(monkeypatch)
    # Each matches one keyword, so the real keyword router sends it to PC1 or PROF3.
    _run([_comment("Excellent history taking on every admission."),
          _comment("Real follow-through on discharge summaries.")])
    competency_prompts = [p for p in model.prompts if "STRENGTHS" in p]
    patient_care = next(p for p in competency_prompts if "COMPETENCY: Patient Care" in p)
    professionalism = next(p for p in competency_prompts if "COMPETENCY: Professionalism" in p)
    assert "history taking" in patient_care and "follow-through" not in patient_care
    assert "follow-through" in professionalism and "history taking" not in professionalism


def test_a_statement_without_a_verbatim_quote_is_dropped(monkeypatch):
    model = StubModel(monkeypatch)
    model.competency_reply = {
        "strengths": {"text": "Excellent histories.", "quotes": ["Thorough history"]},
        "growth_areas": {"text": "Invented weakness.", "quotes": ["words nobody wrote"]},
    }
    events = _run([_comment("Thorough history and physical on every admission.")])
    pc = next(c for c in events[-1][1]["report"]["competencies"] if c["id"] == "PC")
    assert pc["status"] == "ok"
    assert pc["strengths"]["quotes"][0]["text"] == "Thorough history"
    assert pc["strengths"]["quotes"][0]["source"].startswith("MedHub comment, evaluator=")
    assert pc["growth_areas"] is None
    assert pc["dropped_quote_count"] == 1


def test_nothing_surviving_shows_not_enough_evidence(monkeypatch):
    model = StubModel(monkeypatch)
    model.competency_reply = {
        "strengths": {"text": "Made up.", "quotes": ["not in any comment"]},
        "growth_areas": {"text": "Also made up.", "quotes": []},
    }
    events = _run([_comment("Thorough history and physical on every admission.")])
    competencies = {c["id"]: c for c in events[-1][1]["report"]["competencies"]}
    assert competencies["PC"]["status"] == "insufficient_evidence"
    assert competencies["PC"]["note"] == "Not enough evidence in this review period"
    # A competency nobody wrote about gets the same words, without a model call.
    assert competencies["MK"]["status"] == "no_evidence"
    assert competencies["MK"]["note"] == "Not enough evidence in this review period"


def test_validate_competency_keeps_exact_wording_and_caps_quotes():
    comments = [_comment("Calm Under Pressure, kind to families, and organized.")]
    parsed = {
        "strengths": {"text": "Calm.", "quotes": ["calm under pressure", "kind to families",
                                                  "and organized"]},
        "growth_areas": "No quotes given as a bare string.",
    }
    result = summary_builder.validate_competency(_entry(), parsed, comments, _logger(), "[t]")
    assert [q["text"] for q in result["strengths"]["quotes"]] == [
        "Calm Under Pressure", "kind to families"]
    assert result["growth_areas"] is None


def test_an_empty_statement_is_not_counted_as_a_drop():
    comments = [_comment("Calm under pressure.")]
    parsed = {"strengths": {"text": "Calm.", "quotes": ["Calm under pressure"]},
              "growth_areas": {"text": "", "quotes": []}}
    result = summary_builder.validate_competency(_entry(), parsed, comments, _logger(), "[t]")
    assert result["dropped_quote_count"] == 0
    assert result["status"] == "ok"


def test_a_competency_with_too_many_comments_is_read_in_parts(monkeypatch):
    model = StubModel(monkeypatch)
    many = [_comment(f"Thorough history number {i} with careful examination of every system "
                     f"and a differential that considered the less likely causes as well.")
            for i in range(260)]
    events = _run(many)
    pc = next(c for c in events[-1][1]["report"]["competencies"] if c["id"] == "PC")
    assert pc["status"] == "ok"
    competency_prompts = [p for p in model.prompts if "COMPETENCY: Patient Care" in p]
    assert any("of 260" in p for p in competency_prompts)
    assert any("This is what each part found" in p for p in competency_prompts)
    assert all(summary_builder.estimate_tokens(p) <= summary_builder.PROMPT_TOKEN_BUDGET
               for p in model.prompts)


def test_no_comments_needs_no_index_and_no_model(monkeypatch):
    def no_index(engine):
        raise AssertionError("the index must not be opened for an empty period")

    monkeypatch.setattr(rag_retrieval, "open_collection", no_index)
    model = StubModel.__new__(StubModel)
    model.prompts = []
    monkeypatch.setattr(summary_builder, "call_ollama", model._call)
    report = _run([])[-1][1]["report"]
    assert model.prompts == []
    assert all(s["status"] == "no_evidence" for s in report["sections"])
    assert all(s["narrative"] == "No evidence in this review period" for s in report["sections"])
    assert all(c["note"] == "Not enough evidence in this review period"
               for c in report["competencies"])
