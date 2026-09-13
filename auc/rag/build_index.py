"""Build the local ACGME RAG index.

Reads the two ACGME markdown files in auc/rag/documents/, splits each into one
chunk per sub-competency (h3 headings), tags each chunk with the canonical
sub-competency id/name/domain from auc/rag/ontology/acgme_ontology.json, embeds
each chunk, and stores everything in a local ChromaDB PersistentClient at
auc/rag/chroma_db/.

There is one collection per retrieval engine (see backend/rag_retrieval.py), each
embedded with that engine's own model and stamped with its name:

    python auc/rag/build_index.py                    # every engine this machine can run
    python auc/rag/build_index.py --engine ollama    # Standard only
    python auc/rag/build_index.py --engine nemotron  # NVIDIA Nemotron only

Local-only: the only network calls are to Ollama or the Nemotron containers on
this machine. Idempotent: each collection is deleted and recreated on every run
for a clean rebuild.
"""

import argparse
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
BACKEND_DIR = RAG_DIR.parent / "backend"

DOCUMENT_FILES = ["acgme_im_milestones.md", "acgme_im_supplemental_guide.md"]

# The embedders, the collection names and where the index lives all come from
# the app's own modules, and only from there. A local copy would recreate
# exactly the drift this is meant to prevent: an index built with one model,
# searched with another, returning confident nonsense and no error.
sys.path.insert(0, str(BACKEND_DIR))
import rag_retrieval  # noqa: E402
import retrieval_engine  # noqa: E402


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


def build(engine, by_name):
    """Embed every chunk with the engine's embedder and (re)write its collection.

    Returns the number of chunks indexed from each source file.
    """
    ids, documents, embeddings, metadatas = [], [], [], []
    source_counts = Counter()

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
            embeddings.append(rag_retrieval.embed([chunk], engine, "passage")[0])
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
    name = rag_retrieval.COLLECTIONS[engine]
    chroma = chromadb.PersistentClient(path=str(rag_retrieval.CHROMA_DIR))
    try:
        chroma.delete_collection(name)
    except Exception:
        pass
    collection = chroma.create_collection(
        name=name,
        metadata={
            "hnsw:space": "cosine",
            rag_retrieval.EMBED_MODEL_KEY: rag_retrieval.embedder(engine),
        },
    )
    collection.add(
        ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas
    )
    return source_counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the ACGME index for one retrieval engine, or for all of them."
    )
    parser.add_argument(
        "--engine",
        choices=[*rag_retrieval.ENGINES, "all"],
        default="all",
        help="which engine's index to build (default: every engine this machine can run)",
    )
    args = parser.parse_args(argv)

    if args.engine == "all":
        engines = []
        for engine in rag_retrieval.ENGINES:
            ready = retrieval_engine.availability(engine)
            if ready["available"]:
                engines.append(engine)
            else:
                print(f"Skipping {rag_retrieval.LABELS[engine]}: {ready['reason']}")
        if not engines:
            sys.exit("\nNo retrieval engine can run on this machine right now, so there is nothing to build with.")
    else:
        engines = [args.engine]

    by_name, _ = load_ontology()
    failed = []
    for engine in engines:
        label = rag_retrieval.LABELS[engine]
        try:
            counts = build(engine, by_name)
        except (rag_retrieval.RagUnavailable, httpx.HTTPError) as e:
            print(f"\n✗ {label}: could not build the index — {e}")
            failed.append(engine)
            continue

        print(
            f"\nIndexed {sum(counts.values())} chunks into collection "
            f"'{rag_retrieval.COLLECTIONS[engine]}' ({label}) at {rag_retrieval.CHROMA_DIR}"
        )
        print(f"Embedding model: {rag_retrieval.embedder(engine)} (stamped into the index)")
        print("Breakdown by source file:")
        for filename in DOCUMENT_FILES:
            print(f"  {filename}: {counts[filename]} chunks")

    if len(failed) < len(engines):
        print()
        print("Now restart the app so it picks this up:")
        print("  systemctl --user restart auc")
        print("A running app holds the index it opened at startup, so until you do")
        print("this it will keep searching — and reporting on — the previous one.")

    if failed:
        # Nothing is deleted until every chunk has been embedded, so a failed
        # engine's previous index, if it had one, is still there and unchanged.
        sys.exit(1)


if __name__ == "__main__":
    main()
