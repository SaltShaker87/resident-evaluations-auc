"""Query the local ACGME RAG index.

Embeds a query string with the configured embedding model (config.EMBED_MODEL)
and warns if that is not the model the index was built with. Prints the top 3
matching chunks with their id, name, domain, source, and distance score.

    python auc/rag/test_query.py "resident missed a posterior circulation stroke"
"""

import sys
from pathlib import Path

import chromadb
import httpx

RAG_DIR = Path(__file__).resolve().parent
CHROMA_DIR = RAG_DIR / "chroma_db"
BACKEND_DIR = RAG_DIR.parent / "backend"

COLLECTION_NAME = "acgme_guidelines"
EMBED_MODEL_KEY = "embed_model"
TOP_K = 3

# Read both from the app's own config — see the note in build_index.py.
sys.path.insert(0, str(BACKEND_DIR))
from config import EMBED_MODEL, OLLAMA_URL  # noqa: E402


def embed(text):
    """Return the embedding vector for a single string via Ollama /api/embed."""
    with httpx.Client(timeout=120.0) as http:
        r = http.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
        r.raise_for_status()
        return r.json()["embeddings"][0]


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: python test_query.py "your query string"')
    query = " ".join(sys.argv[1:])

    chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = chroma.get_collection(COLLECTION_NAME)

    # A mismatch here is the one failure that produces no error of its own:
    # results come back looking perfectly plausible and are in fact arbitrary.
    stamped = (collection.metadata or {}).get(EMBED_MODEL_KEY)
    if stamped and stamped != EMBED_MODEL:
        print(
            f"WARNING: this index was built with '{stamped}' but you are "
            f"searching it with '{EMBED_MODEL}'.\n"
            f"         The results below are meaningless. Rebuild the index "
            f"with: python auc/rag/build_index.py\n"
        )

    results = collection.query(
        query_embeddings=[embed(query)],
        n_results=TOP_K,
    )

    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    print(f'Query: {query}\n')
    print(f"Top {len(metadatas)} matches:")
    for rank, (meta, distance) in enumerate(zip(metadatas, distances), start=1):
        print(f"\n{rank}. [{meta['id']}] {meta['name']}")
        print(f"   domain:   {meta['domain']}")
        print(f"   source:   {meta['source']}")
        print(f"   distance: {distance:.4f}")


if __name__ == "__main__":
    main()
