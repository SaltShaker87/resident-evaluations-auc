"""Per-sub-competency resident summary generation.

Replaces the single giant "write the whole report" prompt with one small model call
per ACGME sub-competency. The design rule is that **Python owns the report and the
model only fills in slots**:

  1. The report skeleton is built from auc/rag/ontology/acgme_ontology.json. The set
     of sub-competencies in the output is exactly the set in that file — the model
     never gets to decide which sub-competencies exist, so it cannot invent one.
  2. Comments are routed to sub-competencies by rag_retrieval.route_comments (that
     logic is used as-is and is not modified here).
  3. Each sub-competency with routed evidence gets ONE call containing only its own
     ACGME descriptor text and only its own comments. The descriptor is reference
     material for forming a judgment; the prompt forbids reusing its language.
  4. Sub-competencies with no routed evidence are filled in by Python as
     "no evidence in this review period" — no model call at all.
  5. Every quote the model returns is checked verbatim (case-insensitive) against the
     comments actually routed to that sub-competency. Quotes that don't check out are
     dropped, and if nothing survives, the narrative and level are discarded rather
     than shown. Unsupported narrative text never reaches the client.
  6. No prompt is sent that Ollama would cut short. A sub-competency with more comments
     than fit in one prompt is read in parts, and the verified findings of the parts
     are merged in one more call. Every reply is also checked for signs that Ollama
     cut its prompt anyway; one that shows them is discarded, never shown.

For an advisor packet the same rules produce one more thing: for each of the six
ACGME competencies, a "strengths" and a "growth areas" statement (see "Six
competencies" below). The six come from the ontology, one call each, only the
comments routed to that competency's sub-competencies, every quote verified.

Every dropped quote, discarded narrative, split and cut prompt is written to
auc/data/logs/summary_validation.log for inspection.
"""

import json
import logging
import math
import os
import re
import uuid
from datetime import datetime

import anyio
import httpx
import rag_retrieval
from config import DATA_DIR, OLLAMA_MODEL, OLLAMA_URL

# ---------------------------------------------------------------------------
# Configuration — change the model here, in one place.
# ---------------------------------------------------------------------------

# The model used for every sub-competency call. Env var wins so the systemd unit can
# override it without editing code; otherwise it falls back to the app-wide default.
SUMMARY_MODEL: str = os.environ.get("AUC_SUMMARY_MODEL", OLLAMA_MODEL)

OLLAMA_TIMEOUT_SECONDS: float = 600.0
OLLAMA_OPTIONS: dict = {"num_ctx": 8192, "temperature": 0.2}
OLLAMA_THINK: bool = False

# How big one prompt may get. Ollama does not refuse a prompt longer than num_ctx. It
# keeps the END, throws away the beginning, and answers anyway (measured with Ollama
# 0.34: an 8,204-token prompt cut to its last 4,098 tokens). The beginning is where the
# instructions, the sub-competency and the ACGME reference are, so the model writes
# without knowing what it is assessing, its quotes still match the comments that
# survived, and nothing on screen says anything went wrong.
#
# So no prompt may be more than half the window: the other half is room for the reply
# and for estimate_tokens being off. A sub-competency whose comments would not fit is
# read in parts instead (see _generate_split). Both numbers follow num_ctx, so changing
# it above moves them too.
PROMPT_TOKEN_BUDGET: int = OLLAMA_OPTIONS["num_ctx"] // 2 - 512

# A reply that says Ollama read at least this many prompt tokens had its prompt cut.
# Ollama's cut keeps num_ctx minus half of it, so a cut prompt always lands here or just
# above (the 4,098 above), and PROMPT_TOKEN_BUDGET keeps every prompt AUC sends below it.
TRUNCATION_SIGNAL: int = OLLAMA_OPTIONS["num_ctx"] // 2

# Ask Ollama to constrain decoding to valid JSON. This is belt-and-braces on top of the
# defensive parser below — set to False if a model misbehaves with it.
USE_OLLAMA_JSON_FORMAT: bool = True

# Most quotes we will show for one sub-competency, after validation.
MAX_QUOTES: int = 3

# Marks a stored summary as the structured format (vs. the older markdown blobs).
# v2 is v1 plus what the summary covers: its review period, evidence scope, counts,
# MedHub snapshot, PGY year at the cutoff, app version and retrieval engine, and for
# a packet the six-competency statements. A summary with no period ("All time") is
# still written as v1, exactly as before. Both are read everywhere.
REPORT_SCHEMA: str = "auc.summary.v1"
REPORT_SCHEMA_V2: str = "auc.summary.v2"
REPORT_SCHEMAS = (REPORT_SCHEMA, REPORT_SCHEMA_V2)

# Most quotes shown for one strength or growth area of a competency. Fewer than a
# sub-competency's, so the six fit near one page.
COMPETENCY_MAX_QUOTES: int = 2

LOG_DIR = DATA_DIR / "logs"
VALIDATION_LOG = LOG_DIR / "summary_validation.log"

# Status values a section can carry.
STATUS_OK = "ok"
STATUS_NO_EVIDENCE = "no_evidence"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_FAILED = "generation_failed"

NO_EVIDENCE_TEXT = "No evidence in this review period"
COMPETENCY_EMPTY_TEXT = "Not enough evidence in this review period"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_logger = None


def get_logger():
    """File logger for validation decisions. Created lazily, configured once."""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("auc.summary")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(VALIDATION_LOG, encoding="utf-8")
        except Exception:
            # Never let a logging problem take down summary generation.
            handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)

    _logger = logger
    return logger


def _flat(text, limit=400):
    """Collapse a string onto one log line."""
    one_line = re.sub(r"\s+", " ", (text or "")).strip()
    return one_line[:limit] + ("…" if len(one_line) > limit else "")


# ---------------------------------------------------------------------------
# Skeleton — built from the ontology, never from model output
# ---------------------------------------------------------------------------

def build_skeleton(ontology):
    """Return every sub-competency in the ontology, in canonical display order.

    Ordered by rag_retrieval.DOMAIN_ORDER, then by the order they appear in the
    ontology file. The length of this list is the length of the final report.
    """
    domains = ontology["domains"]
    subs = ontology["subcompetencies"]

    skeleton = []
    for domain_code in rag_retrieval.DOMAIN_ORDER:
        for sub in subs:
            if sub["domain"] != domain_code:
                continue
            skeleton.append({
                "id": sub["id"],
                "name": sub["name"],
                "domain": domain_code,
                "domain_name": domains[domain_code],
            })

    # Guard against a domain code in the ontology that DOMAIN_ORDER doesn't know about,
    # so a sub-competency can never be silently dropped from the report.
    seen = {s["id"] for s in skeleton}
    for sub in subs:
        if sub["id"] not in seen:
            skeleton.append({
                "id": sub["id"],
                "name": sub["name"],
                "domain": sub["domain"],
                "domain_name": domains.get(sub["domain"], sub["domain"]),
            })

    return skeleton


def _empty_section(entry, status, note=None):
    """A section with no model-generated content."""
    return {
        "id": entry["id"],
        "name": entry["name"],
        "domain": entry["domain"],
        "domain_name": entry["domain_name"],
        "status": status,
        "level": None,
        "narrative": note,
        "quotes": [],
        "dropped_quote_count": 0,
    }


# ---------------------------------------------------------------------------
# Prompt for a single sub-competency
# ---------------------------------------------------------------------------

def _reference_block(descriptor):
    return (
        "REFERENCE — the official ACGME descriptor for this sub-competency. This is\n"
        "background to help you judge where the resident sits. It is NOT about this\n"
        "resident and must not appear in your answer:\n"
        f"{descriptor}\n"
        if descriptor else ""
    )


def build_prompt(resident_label, entry, descriptor, comments, part=None):
    """Build the prompt for exactly one sub-competency.

    Contains only this sub-competency's descriptor and only the comments routed to it.

    part is (first, last, total) when the comments are one part of a sub-competency
    too big for one prompt: they are numbered first..last and the model is told the
    rest are read separately. Without it the prompt is exactly what it always was.
    """
    first = part[0] if part else 1
    numbered = "\n".join(
        f"{i}. {(c.get('text') or '').strip()}"
        for i, c in enumerate(comments, start=first)
    )

    if part:
        heading = (
            f"COMMENTS about this resident that were routed to this sub-competency — "
            f"comments {part[0]} to {part[1]} of {part[2]}. The others are being read "
            f"separately, so write only about these:\n"
        )
    else:
        heading = "COMMENTS about this resident that were routed to this sub-competency:\n"

    return (
        f"You are drafting ONE section of a Clinical Competency Committee report for "
        f"{resident_label}.\n\n"
        f"SUB-COMPETENCY: {entry['id']} — {entry['name']}\n"
        f"COMPETENCY DOMAIN: {entry['domain_name']}\n\n"
        f"{_reference_block(descriptor)}\n"
        f"{heading}"
        f"{numbered}\n\n"
        "Write 2 to 3 sentences about THIS resident, in your own words, based only on "
        "the comments above.\n\n"
        "Rules:\n"
        "- Do not quote, paraphrase, echo, or reuse any wording from the REFERENCE "
        "section. Write as if you had never seen it.\n"
        "- Do not state anything the comments do not support. Do not invent rotations, "
        "events, dates, patients, or behaviors.\n"
        "- Do not mention other sub-competencies.\n"
        "- Suggest a milestone level from 1 to 5 for committee discussion.\n"
        f"- In \"quotes\", copy 1 to {MAX_QUOTES} short snippets from the COMMENTS above, "
        "character for character, exactly as written. Do not fix typos, do not "
        "re-punctuate, do not shorten with ellipses. A snippet that is not an exact "
        "copy will be discarded.\n\n"
        "Respond with a single JSON object and nothing else. No markdown, no code "
        "fences, no explanation before or after:\n"
        '{"level": 3, "narrative": "...", "quotes": ["exact snippet", "exact snippet"]}'
    )


def build_combine_prompt(resident_label, entry, descriptor, findings):
    """Build the prompt that merges the parts of a sub-competency read in parts.

    findings are sections that already passed validate_section, so everything shown
    here is a narrative backed by at least one verified quote, and every quote is the
    evaluator's own wording. A part whose narrative was discarded is not among them:
    unsupported text never feeds the merged one.
    """
    blocks = []
    for i, finding in enumerate(findings, start=1):
        level = finding.get("level")
        # One line each; find_verbatim matches across the line breaks this removes.
        quotes = "\n".join(f"- {' '.join(q['text'].split())}" for q in finding["quotes"])
        blocks.append(
            f"PART {i} (suggested level: {level if level is not None else 'none'})\n"
            f"Summary: {finding['narrative']}\n"
            f"VERIFIED QUOTES:\n{quotes}"
        )
    parts = "\n\n".join(blocks)

    return (
        f"You are drafting ONE section of a Clinical Competency Committee report for "
        f"{resident_label}.\n\n"
        f"SUB-COMPETENCY: {entry['id']} — {entry['name']}\n"
        f"COMPETENCY DOMAIN: {entry['domain_name']}\n\n"
        f"{_reference_block(descriptor)}\n"
        f"There were too many comments about this resident for this sub-competency to "
        f"read at once, so they were read in {len(findings)} parts. This is what each "
        f"part found:\n\n"
        f"{parts}\n\n"
        "Write 2 to 3 sentences about THIS resident, in your own words, that combine "
        "these findings into one assessment. If the parts disagree, say so.\n\n"
        "Rules:\n"
        "- Do not quote, paraphrase, echo, or reuse any wording from the REFERENCE "
        "section. Write as if you had never seen it.\n"
        "- Do not state anything the parts above do not support. Do not invent "
        "rotations, events, dates, patients, or behaviors.\n"
        "- Do not mention other sub-competencies, and do not mention the parts.\n"
        "- Suggest one milestone level from 1 to 5 for committee discussion.\n"
        f"- In \"quotes\", copy 1 to {MAX_QUOTES} of the VERIFIED QUOTES above, "
        "character for character, exactly as written. A snippet that is not an exact "
        "copy will be discarded.\n\n"
        "Respond with a single JSON object and nothing else. No markdown, no code "
        "fences, no explanation before or after:\n"
        '{"level": 3, "narrative": "...", "quotes": ["exact snippet", "exact snippet"]}'
    )


_RETRY_SUFFIX = (
    "\n\nYour previous reply could not be parsed. Reply with the JSON object only — "
    "starting with { and ending with } — and nothing else."
)


# ---------------------------------------------------------------------------
# Prompt size — keep every prompt whole
# ---------------------------------------------------------------------------

_TOKEN_PIECE_RE = re.compile(r"[A-Za-z]+|\S")


def estimate_tokens(text):
    """A deliberately high guess at how many tokens `text` is.

    Ollama has no endpoint that counts tokens, so this guesses. It was checked against
    real tokenizers (Llama 3's and OpenAI's cl100k) on AUC's own prompts: about 1.3
    times the true count on evaluation prose, and about the true count on clinical
    shorthand ("K 5.9 -> Ca gluc 1g IV"), where every digit and symbol is a token of
    its own. Counting characters instead would be badly wrong one way or the other.
    Guessing high only means reading a sub-competency in parts a little early.
    """
    count = 0
    for piece in _TOKEN_PIECE_RE.findall(text or ""):
        if piece.isascii() and piece.isalpha():
            length = len(piece)
            # Short words are one token; longer ones are more often split.
            count += 1 if length <= 6 else 2 if length <= 10 else math.ceil(length / 3)
        else:
            # A digit, a punctuation mark, or a character outside plain English.
            count += 1
    return count


class PromptTruncated(Exception):
    """Ollama did not read the whole prompt, so its reply cannot be trusted."""

    def __init__(self, evaluated, estimated):
        super().__init__(
            f"Ollama read {evaluated} prompt tokens of a prompt estimated at {estimated}"
        )
        self.evaluated = evaluated
        self.estimated = estimated


def check_not_truncated(prompt, prompt_eval_count):
    """Raise PromptTruncated if Ollama's reply shows it cut the prompt.

    prompt_eval_count is how many prompt tokens Ollama says it read, counting any it
    reused from an earlier call. Two signs of a cut:

      - It reached TRUNCATION_SIGNAL, the point every cut lands on.
      - It is under a third of the estimate. That is a cut at a smaller window than
        num_ctx: Ollama quietly lowers num_ctx to a model's own maximum when that is
        smaller. A third, not a half, because text full of long common words
        ("professionalism", "communication") can make the estimate 2.6 times too high.

    A reply without the count (an older Ollama) is not checked.
    """
    if isinstance(prompt_eval_count, bool) or not isinstance(prompt_eval_count, int):
        return
    if prompt_eval_count <= 0:
        return
    estimated = estimate_tokens(prompt)
    if prompt_eval_count >= TRUNCATION_SIGNAL or prompt_eval_count < estimated // 3:
        raise PromptTruncated(prompt_eval_count, estimated)


def _split_evenly(items, fits, start=1):
    """Split items into the fewest consecutive, evenly sized runs that all fit.

    fits(run, first) says whether the run beginning at items[first] fits in one prompt.
    Tries start runs, then one more each time. Returns [(first, run), ...], or None if
    even runs of one item each do not all fit.
    """
    total = len(items)
    for count in range(max(1, start), total + 1):
        bounds = [i * total // count for i in range(count + 1)]
        runs = [(bounds[i], items[bounds[i]:bounds[i + 1]]) for i in range(count)]
        if all(fits(run, first) for first, run in runs):
            return runs
    return None


# ---------------------------------------------------------------------------
# Ollama call + defensive parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")


async def call_ollama(client, model, prompt):
    """One non-streaming generate call. Returns the raw response text.

    Raises PromptTruncated if the reply shows Ollama did not read the whole prompt.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": OLLAMA_THINK,
        "options": OLLAMA_OPTIONS,
    }
    if USE_OLLAMA_JSON_FORMAT:
        payload["format"] = "json"

    response = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
    response.raise_for_status()
    body = response.json()
    check_not_truncated(prompt, body.get("prompt_eval_count"))
    return body.get("response", "")


def parse_model_json(raw):
    """Parse the model's reply into a dict, or raise ValueError.

    Tolerates code fences and leading/trailing prose by trimming to the outermost
    braces.
    """
    if not raw or not raw.strip():
        raise ValueError("empty response")

    text = _FENCE_RE.sub("", raw.strip())

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as first_error:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found in response") from first_error
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}") from e

    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


# ---------------------------------------------------------------------------
# Validation — quotes must appear verbatim in the routed comments
# ---------------------------------------------------------------------------

def _collapse_with_map(text):
    """Whitespace-collapsed copy of text, plus index map back to the original.

    index_map[i] is the position in `text` of collapsed[i], so a match found in the
    collapsed string can be sliced out of the original.
    """
    out = []
    index_map = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space or not out:
                continue
            out.append(" ")
            index_map.append(i)
            prev_space = True
        else:
            out.append(ch)
            index_map.append(i)
            prev_space = False
    # Drop a trailing collapsed space so it can't cause an off-by-one on the map.
    while out and out[-1] == " ":
        out.pop()
        index_map.pop()
    return "".join(out), index_map


def find_verbatim(quote, comments):
    """Locate `quote` in one of `comments`; return (exact_text, source_label) or None.

    Matching is case-insensitive, and tolerant of the model having flattened internal
    whitespace. The returned text is sliced out of the ORIGINAL comment, so what we
    display is the evaluator's exact wording and casing rather than the model's copy
    of it.
    """
    needle = (quote or "").strip()
    if not needle:
        return None

    lowered_needle = needle.lower()

    # Pass 1: straight case-insensitive substring match.
    for comment in comments:
        haystack = comment.get("text") or ""
        # .lower() is length-preserving for effectively all clinical text; if some
        # exotic character changes the length, the offsets would be wrong, so fall
        # through to pass 2 rather than slice at a bad index.
        low = haystack.lower()
        if len(low) != len(haystack):
            continue
        idx = low.find(lowered_needle)
        if idx != -1:
            return haystack[idx:idx + len(needle)], comment.get("label", "")

    # Pass 2: compare with whitespace collapsed, then map the span back.
    collapsed_needle, _ = _collapse_with_map(needle)
    collapsed_needle = collapsed_needle.lower()
    if not collapsed_needle:
        return None

    for comment in comments:
        haystack = comment.get("text") or ""
        collapsed, index_map = _collapse_with_map(haystack)
        idx = collapsed.lower().find(collapsed_needle)
        if idx != -1:
            start = index_map[idx]
            end = index_map[idx + len(collapsed_needle) - 1] + 1
            return haystack[start:end], comment.get("label", "")

    return None


def _coerce_level(value):
    """Return an int 1-5, or None. A bad level alone does not discard a section."""
    try:
        # float() first so a model that answers 2.0 or "3.0" still counts.
        level = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return level if 1 <= level <= 5 else None


def validate_section(entry, parsed, comments, logger, log_prefix, tag=None):
    """Turn a parsed model reply into a validated section dict.

    Drops unverifiable quotes. If nothing survives, discards the narrative and level
    entirely and returns an `insufficient_evidence` section — the discarded text goes
    to the log and never to the client.

    tag names the call in the log, e.g. "PC1 part 2/3"; it defaults to the id.
    """
    sub_id = entry["id"]
    tag = tag or sub_id
    narrative = parsed.get("narrative")
    narrative = narrative.strip() if isinstance(narrative, str) else ""
    level = _coerce_level(parsed.get("level"))

    raw_quotes = parsed.get("quotes")
    if isinstance(raw_quotes, str):
        raw_quotes = [raw_quotes]
    elif not isinstance(raw_quotes, list):
        raw_quotes = []

    kept = []
    dropped = 0
    seen = set()

    for raw_quote in raw_quotes:
        if not isinstance(raw_quote, str):
            dropped += 1
            logger.info(
                "%s %s DROPPED QUOTE (not a string): %r", log_prefix, tag, raw_quote
            )
            continue

        match = find_verbatim(raw_quote, comments)
        if match is None:
            dropped += 1
            logger.info(
                "%s %s DROPPED QUOTE (not found verbatim in routed comments): %s",
                log_prefix, tag, _flat(raw_quote),
            )
            continue

        text, source = match
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        kept.append({"text": text, "source": source})

        if len(kept) >= MAX_QUOTES:
            break

    if not kept:
        logger.info(
            "%s %s DISCARDED NARRATIVE (zero quotes survived validation; "
            "level=%s): %s",
            log_prefix, tag, parsed.get("level"), _flat(narrative, 800),
        )
        section = _empty_section(entry, STATUS_INSUFFICIENT)
        section["dropped_quote_count"] = dropped
        return section

    if not narrative:
        logger.info(
            "%s %s DISCARDED (quotes validated but narrative was empty)",
            log_prefix, tag,
        )
        section = _empty_section(entry, STATUS_INSUFFICIENT)
        section["dropped_quote_count"] = dropped
        return section

    return {
        "id": sub_id,
        "name": entry["name"],
        "domain": entry["domain"],
        "domain_name": entry["domain_name"],
        "status": STATUS_OK,
        "level": level,
        "narrative": narrative,
        "quotes": kept,
        "dropped_quote_count": dropped,
    }


# ---------------------------------------------------------------------------
# Retrieval prep (blocking — run in a worker thread)
# ---------------------------------------------------------------------------

def _prepare_sync(comments, engine):
    """Load ontology, route comments, and fetch each sub-competency's descriptor.

    Blocking: touches ChromaDB and synchronous embed calls (Ollama or the
    Nemotron containers, depending on engine). Returns (skeleton, routed,
    descriptors). Raises rag_retrieval.RagUnavailable.
    """
    ontology = rag_retrieval.load_ontology()
    skeleton = build_skeleton(ontology)
    if not comments:
        # Nothing to route: every section is "no evidence", and the index is not needed.
        return skeleton, {}, {}

    collection = rag_retrieval.open_collection(engine)
    known_ids = {e["id"] for e in skeleton}

    routed = rag_retrieval.route_comments(comments, ontology, collection, engine)
    # Routing can only ever produce ids from the ontology, but drop anything unknown
    # so a stale index entry cannot introduce a section that isn't in the skeleton.
    routed = {k: v for k, v in routed.items() if k in known_ids}

    descriptors = {}
    for sub_id in routed:
        milestones, supplemental = rag_retrieval.fetch_chunks(collection, sub_id)
        parts = [p for p in (milestones, supplemental) if p]
        descriptors[sub_id] = "\n\n".join(parts)

    return skeleton, routed, descriptors


# ---------------------------------------------------------------------------
# Event generation
# ---------------------------------------------------------------------------

def sse(event, data):
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def generate_report(resident_id, resident_label, comments, model=None, *, engine,
                          metadata=None, competencies=False):
    """Async generator yielding (event_name, payload) tuples.

    Emits `start` with the full skeleton, one `subcompetency` per section as it
    completes, with competencies=True one `competency` per ACGME competency after
    them, and a final `done`. A fatal problem emits `error` and stops.

    engine is the retrieval engine in force. It is written into every log line,
    so the validation log records which engine routed each summary.

    metadata, when given, describes what the summary covers (its review period and
    so on). It is stored in the report, which then has schema v2. It never reaches
    the model.
    """
    logger = get_logger()
    effective_model = model or SUMMARY_MODEL
    summary_id = str(uuid.uuid4())[:8]
    schema = REPORT_SCHEMA_V2 if metadata is not None else REPORT_SCHEMA
    log_prefix = f"[resident={resident_id} summary={summary_id} engine={engine}]"

    try:
        skeleton, routed, descriptors = await anyio.to_thread.run_sync(
            _prepare_sync, comments, engine
        )
    except rag_retrieval.RagUnavailable as e:
        logger.error("%s RETRIEVAL UNAVAILABLE: %s", log_prefix, e)
        yield "error", {"message": f"Could not route evidence: {e}"}
        return
    except Exception as e:
        logger.exception("%s RETRIEVAL FAILED", log_prefix)
        yield "error", {"message": f"Retrieval failed: {e}"}
        return

    domains = _competency_entries(skeleton) if competencies else []
    total = len(skeleton) + len(domains)
    logger.info(
        "%s RUN START model=%s resident=%s comments=%d total_subcompetencies=%d "
        "routing=%s",
        log_prefix, effective_model, resident_label, len(comments), total,
        {k: len(v) for k, v in sorted(routed.items())},
    )

    yield "start", {
        "summary_id": summary_id,
        "schema": schema,
        "metadata": metadata,
        "resident": resident_label,
        "model": effective_model,
        "total": total,
        "sections": [
            {
                **entry,
                "status": "pending" if entry["id"] in routed else STATUS_NO_EVIDENCE,
            }
            for entry in skeleton
        ],
    }

    sections = []
    completed = 0
    counts = {
        STATUS_OK: 0, STATUS_NO_EVIDENCE: 0,
        STATUS_INSUFFICIENT: 0, STATUS_FAILED: 0,
    }

    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
        for entry in skeleton:
            sub_id = entry["id"]
            evidence = routed.get(sub_id)

            if not evidence:
                # No model call: Python fills this in directly.
                section = _empty_section(entry, STATUS_NO_EVIDENCE, NO_EVIDENCE_TEXT)
            else:
                section = await _generate_one(
                    client, effective_model, resident_label, entry,
                    descriptors.get(sub_id, ""), evidence, logger, log_prefix,
                )

            sections.append(section)
            counts[section["status"]] = counts.get(section["status"], 0) + 1
            completed += 1

            yield "subcompetency", {
                **section,
                "completed": completed,
                "total": total,
            }

        competency_blocks = []
        for entry in domains:
            evidence = _competency_comments(skeleton, routed, entry["domain"])
            if not evidence:
                block = _empty_competency(entry, STATUS_NO_EVIDENCE)
            else:
                block = await _generate_competency(
                    client, effective_model, resident_label, entry, evidence, logger,
                    log_prefix,
                )
            competency_blocks.append(block)
            completed += 1
            yield "competency", {**block, "completed": completed, "total": total}

    logger.info("%s RUN END counts=%s", log_prefix, counts)

    report = {
        "schema": schema,
        "resident": resident_label,
        "model": effective_model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sections": sections,
    }
    if competencies:
        report["competencies"] = competency_blocks
    if metadata is not None:
        report.update({k: v for k, v in metadata.items() if k not in report})

    yield "done", {
        "summary_id": summary_id,
        "completed": completed,
        "total": total,
        "counts": counts,
        "report": report,
    }


async def _generate_one(
    client, model, resident_label, entry, descriptor, evidence, logger, log_prefix
):
    """One sub-competency: one call if its comments fit in one prompt, else in parts."""
    prompt = build_prompt(resident_label, entry, descriptor, evidence)
    estimated = estimate_tokens(prompt)
    if estimated <= PROMPT_TOKEN_BUDGET:
        return await _call_and_validate(
            client, model, entry, prompt, evidence, logger, log_prefix, entry["id"]
        )
    return await _generate_split(
        client, model, resident_label, entry, descriptor, evidence, logger, log_prefix,
        estimated,
    )


async def _call_and_validate(client, model, entry, prompt, comments, logger, log_prefix, tag,
                             validate=None, empty=None):
    """One call: send, parse (with one retry), validate against `comments`.

    tag names the call in the log: the sub-competency id, or e.g. "PC1 part 2/3".
    validate and empty default to a sub-competency's; a competency passes its own.
    """
    validate = validate or validate_section
    empty = empty or _empty_section
    for attempt in (1, 2):
        attempt_prompt = prompt if attempt == 1 else prompt + _RETRY_SUFFIX
        try:
            raw = await call_ollama(client, model, attempt_prompt)
        except httpx.ConnectError as e:
            logger.error("%s %s OLLAMA UNREACHABLE: %s", log_prefix, tag, e)
            return empty(entry, STATUS_FAILED, "Could not reach Ollama")
        except PromptTruncated as e:
            # No retry: the same prompt would be cut the same way. The reply is not
            # parsed, so nothing the model wrote from a cut prompt can be shown.
            logger.error(
                "%s %s TRUNCATED (Ollama read %d prompt tokens; estimated %d; num_ctx=%d) "
                "— reply discarded",
                log_prefix, tag, e.evaluated, e.estimated, OLLAMA_OPTIONS["num_ctx"],
            )
            return empty(entry, STATUS_FAILED, "Ollama cut the prompt short")
        except Exception as e:
            logger.error(
                "%s %s REQUEST FAILED (attempt %d): %s", log_prefix, tag, attempt, e
            )
            if attempt == 2:
                return empty(entry, STATUS_FAILED, "Model request failed")
            continue

        try:
            parsed = parse_model_json(raw)
        except ValueError as e:
            logger.info(
                "%s %s PARSE FAILURE (attempt %d): %s | raw=%s",
                log_prefix, tag, attempt, e, _flat(raw, 600),
            )
            if attempt == 1:
                logger.info("%s %s RETRY", log_prefix, tag)
                continue
            logger.info("%s %s GENERATION FAILED after retry", log_prefix, tag)
            return empty(entry, STATUS_FAILED, "Could not parse model output")

        return validate(entry, parsed, comments, logger, log_prefix, tag)

    return empty(entry, STATUS_FAILED, "Could not parse model output")


async def _generate_split(
    client, model, resident_label, entry, descriptor, evidence, logger, log_prefix,
    estimated,
):
    """A sub-competency with more comments than fit in one prompt.

    The comments are split into the fewest even, consecutive parts that each fit, and
    every part goes through the same call and validation as an ordinary section. The
    parts that come back with verified quotes are then merged in one more call (in
    rounds, if even their findings do not fit in one prompt), and the merged reply is
    validated against all the sub-competency's comments.

    If any call fails, the whole section fails: a summary that silently left out part
    of the evidence is exactly what this exists to prevent. A part whose quotes could
    not be verified is left out of the merge, so its narrative never feeds the one
    shown. If no part has verified quotes, the section is insufficient_evidence.
    """
    sub_id = entry["id"]
    total = len(evidence)

    def part_fits(run, first):
        prompt = build_prompt(
            resident_label, entry, descriptor, run, (first + 1, first + len(run), total)
        )
        return estimate_tokens(prompt) <= PROMPT_TOKEN_BUDGET

    parts = _split_evenly(
        evidence, part_fits, start=max(2, math.ceil(estimated / PROMPT_TOKEN_BUDGET))
    )
    if parts is None:
        logger.error(
            "%s %s TOO LONG: a single comment does not fit in one prompt (budget %d "
            "tokens) — section not generated",
            log_prefix, sub_id, PROMPT_TOKEN_BUDGET,
        )
        return _empty_section(entry, STATUS_FAILED, "A comment is too long to summarize")

    logger.info(
        "%s %s SPLIT into %d parts (%d comments; estimated %d prompt tokens, budget %d)",
        log_prefix, sub_id, len(parts), total, estimated, PROMPT_TOKEN_BUDGET,
    )

    dropped = 0
    findings = []
    for index, (first, run) in enumerate(parts, start=1):
        prompt = build_prompt(
            resident_label, entry, descriptor, run, (first + 1, first + len(run), total)
        )
        section = await _call_and_validate(
            client, model, entry, prompt, run, logger, log_prefix,
            f"{sub_id} part {index}/{len(parts)}",
        )
        dropped += section["dropped_quote_count"]
        if section["status"] == STATUS_FAILED:
            logger.info(
                "%s %s GENERATION FAILED (part %d of %d failed)",
                log_prefix, sub_id, index, len(parts),
            )
            return {**section, "dropped_quote_count": dropped}
        if section["status"] == STATUS_OK:
            findings.append(section)

    def merge_fits(run, _first):
        prompt = build_combine_prompt(resident_label, entry, descriptor, run)
        return estimate_tokens(prompt) <= PROMPT_TOKEN_BUDGET

    merge_round = 0
    while len(findings) > 1:
        merge_round += 1
        groups = _split_evenly(findings, merge_fits)
        if groups is None or len(groups) == len(findings):
            logger.error(
                "%s %s TOO LONG: %d findings cannot be merged within the budget (%d "
                "tokens) — section not generated",
                log_prefix, sub_id, len(findings), PROMPT_TOKEN_BUDGET,
            )
            return _empty_section(entry, STATUS_FAILED, "Too much evidence to summarize")

        merged = []
        for index, (_first, group) in enumerate(groups, start=1):
            if len(group) == 1:
                merged.append(group[0])
                continue
            prompt = build_combine_prompt(resident_label, entry, descriptor, group)
            section = await _call_and_validate(
                client, model, entry, prompt, evidence, logger, log_prefix,
                f"{sub_id} merge {merge_round}.{index}",
            )
            dropped += section["dropped_quote_count"]
            if section["status"] == STATUS_FAILED:
                logger.info("%s %s GENERATION FAILED (merge failed)", log_prefix, sub_id)
                return {**section, "dropped_quote_count": dropped}
            if section["status"] == STATUS_OK:
                merged.append(section)
        findings = merged

    if not findings:
        section = _empty_section(entry, STATUS_INSUFFICIENT)
        section["dropped_quote_count"] = dropped
        return section

    return {**findings[0], "dropped_quote_count": dropped}


# ---------------------------------------------------------------------------
# Six competencies — strengths and growth areas, for advisor packets
# ---------------------------------------------------------------------------
#
# The same rules as a sub-competency section: Python owns the structure (the six
# competencies come from the ontology, never from the model), one call per
# competency containing only the comments routed to its sub-competencies, every
# quote checked verbatim, nothing shown that lost all its quotes, and a competency
# with too many comments read in parts. The difference is the shape of the reply:
# two statements, each with its own quotes.

COMPETENCY_PARTS = ("strengths", "growth_areas")
COMPETENCY_PART_NAMES = {"strengths": "STRENGTHS", "growth_areas": "GROWTH AREAS"}


def _competency_entries(skeleton):
    """The six competencies in display order, from the skeleton (so from the ontology)."""
    entries, seen = [], set()
    for sub in skeleton:
        if sub["domain"] not in seen:
            seen.add(sub["domain"])
            entries.append({
                "id": sub["domain"], "name": sub["domain_name"],
                "domain": sub["domain"], "domain_name": sub["domain_name"],
            })
    return entries


def _competency_comments(skeleton, routed, domain):
    """Comments routed to any sub-competency of `domain`, each once, in routing order."""
    out, seen = [], set()
    for sub in skeleton:
        if sub["domain"] != domain:
            continue
        for comment in routed.get(sub["id"], []):
            key = (comment.get("text"), comment.get("label"))
            if key not in seen:
                seen.add(key)
                out.append(comment)
    return out


def _empty_competency(entry, status, note=None):
    return {
        "id": entry["id"],
        "name": entry["name"],
        "domain": entry["domain"],
        "domain_name": entry["domain_name"],
        "status": status,
        "strengths": None,
        "growth_areas": None,
        "note": note if note is not None else (
            COMPETENCY_EMPTY_TEXT if status in (STATUS_NO_EVIDENCE, STATUS_INSUFFICIENT) else None
        ),
        "dropped_quote_count": 0,
    }


_COMPETENCY_REPLY_SHAPE = (
    '{"strengths": {"text": "...", "quotes": ["exact snippet"]}, '
    '"growth_areas": {"text": "...", "quotes": ["exact snippet"]}}'
)


def _competency_rules(source):
    return (
        "Rules:\n"
        f"- Do not state anything the {source} do not support. Do not invent rotations, "
        "events, dates, patients, or behaviors.\n"
        "- Do not describe trends or changes over time.\n"
        "- If nothing above supports a strength, or nothing supports a growth area, leave "
        "that text empty and its quotes empty. Do not turn praise into a growth area or "
        "criticism into a strength.\n"
        f"- For each, in \"quotes\", copy 1 to {COMPETENCY_MAX_QUOTES} short snippets "
        f"from the {source} above that support it, character for character, exactly as "
        "written. Do not fix typos, do not re-punctuate, do not shorten with ellipses. A "
        "snippet that is not an exact copy will be discarded, and a statement with no "
        "exact snippet is not shown.\n\n"
        "Respond with a single JSON object and nothing else. No markdown, no code "
        "fences, no explanation before or after:\n"
        f"{_COMPETENCY_REPLY_SHAPE}"
    )


def build_competency_prompt(resident_label, entry, comments, part=None):
    """The prompt for one competency's strengths and growth areas.

    Contains only the comments routed to this competency's sub-competencies. part is
    (first, last, total) when the comments are one part of too many for one prompt.
    """
    first = part[0] if part else 1
    numbered = "\n".join(
        f"{i}. {(c.get('text') or '').strip()}" for i, c in enumerate(comments, start=first)
    )
    if part:
        heading = (
            f"COMMENTS about this resident that were routed to this competency — comments "
            f"{part[0]} to {part[1]} of {part[2]}. The others are being read separately, so "
            f"write only about these:\n"
        )
    else:
        heading = "COMMENTS about this resident that were routed to this competency:\n"

    return (
        f"You are drafting ONE competency of an advisor's summary about {resident_label}, "
        f"written from their evaluations.\n\n"
        f"COMPETENCY: {entry['name']}\n\n"
        f"{heading}{numbered}\n\n"
        "Write two short statements about THIS resident in this competency, in your own "
        "words, based only on the comments above:\n"
        "- STRENGTHS: 1 to 3 sentences on what the resident does well.\n"
        "- GROWTH AREAS: 1 to 3 sentences on what the resident should work on.\n\n"
        + _competency_rules("comments")
    )


def build_competency_combine_prompt(resident_label, entry, findings):
    """Merge the verified findings of a competency read in parts."""
    blocks = []
    for i, finding in enumerate(findings, start=1):
        lines = [f"PART {i}"]
        for key in COMPETENCY_PARTS:
            block = finding.get(key)
            if not block:
                lines.append(f"{COMPETENCY_PART_NAMES[key].title()}: none found")
                continue
            quotes = "\n".join(f"- {' '.join(q['text'].split())}" for q in block["quotes"])
            lines.append(f"{COMPETENCY_PART_NAMES[key].title()}: {block['text']}")
            lines.append(f"VERIFIED QUOTES:\n{quotes}")
        blocks.append("\n".join(lines))

    return (
        f"You are drafting ONE competency of an advisor's summary about {resident_label}, "
        f"written from their evaluations.\n\n"
        f"COMPETENCY: {entry['name']}\n\n"
        f"There were too many comments to read at once, so they were read in "
        f"{len(findings)} parts. This is what each part found:\n\n"
        + "\n\n".join(blocks)
        + "\n\nWrite two short statements about THIS resident in this competency, in your "
        "own words, that combine these findings. If the parts disagree, say so.\n"
        "- STRENGTHS: 1 to 3 sentences.\n"
        "- GROWTH AREAS: 1 to 3 sentences.\n\n"
        "Do not mention the parts.\n"
        + _competency_rules("VERIFIED QUOTES")
    )


def validate_competency(entry, parsed, comments, logger, log_prefix, tag=None):
    """Keep each statement only if at least one of its quotes is verbatim in the
    competency's comments. A competency with neither surviving is insufficient."""
    tag = tag or entry["id"]
    result = _empty_competency(entry, STATUS_INSUFFICIENT)
    dropped = 0

    for key in COMPETENCY_PARTS:
        raw = parsed.get(key)
        if isinstance(raw, str):
            raw = {"text": raw, "quotes": []}
        if not isinstance(raw, dict):
            continue
        text = raw.get("text")
        text = text.strip() if isinstance(text, str) else ""
        raw_quotes = raw.get("quotes")
        if isinstance(raw_quotes, str):
            raw_quotes = [raw_quotes]
        elif not isinstance(raw_quotes, list):
            raw_quotes = []
        if not text and not raw_quotes:
            continue  # the model said there was nothing to say: not a drop

        kept, seen = [], set()
        for raw_quote in raw_quotes:
            match = find_verbatim(raw_quote, comments) if isinstance(raw_quote, str) else None
            if match is None:
                dropped += 1
                logger.info(
                    "%s %s %s DROPPED QUOTE (not found verbatim in routed comments): %s",
                    log_prefix, tag, key, _flat(str(raw_quote)),
                )
                continue
            quote_text, source = match
            if quote_text.lower() in seen:
                continue
            seen.add(quote_text.lower())
            kept.append({"text": quote_text, "source": source})
            if len(kept) >= COMPETENCY_MAX_QUOTES:
                break

        if not kept or not text:
            logger.info(
                "%s %s DISCARDED %s (%s): %s",
                log_prefix, tag, key,
                "zero quotes survived validation" if not kept else "text was empty",
                _flat(text, 800),
            )
            continue
        result[key] = {"text": text, "quotes": kept}

    result["dropped_quote_count"] = dropped
    if result["strengths"] or result["growth_areas"]:
        result["status"] = STATUS_OK
        result["note"] = None
    return result


async def _generate_competency(client, model, resident_label, entry, evidence, logger,
                               log_prefix):
    """One competency: one call if its comments fit, else in parts, then merged."""
    tag = f"COMPETENCY {entry['id']}"
    prompt = build_competency_prompt(resident_label, entry, evidence)
    estimated = estimate_tokens(prompt)
    if estimated <= PROMPT_TOKEN_BUDGET:
        return await _call_and_validate(
            client, model, entry, prompt, evidence, logger, log_prefix, tag,
            validate=validate_competency, empty=_empty_competency,
        )

    total = len(evidence)

    def part_fits(run, first):
        part = (first + 1, first + len(run), total)
        return estimate_tokens(build_competency_prompt(resident_label, entry, run, part)) \
            <= PROMPT_TOKEN_BUDGET

    parts = _split_evenly(
        evidence, part_fits, start=max(2, math.ceil(estimated / PROMPT_TOKEN_BUDGET))
    )
    if parts is None:
        logger.error("%s %s TOO LONG: a single comment does not fit in one prompt", log_prefix, tag)
        return _empty_competency(entry, STATUS_FAILED, "A comment is too long to summarize")

    logger.info(
        "%s %s SPLIT into %d parts (%d comments; estimated %d prompt tokens, budget %d)",
        log_prefix, tag, len(parts), total, estimated, PROMPT_TOKEN_BUDGET,
    )

    dropped = 0
    findings = []
    for index, (first, run) in enumerate(parts, start=1):
        prompt = build_competency_prompt(
            resident_label, entry, run, (first + 1, first + len(run), total)
        )
        block = await _call_and_validate(
            client, model, entry, prompt, run, logger, log_prefix,
            f"{tag} part {index}/{len(parts)}",
            validate=validate_competency, empty=_empty_competency,
        )
        dropped += block["dropped_quote_count"]
        if block["status"] == STATUS_FAILED:
            logger.info("%s %s GENERATION FAILED (part %d of %d failed)",
                        log_prefix, tag, index, len(parts))
            return {**block, "dropped_quote_count": dropped}
        if block["status"] == STATUS_OK:
            findings.append(block)

    def merge_fits(run, _first):
        return estimate_tokens(build_competency_combine_prompt(resident_label, entry, run)) \
            <= PROMPT_TOKEN_BUDGET

    merge_round = 0
    while len(findings) > 1:
        merge_round += 1
        groups = _split_evenly(findings, merge_fits)
        if groups is None or len(groups) == len(findings):
            logger.error("%s %s TOO LONG: %d findings cannot be merged within the budget",
                         log_prefix, tag, len(findings))
            return _empty_competency(entry, STATUS_FAILED, "Too much evidence to summarize")
        merged = []
        for index, (_first, group) in enumerate(groups, start=1):
            if len(group) == 1:
                merged.append(group[0])
                continue
            prompt = build_competency_combine_prompt(resident_label, entry, group)
            block = await _call_and_validate(
                client, model, entry, prompt, evidence, logger, log_prefix,
                f"{tag} merge {merge_round}.{index}",
                validate=validate_competency, empty=_empty_competency,
            )
            dropped += block["dropped_quote_count"]
            if block["status"] == STATUS_FAILED:
                logger.info("%s %s GENERATION FAILED (merge failed)", log_prefix, tag)
                return {**block, "dropped_quote_count": dropped}
            if block["status"] == STATUS_OK:
                merged.append(block)
        findings = merged

    if not findings:
        block = _empty_competency(entry, STATUS_INSUFFICIENT)
        block["dropped_quote_count"] = dropped
        return block
    return {**findings[0], "dropped_quote_count": dropped}
