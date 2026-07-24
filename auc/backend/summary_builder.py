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
     "no evidence this cycle" — no model call at all.
  5. Every quote the model returns is checked verbatim (case-insensitive) against the
     comments actually routed to that sub-competency. Quotes that don't check out are
     dropped, and if nothing survives, the narrative and level are discarded rather
     than shown. Unsupported narrative text never reaches the client.

Every dropped quote and discarded narrative is written to
auc/data/logs/summary_validation.log for inspection.
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import anyio
import httpx

import rag_retrieval
from config import OLLAMA_URL, OLLAMA_MODEL

# ---------------------------------------------------------------------------
# Configuration — change the model here, in one place.
# ---------------------------------------------------------------------------

# The model used for every sub-competency call. Env var wins so the systemd unit can
# override it without editing code; otherwise it falls back to the app-wide default.
SUMMARY_MODEL: str = os.environ.get("AUC_SUMMARY_MODEL", OLLAMA_MODEL)

OLLAMA_TIMEOUT_SECONDS: float = 600.0
OLLAMA_OPTIONS: dict = {"num_ctx": 8192, "temperature": 0.2}
OLLAMA_THINK: bool = False

# Ask Ollama to constrain decoding to valid JSON. This is belt-and-braces on top of the
# defensive parser below — set to False if a model misbehaves with it.
USE_OLLAMA_JSON_FORMAT: bool = True

# Most quotes we will show for one sub-competency, after validation.
MAX_QUOTES: int = 3

# Marks a stored summary as the structured format (vs. the older markdown blobs).
REPORT_SCHEMA: str = "auc.summary.v1"

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "data" / "logs"
VALIDATION_LOG = LOG_DIR / "summary_validation.log"

# Status values a section can carry.
STATUS_OK = "ok"
STATUS_NO_EVIDENCE = "no_evidence"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_FAILED = "generation_failed"

NO_EVIDENCE_TEXT = "No evidence this cycle"


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

def build_prompt(resident_label, entry, descriptor, comments):
    """Build the prompt for exactly one sub-competency.

    Contains only this sub-competency's descriptor and only the comments routed to it.
    """
    numbered = "\n".join(
        f"{i}. {(c.get('text') or '').strip()}"
        for i, c in enumerate(comments, start=1)
    )

    reference = (
        "REFERENCE — the official ACGME descriptor for this sub-competency. This is\n"
        "background to help you judge where the resident sits. It is NOT about this\n"
        "resident and must not appear in your answer:\n"
        f"{descriptor}\n"
        if descriptor else ""
    )

    return (
        f"You are drafting ONE section of a Clinical Competency Committee report for "
        f"{resident_label}.\n\n"
        f"SUB-COMPETENCY: {entry['id']} — {entry['name']}\n"
        f"COMPETENCY DOMAIN: {entry['domain_name']}\n\n"
        f"{reference}\n"
        f"COMMENTS about this resident that were routed to this sub-competency:\n"
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


_RETRY_SUFFIX = (
    "\n\nYour previous reply could not be parsed. Reply with the JSON object only — "
    "starting with { and ending with } — and nothing else."
)


# ---------------------------------------------------------------------------
# Ollama call + defensive parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")


async def call_ollama(client, model, prompt):
    """One non-streaming generate call. Returns the raw response text."""
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
    return response.json().get("response", "")


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
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found in response")
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}")

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


def validate_section(entry, parsed, comments, logger, log_prefix):
    """Turn a parsed model reply into a validated section dict.

    Drops unverifiable quotes. If nothing survives, discards the narrative and level
    entirely and returns an `insufficient_evidence` section — the discarded text goes
    to the log and never to the client.
    """
    sub_id = entry["id"]
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
                "%s %s DROPPED QUOTE (not a string): %r", log_prefix, sub_id, raw_quote
            )
            continue

        match = find_verbatim(raw_quote, comments)
        if match is None:
            dropped += 1
            logger.info(
                "%s %s DROPPED QUOTE (not found verbatim in routed comments): %s",
                log_prefix, sub_id, _flat(raw_quote),
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
            log_prefix, sub_id, parsed.get("level"), _flat(narrative, 800),
        )
        section = _empty_section(entry, STATUS_INSUFFICIENT)
        section["dropped_quote_count"] = dropped
        return section

    if not narrative:
        logger.info(
            "%s %s DISCARDED (quotes validated but narrative was empty)",
            log_prefix, sub_id,
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

def _prepare_sync(comments):
    """Load ontology, route comments, and fetch each sub-competency's descriptor.

    Blocking: touches ChromaDB and a synchronous Ollama embed call. Returns
    (skeleton, routed, descriptors). Raises rag_retrieval.RagUnavailable.
    """
    ontology = rag_retrieval.load_ontology()
    collection = rag_retrieval.open_collection()

    skeleton = build_skeleton(ontology)
    known_ids = {e["id"] for e in skeleton}

    routed = rag_retrieval.route_comments(comments, ontology, collection)
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


async def generate_report(resident_id, resident_label, comments, model=None):
    """Async generator yielding (event_name, payload) tuples.

    Emits `start` with the full skeleton, one `subcompetency` per section as it
    completes, and a final `done`. A fatal problem emits `error` and stops.
    """
    logger = get_logger()
    effective_model = model or SUMMARY_MODEL
    summary_id = str(uuid.uuid4())[:8]
    log_prefix = f"[resident={resident_id} summary={summary_id}]"

    try:
        skeleton, routed, descriptors = await anyio.to_thread.run_sync(
            _prepare_sync, comments
        )
    except rag_retrieval.RagUnavailable as e:
        logger.error("%s RETRIEVAL UNAVAILABLE: %s", log_prefix, e)
        yield "error", {"message": f"Could not route evidence: {e}"}
        return
    except Exception as e:
        logger.exception("%s RETRIEVAL FAILED", log_prefix)
        yield "error", {"message": f"Retrieval failed: {e}"}
        return

    total = len(skeleton)
    logger.info(
        "%s RUN START model=%s resident=%s comments=%d total_subcompetencies=%d "
        "routing=%s",
        log_prefix, effective_model, resident_label, len(comments), total,
        {k: len(v) for k, v in sorted(routed.items())},
    )

    yield "start", {
        "summary_id": summary_id,
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

    logger.info("%s RUN END counts=%s", log_prefix, counts)

    report = {
        "schema": REPORT_SCHEMA,
        "resident": resident_label,
        "model": effective_model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sections": sections,
    }

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
    """One sub-competency: call, parse (with one retry), validate."""
    sub_id = entry["id"]
    prompt = build_prompt(resident_label, entry, descriptor, evidence)

    for attempt in (1, 2):
        attempt_prompt = prompt if attempt == 1 else prompt + _RETRY_SUFFIX
        try:
            raw = await call_ollama(client, model, attempt_prompt)
        except httpx.ConnectError as e:
            logger.error("%s %s OLLAMA UNREACHABLE: %s", log_prefix, sub_id, e)
            return _empty_section(entry, STATUS_FAILED, "Could not reach Ollama")
        except Exception as e:
            logger.error(
                "%s %s REQUEST FAILED (attempt %d): %s", log_prefix, sub_id, attempt, e
            )
            if attempt == 2:
                return _empty_section(entry, STATUS_FAILED, "Model request failed")
            continue

        try:
            parsed = parse_model_json(raw)
        except ValueError as e:
            logger.info(
                "%s %s PARSE FAILURE (attempt %d): %s | raw=%s",
                log_prefix, sub_id, attempt, e, _flat(raw, 600),
            )
            if attempt == 1:
                logger.info("%s %s RETRY", log_prefix, sub_id)
                continue
            logger.info("%s %s GENERATION FAILED after retry", log_prefix, sub_id)
            return _empty_section(entry, STATUS_FAILED, "Could not parse model output")

        return validate_section(entry, parsed, evidence, logger, log_prefix)

    return _empty_section(entry, STATUS_FAILED, "Could not parse model output")
