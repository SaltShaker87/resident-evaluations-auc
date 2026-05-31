"""Build the local ACGME RAG index.

Reads the two ACGME markdown files in auc/rag/documents/, splits each into one
chunk per sub-competency (h3 headings), tags each chunk with the canonical
sub-competency id/name/domain from auc/rag/ontology/acgme_ontology.json, embeds
each chunk with Ollama's qwen3-embedding:0.6b, and stores everything in a local
ChromaDB PersistentClient at auc/rag/chroma_db/.

Local-only: the only network call is to the local Ollama server. Idempotent: the
collection is deleted and recreated on every run for a clean rebuild.

    python auc/rag/build_index.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

import chromadb
import httpx

# --- Paths -----------------------------------------------------------------
RAG_DIR = Path(__file__).resolve().parent
DOCUMENTS_DIR = RAG_DIR / "documents"
ONTOLOGY_FILE = RAG_DIR / "ontology" / "acgme_ontology.json"
CHROMA_DIR = RAG_DIR / "chroma_db"
BACKEND_DIR = RAG_DIR.parent / "backend"

DOCUMENT_FILES = ["acgme_im_milestones.md", "acgme_im_supplemental_guide.md"]
COLLECTION_NAME = "acgme_guidelines"
EMBED_MODEL = "qwen3-embedding:0.6b"

# Reuse the app's Ollama base URL from config if available, else default.
sys.path.insert(0, str(BACKEND_DIR))
try:
    from config import OLLAMA_URL  # type: ignore
except Exception:
    import os

    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


def load_ontology():
    """Return (name->meta) and (domain_code->domain_name) from the ontology.

    meta is a dict with the canonical id, name, and full domain name. Matching is
    keyed on the exact sub-competency name, which is identical to the markdown h3
    heading text.
    """
    data = json.loads(ONTOLOGY_FILE.read_text(encoding="utf-8"))
    domains = data["domains"]
    by_name = {}
    for sub in data["subcompetencies"]:
        by_name[sub["name"]] = {
            "id": sub["id"],
            "name": sub["name"],
            "domain": domains[sub["domain"]],
        }
    return by_name, domains


def split_into_subcompetencies(text, by_name):
    """Split markdown into one chunk per sub-competency h3 heading.

    Yields (meta, chunk_text). Only h3 headings whose text matches an ontology
    sub-competency name are kept (skips ## sections like the overall rating or the
    1.0->2.0 mapping table).
    """
    lines = text.splitlines()
    # Find the start line of every h3 heading.
    starts = [i for i, ln in enumerate(lines) if ln.startswith("### ")]
    for idx, start in enumerate(starts):
        heading = lines[start][4:].strip()
        meta = by_name.get(heading)
        if meta is None:
            continue
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[start:end]).strip()
        yield meta, chunk


def embed(text, client):
    """Return the embedding vector for a single string via Ollama /api/embed."""
    r = client.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
    )
    r.raise_for_status()
    return r.json()["embeddings"][0]


def main():
    by_name, _ = load_ontology()

    ids, documents, embeddings, metadatas = [], [], [], []
    source_counts = Counter()

    with httpx.Client(timeout=120.0) as http:
        for filename in DOCUMENT_FILES:
            path = DOCUMENTS_DIR / filename
            if not path.exists():
                sys.exit(f"Missing document: {path}")
            text = path.read_text(encoding="utf-8")
            for meta, chunk in split_into_subcompetencies(text, by_name):
                # Record id must be unique; the same sub-competency id appears in
                # both source files, so namespace it by source filename.
                record_id = f"{path.stem}:{meta['id']}"
                ids.append(record_id)
                documents.append(chunk)
                embeddings.append(embed(chunk, http))
                metadatas.append(
                    {
                        "id": meta["id"],
                        "name": meta["name"],
                        "domain": meta["domain"],
                        "source": filename,
                    }
                )
                source_counts[filename] += 1

    # Idempotent rebuild: drop and recreate the collection.
    chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        chroma.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = chroma.create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    collection.add(
        ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas
    )

    print(f"Indexed {len(ids)} chunks into collection '{COLLECTION_NAME}' at {CHROMA_DIR}")
    print("Breakdown by source file:")
    for filename in DOCUMENT_FILES:
        print(f"  {filename}: {source_counts[filename]} chunks")


if __name__ == "__main__":
    main()
