"""A sub-competency with a lot of evidence must never be summarized from a cut prompt.

Ollama does not refuse a prompt longer than num_ctx. It keeps the end, drops the
beginning — the instructions, the sub-competency and the ACGME reference — and answers
anyway, with quotes that still match the comments that survived. Measured on a DGX
Spark with Ollama 0.34: 250-odd comments routed to one sub-competency made an
8,204-token prompt, Ollama logged "truncating input prompt limit=4098", and the section
came back "ok".

These tests run the real call, parse and validation path against a fake Ollama that
cuts prompts the way 0.34 does, so what they check is what a program would see.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
import pytest
import rag_retrieval
import summary_builder

NUM_CTX = summary_builder.OLLAMA_OPTIONS["num_ctx"]

# Llama 3's tokenizer counts AUC's prompts at about 4.75 characters per token, measured
# on 2026-10-10. The fake Ollama uses that for its own token count, independently of
# estimate_tokens, so the tests are not grading the estimate against itself.
CHARS_PER_TOKEN = 4.75

DOCUMENTS = Path(__file__).resolve().parents[2] / "rag" / "documents"


def real_tokens(prompt):
    return int(len(prompt) / CHARS_PER_TOKEN)


def ollama_cut(tokens, window):
    """How many tokens Ollama 0.34 keeps of a prompt too long for its window.

    llm/llama_server.go: limit = num_ctx - (num_ctx - num_keep) / 2, num_keep = 4.
    """
    return window - (window - 4) // 2


class FakeOllama:
    """Answers /api/generate like Ollama 0.34, including cutting long prompts.

    The reply quotes the first comment (or verified quote) the model can still see,
    which after a cut is one near the end — so a cut prompt yields a section whose
    quotes verify, exactly as measured.
    """

    def __init__(self, window=NUM_CTX, report_count=True, fail_part=None, invent_part=None):
        self.window = window
        self.report_count = report_count
        self.fail_part = fail_part
        self.invent_part = invent_part
        self.prompts = []
        self.evaluated = []
        self.parts = {}  # first comment number -> part number

    def handler(self, request):
        prompt = json.loads(request.content)["prompt"]
        self.prompts.append(prompt)

        tokens = real_tokens(prompt)
        seen = prompt
        if tokens > self.window - 1:
            tokens = ollama_cut(tokens, self.window)
            seen = prompt[-int(tokens * CHARS_PER_TOKEN):]
        self.evaluated.append(tokens)

        part = re.search(r"comments (\d+) to \d+ of \d+", prompt)
        part_number = self.parts.setdefault(part.group(1), len(self.parts) + 1) if part else None
        if self.fail_part is not None and part_number == self.fail_part:
            return httpx.Response(500, json={"error": "model crashed"})

        # Look only below the evidence heading when it survived, so a bullet in the
        # ACGME reference is never mistaken for evidence.
        for marker in ("This is what each part found", "COMMENTS about this resident"):
            if marker in seen:
                seen = seen[seen.index(marker):]
                break

        if self.invent_part is not None and part_number == self.invent_part:
            reply = {"level": 5, "narrative": "INVENTED NARRATIVE about a resident.",
                     "quotes": ["a sentence no evaluator ever wrote"]}
        elif seen.startswith("This is what each part found"):
            quote = re.search(r"^- (.+)$", seen, re.MULTILINE).group(1)
            reply = {"level": 3, "narrative": "Merged assessment.", "quotes": [quote]}
        else:
            # Comments are "N. text"; the rules below them are "- ...".
            match = re.search(r"^\d+\. (.+)$", seen, re.MULTILINE)
            reply = {"level": 3, "narrative": "Part assessment.", "quotes": [match.group(1)]}

        body = {"response": json.dumps(reply), "done": True, "done_reason": "stop"}
        if self.report_count:
            body["prompt_eval_count"] = tokens
        return httpx.Response(200, json=body)


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def log():
    logger = logging.getLogger("auc.test_prompt_budget")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = ListHandler()
    logger.addHandler(handler)
    yield logger, handler.lines
    logger.removeHandler(handler)


@pytest.fixture
def pc1():
    ontology = rag_retrieval.load_ontology()
    return next(e for e in summary_builder.build_skeleton(ontology) if e["id"] == "PC1")


@pytest.fixture
def descriptor(pc1):
    """PC1's real milestone text, so prompts are the size real ones are."""
    lines = (DOCUMENTS / rag_retrieval.MILESTONES_SOURCE).read_text().splitlines()
    start = lines.index(f"### {pc1['name']}")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("### "))
    return "\n".join(lines[start:end])


def comments(count):
    """Distinct, evaluation-like comments, ~31 tokens each like the measured ones."""
    return [
        {"text": f"Comment {i}: excellent history taking on a difficult admission with "
                 f"septic shock; the attending noted solid reasoning and good follow-through.",
         "label": f"note {i}"}
        for i in range(1, count + 1)
    ]


def generate(fake, entry, descriptor, evidence, logger):
    async def run():
        transport = httpx.MockTransport(fake.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await summary_builder._generate_one(
                client, "fake-model", "Jane Doe, PGY-2", entry, descriptor, evidence,
                logger, "[test]",
            )
    return asyncio.run(run())


# --- Detection: a cut prompt is never shown ------------------------------------

def test_a_prompt_ollama_cut_is_discarded_not_shown(pc1, descriptor, log, monkeypatch):
    # Turn splitting off to send the over-long prompt the old code sent.
    monkeypatch.setattr(summary_builder, "PROMPT_TOKEN_BUDGET", 10**9)
    logger, lines = log
    fake = FakeOllama()

    section = generate(fake, pc1, descriptor, comments(280), logger)

    assert real_tokens(fake.prompts[0]) > NUM_CTX, "the prompt must be long enough to be cut"
    assert fake.evaluated == [ollama_cut(real_tokens(fake.prompts[0]), NUM_CTX)]  # 4098
    assert section["status"] == summary_builder.STATUS_FAILED
    assert section["quotes"] == [] and section["level"] is None
    assert "Part assessment" not in json.dumps(section)
    assert any("PC1 TRUNCATED" in line and "4098" in line for line in lines)
    assert len(fake.prompts) == 1, "a cut prompt would be cut again, so it is not retried"


def test_a_big_cut_at_a_model_smaller_window_is_caught(pc1, descriptor, log):
    # Ollama lowers num_ctx to a model's own maximum when that is smaller. This prompt
    # fits AUC's budget but not a 2,048-token model, which keeps barely a third of it.
    logger, lines = log
    fake = FakeOllama(window=2048)

    section = generate(fake, pc1, descriptor, comments(75), logger)

    assert len(fake.prompts) == 1, "it fits the budget, so it is one call"

    assert fake.evaluated[0] == ollama_cut(real_tokens(fake.prompts[0]), 2048)
    assert section["status"] == summary_builder.STATUS_FAILED
    assert any("TRUNCATED" in line for line in lines)


def test_an_ollama_without_the_token_count_still_works(pc1, descriptor, log):
    logger, _lines = log
    section = generate(FakeOllama(report_count=False), pc1, descriptor, comments(5), logger)
    assert section["status"] == summary_builder.STATUS_OK


@pytest.mark.parametrize("text, measured", [
    # Real token counts from Llama 3's and OpenAI's cl100k tokenizers (they agree).
    ("She consistently demonstrates thoughtful clinical reasoning and communicates well "
     "with nursing staff; residents found her approachable and she taught at the bedside "
     "without being asked.", 28),
    ("pt w/ hx CHF (EF 25%), BNP 1200, Cr 1.8->2.3; K 5.9 -> gave Ca gluc 1g IV, insulin "
     "10u + D50; HR 132, BP 84/52, lactate 4.1 -> 30cc/kg IVF.", 73),
    ("pseudomembranous colitis with thrombocytopenia, hepatosplenomegaly and "
     "lymphadenopathy; bronchoalveolar lavage and echocardiography arranged.", 41),
    ("Demonstrates professionalism, responsibility and accountability; communication with "
     "interdisciplinary colleagues was consistently respectful, organized and thoughtful. "
     "Documentation exceptionally comprehensive.", 25),
])
def test_the_estimate_never_comes_in_under_the_real_count(text, measured):
    estimated = summary_builder.estimate_tokens(text)
    assert estimated >= measured
    # And an honest reply for it is never mistaken for a cut one.
    summary_builder.check_not_truncated(text, measured)


# --- Prevention: a big sub-competency is read in parts --------------------------

def test_a_big_sub_competency_is_read_in_parts_and_merged(pc1, descriptor, log):
    logger, lines = log
    fake = FakeOllama()
    evidence = comments(280)

    section = generate(fake, pc1, descriptor, evidence, logger)

    part_prompts = [p for p in fake.prompts if "comments " in p and " of 280" in p]
    merges = [p for p in fake.prompts if "This is what each part found" in p]
    assert len(part_prompts) >= 3 and len(merges) == 1
    assert len(fake.prompts) == len(part_prompts) + 1

    # Every prompt fit, nothing was cut, and nothing came near the cut.
    for prompt in fake.prompts:
        assert summary_builder.estimate_tokens(prompt) <= summary_builder.PROMPT_TOKEN_BUDGET
        assert "You are drafting ONE section" in prompt and "SUB-COMPETENCY: PC1" in prompt
    assert max(fake.evaluated) < summary_builder.TRUNCATION_SIGNAL

    # Every comment was read exactly once.
    for comment in evidence:
        line = re.compile(rf"^\d+\. {re.escape(comment['text'])}$", re.MULTILINE)
        assert sum(bool(line.search(p)) for p in part_prompts) == 1

    assert section["status"] == summary_builder.STATUS_OK
    assert section["narrative"] == "Merged assessment."
    texts = [c["text"] for c in evidence]
    assert section["quotes"] and all(
        any(q["text"] in t for t in texts) for q in section["quotes"]
    )
    assert any(f"PC1 SPLIT into {len(part_prompts)} parts" in line for line in lines)


def test_a_part_that_fails_fails_the_whole_section(pc1, descriptor, log):
    logger, _lines = log
    fake = FakeOllama(fail_part=2)

    section = generate(fake, pc1, descriptor, comments(280), logger)

    assert section["status"] == summary_builder.STATUS_FAILED
    assert section["narrative"] != "Part assessment."
    assert not any("This is what each part found" in p for p in fake.prompts)


def test_an_unverified_part_never_reaches_the_merge(pc1, descriptor, log):
    logger, lines = log
    fake = FakeOllama(invent_part=2)

    section = generate(fake, pc1, descriptor, comments(280), logger)

    merges = [p for p in fake.prompts if "This is what each part found" in p]
    assert merges and all("INVENTED NARRATIVE" not in p for p in merges)
    assert "INVENTED NARRATIVE" not in json.dumps(section)
    assert section["status"] == summary_builder.STATUS_OK
    assert section["dropped_quote_count"] == 1
    assert any("part 2/" in line and "DISCARDED NARRATIVE" in line for line in lines)


def test_an_ordinary_section_is_still_one_unchanged_call(pc1, descriptor, log):
    logger, lines = log
    fake = FakeOllama()
    evidence = comments(5)

    section = generate(fake, pc1, descriptor, evidence, logger)

    assert fake.prompts == [
        summary_builder.build_prompt("Jane Doe, PGY-2", pc1, descriptor, evidence)
    ]
    assert section["status"] == summary_builder.STATUS_OK
    assert not any("SPLIT" in line for line in lines)
