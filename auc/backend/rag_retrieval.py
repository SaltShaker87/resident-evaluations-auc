"""Local RAG retrieval for grounding resident summaries in the ACGME ontology + index.

This module augments (does not replace) the existing summary generator. Given the
resident's evidence (committee notes and MedHub comments), it:

  1. Loads the ACGME ontology (auc/rag/ontology/acgme_ontology.json) and opens the
     local ChromaDB collection "acgme_guidelines" at auc/rag/chroma_db/.
  2. Routes each comment to ACGME sub-competencies via the ontology keywords. A
     comment can match several sub-competencies. A comment that matches no keyword
     falls back to a semantic search of the index so nothing is silently dropped.
  3. For every sub-competency with evidence, fetches BOTH of its chunks from the
     index by metadata id (the milestones level descriptors AND the supplemental-
     guide examples).
  4. Assembles a model prompt: evidence grouped by competency domain, each
     competency's retrieved descriptors + examples, and instructions to draft a
     development summary with suggested milestone levels for the committee.

Everything stays local: the only network call is to the local Ollama server for
embedding the handful of comments that need the semantic fallback. If the ontology
or index is missing, a RagUnavailable error is raised so the caller can fall back
to the legacy (ungrounded) prompt with a clear message instead of crashing.
"""

import json
import sys
from pathlib import Path

import httpx

# config.py sits next to this file. Import it for real rather than keeping a
# fallback default for EMBED_MODEL here: two defaults is how an index ends up
# built with one model and searched with another.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import EMBED_MODEL, OLLAMA_URL  # noqa: E402

# --- Paths / constants -----------------------------------------------------
RAG_DIR = Path(__file__).resolve().parent.parent / "rag"
ONTOLOGY_FILE = RAG_DIR / "ontology" / "acgme_ontology.json"
CHROMA_DIR = RAG_DIR / "chroma_db"

COLLECTION_NAME = "acgme_guidelines"

# Key under which build_index.py stamps the embedding model into the collection
# metadata, so a build/search mismatch can be detected rather than silently
# returning arbitrary results.
EMBED_MODEL_KEY = "embed_model"
MILESTONES_SOURCE = "acgme_im_milestones.md"
SUPPLEMENT_SOURCE = "acgme_im_supplemental_guide.md"

# Stable display order of the six ACGME competency domains.
DOMAIN_ORDER = ["PC", "MK", "SBP", "PBLI", "PROF", "ICS"]


class RagUnavailable(Exception):
    """Raised when the ontology/index is missing or unusable.

    The caller should catch this and fall back to the legacy prompt.
    """


# --- Loading ---------------------------------------------------------------
def load_ontology():
    if not ONTOLOGY_FILE.exists():
        raise RagUnavailable(
            f"ACGME ontology not found at {ONTOLOGY_FILE}. "
            "Create it before grounding summaries."
        )
    try:
        return json.loads(ONTOLOGY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        raise RagUnavailable(f"Could not read ACGME ontology: {e}")


def open_collection():
    if not CHROMA_DIR.exists():
        raise RagUnavailable(
            f"ACGME index not found at {CHROMA_DIR}. "
            "Run 'python auc/rag/build_index.py' first."
        )
    try:
        import chromadb
    except ImportError:
        raise RagUnavailable("chromadb is not installed in this environment.")
    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        return client.get_collection(COLLECTION_NAME)
    except Exception as e:
        raise RagUnavailable(
            f"ACGME collection '{COLLECTION_NAME}' is not available: {e}. "
            "Run 'python auc/rag/build_index.py' to (re)build it."
        )


def _embed(text):
    """Embed a single string with the same model used to build the index."""
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
        r.raise_for_status()
        return r.json()["embeddings"][0]


# --- Health ----------------------------------------------------------------
def index_status():
    """Report whether the ACGME index is usable, without raising.

    Exists so the index can be checked *before* a summary is attempted. Since
    the study-branch merge a broken index is a visible error rather than a
    silent downgrade, but you still only discover it by trying to generate —
    which, in a committee meeting, is the worst possible moment.

    Returns a dict with:
      level   "ok" | "warning" | "error"
      message a sentence saying what to do about it
      plus collection/count/expected_count/configured_model/index_model
      for anything that wants the detail.
    """
    result = {
        "level": "error",
        "message": "",
        "collection": COLLECTION_NAME,
        "count": None,
        "expected_count": None,
        "configured_model": EMBED_MODEL,
        "index_model": None,
    }

    try:
        ontology = load_ontology()
        collection = open_collection()
    except RagUnavailable as e:
        result["message"] = str(e)
        return result
    except Exception as e:  # defensive: a status check must never take the app down
        result["message"] = f"ACGME index could not be checked: {e}"
        return result

    # Two chunks per sub-competency: the milestones descriptors and the
    # supplemental-guide examples. Derived from the ontology rather than
    # hard-coded, so editing the ontology does not make this lie.
    expected = len(ontology.get("subcompetencies", [])) * 2
    result["expected_count"] = expected

    try:
        count = collection.count()
        stamped = (collection.metadata or {}).get(EMBED_MODEL_KEY)
    except Exception as e:
        result["message"] = f"ACGME collection '{COLLECTION_NAME}' could not be read: {e}"
        return result

    result["count"] = count
    result["index_model"] = stamped

    if count == 0:
        result["message"] = (
            "The ACGME index is empty. Rebuild it with: "
            "python auc/rag/build_index.py"
        )
        return result

    # A mismatch is the one failure that produces no error of its own: retrieval
    # keeps working and returns arbitrary results. Treat it as fatal.
    if stamped and stamped != EMBED_MODEL:
        result["message"] = (
            f"This index was built with '{stamped}' but the app is configured to "
            f"search it with '{EMBED_MODEL}'. Results would be meaningless. "
            f"Either set AUC_EMBED_MODEL back to '{stamped}', or rebuild the "
            f"index with: python auc/rag/build_index.py"
        )
        return result

    if stamped is None:
        result["level"] = "warning"
        result["message"] = (
            f"The index works, but it predates embedding-model stamping, so a "
            f"mismatch cannot be detected. Rebuild it once with "
            f"'python auc/rag/build_index.py' to record that it was built with "
            f"'{EMBED_MODEL}'."
        )
        return result

    if count != expected:
        result["level"] = "warning"
        result["message"] = (
            f"The index holds {count} entries but {expected} were expected "
            f"({expected // 2} sub-competencies across 2 source documents). "
            f"Some reference material may not have been read. Rebuild with: "
            f"python auc/rag/build_index.py"
        )
        return result

    result["level"] = "ok"
    result["message"] = (
        f"ACGME index ready — {count} entries, embedded with {EMBED_MODEL}."
    )
    return result

# --- Routing ---------------------------------------------------------------
def route_comments(comments, ontology, collection):
    """Route each comment to one or more sub-competency ids.

    comments: list of {"text": str, "label": str}.
    Returns {sub_id: [comment_dict, ...]}. Each comment_dict gains a "routed_by"
    field ("keyword" or "semantic"). Comments with no keyword hit fall back to a
    top-1 semantic search so nothing is dropped.
    """
    subs = ontology["subcompetencies"]
    routed = {}

    for comment in comments:
        text = (comment.get("text") or "").strip()
        if not text:
            continue
        lowered = text.lower()

        matched = []
        for sub in subs:
            if any(kw.lower() in lowered for kw in sub["keywords"]):
                matched.append(sub["id"])

        if matched:
            tagged = {**comment, "routed_by": "keyword"}
        else:
            # Semantic fallback: closest sub-competency by embedding similarity.
            result = collection.query(query_embeddings=[_embed(text)], n_results=1)
            metas = result.get("metadatas") or [[]]
            if not metas or not metas[0]:
                # Index unexpectedly empty; surface rather than silently drop.
                raise RagUnavailable("ACGME index returned no results for fallback.")
            matched = [metas[0][0]["id"]]
            tagged = {**comment, "routed_by": "semantic"}

        for sub_id in matched:
            routed.setdefault(sub_id, []).append(tagged)

    return routed


def fetch_chunks(collection, sub_id):
    """Return (milestones_chunk, supplemental_chunk) for a sub-competency id.

    Both chunks are matched purely by metadata id (no embedding needed). Either may
    be None if a source is missing from the index.
    """
    got = collection.get(where={"id": sub_id})
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []

    milestones = supplemental = None
    for meta, doc in zip(metas, docs):
        if meta.get("source") == MILESTONES_SOURCE:
            milestones = doc
        elif meta.get("source") == SUPPLEMENT_SOURCE:
            supplemental = doc
    return milestones, supplemental


# --- Prompt assembly -------------------------------------------------------
INSTRUCTIONS = (
    "TASK: Write a resident development summary organized by the six ACGME "
    "competency domains, in this order: Patient Care; Medical Knowledge; "
    "Systems-Based Practice; Practice-Based Learning and Improvement; "
    "Professionalism; Interpersonal and Communication Skills.\n"
    "For each domain, address every sub-competency that has evidence above. For "
    "each such sub-competency:\n"
    "  1. Summarize what the resident's routed evidence shows.\n"
    "  2. Ground the assessment in the retrieved ACGME milestone descriptors and "
    "supplemental examples, referencing the relevant level language.\n"
    "  3. Suggest a milestone level from 1 to 5, give a one-sentence reason, and "
    "cite the specific comment(s) the suggestion is based on.\n"
    "IMPORTANT: Frame every suggested level explicitly as a DRAFT for the Clinical "
    "Competency Committee to discuss, not a final or official score. If a domain "
    "has no evidence, say so briefly. Do not invent evidence that is not provided.\n"
    "Do not reproduce or quote the ACGME descriptor text in your output. Use it only "
    "to inform your judgment. Only write a section for a competency that has routed evidence above; for the rest, briefly note no evidence this cycle. Never reuse one comment as evidence for an unrelated competency."
)


def compose_prompt(resident_label, comments):
    """Build the grounded prompt for the model.

    resident_label: short description, e.g. "Jane Doe, PGY-2".
    comments: list of {"text": str, "label": str} evidence items.

    Returns (prompt_str, routing_info). Raises RagUnavailable if the ontology or
    index is missing/unusable, or if there is no routable evidence.
    """
    ontology = load_ontology()
    collection = open_collection()

    routed = route_comments(comments, ontology, collection)
    if not routed:
        raise RagUnavailable("No comment text was available to route to ACGME competencies.")

    domains = ontology["domains"]
    sub_by_id = {s["id"]: s for s in ontology["subcompetencies"]}

    sections = []
    routing_info = {}  # sub_id -> count, for server-side logging

    for domain_code in DOMAIN_ORDER:
        domain_name = domains[domain_code]
        # Sub-competencies in this domain that have evidence, in ontology order.
        domain_subs = [
            s for s in ontology["subcompetencies"]
            if s["domain"] == domain_code and s["id"] in routed
        ]
        if not domain_subs:
            continue

        block = [f"\n## Competency Domain: {domain_name}"]
        for sub in domain_subs:
            sub_id = sub["id"]
            evidence = routed[sub_id]
            routing_info[sub_id] = len(evidence)
            milestones_chunk, supplemental_chunk = fetch_chunks(collection, sub_id)

            block.append(f"\n### {sub_id} — {sub['name']}")
            block.append("Resident evidence routed here:")
            for item in evidence:
                via = item.get("routed_by", "keyword")
                block.append(f"  - ({item['label']}; matched via {via}) {item['text']}")

            if milestones_chunk:
                block.append("\nACGME milestone level descriptors:\n" + milestones_chunk)
            if supplemental_chunk:
                block.append("\nACGME supplemental guide (intent and examples by level):\n" + supplemental_chunk)
        sections.append("\n".join(block))

    if not sections:
        raise RagUnavailable("Routed evidence did not map to any known competency domain.")

    header = (
        f"You are helping a program director prepare for a Clinical Competency "
        f"Committee (CCC) meeting for {resident_label}. Below is the resident's "
        f"documented evidence, routed to specific ACGME Internal Medicine "
        f"sub-competencies and grounded in the official ACGME milestone descriptors "
        f"and supplemental-guide examples.\n"
        f"\n=== GROUNDED EVIDENCE BY COMPETENCY ===\n"
    )

    prompt = header + "\n".join(sections) + "\n\n=== END EVIDENCE ===\n\n" + INSTRUCTIONS
    return prompt, routing_info
