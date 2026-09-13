"""Query the local ACGME RAG index.

Embeds a query string with a retrieval engine's embedder and warns if that is not
the model the index was built with. Prints the top 3 matching chunks with their
id, name, domain, source, and distance score. With the NVIDIA Nemotron engine it
also prints the order the reranker puts the shortlist in, which is the order the
app actually routes by.

    python auc/rag/test_query.py "resident missed a posterior circulation stroke"
    python auc/rag/test_query.py --engine nemotron "resident missed a posterior circulation stroke"

Without --engine it uses the engine the app is set to.
"""

import argparse
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent
BACKEND_DIR = RAG_DIR.parent / "backend"

TOP_K = 3

# Everything comes from the app's own modules — see the note in build_index.py.
sys.path.insert(0, str(BACKEND_DIR))
import rag_retrieval  # noqa: E402
import retrieval_engine  # noqa: E402


def print_match(rank, meta, detail):
    print(f"\n{rank}. [{meta['id']}] {meta['name']}")
    print(f"   domain:   {meta['domain']}")
    print(f"   source:   {meta['source']}")
    print(f"   {detail}")


def main():
    parser = argparse.ArgumentParser(description="Search the ACGME index the way the app does.")
    parser.add_argument(
        "--engine",
        choices=rag_retrieval.ENGINES,
        default=None,
        help="which engine's index to search (default: the one the app is set to)",
    )
    parser.add_argument("query", nargs="+", help="the text to look up")
    args = parser.parse_args()

    engine = args.engine or retrieval_engine.resolve(retrieval_engine.stored_on_disk())
    query = " ".join(args.query)

    try:
        collection = rag_retrieval.open_collection(engine)
    except rag_retrieval.RagUnavailable as e:
        sys.exit(str(e))

    # A mismatch here is the one failure that produces no error of its own:
    # results come back looking perfectly plausible and are in fact arbitrary.
    stamped = (collection.metadata or {}).get(rag_retrieval.EMBED_MODEL_KEY)
    configured = rag_retrieval.embedder(engine)
    if stamped and stamped != configured:
        print(
            f"WARNING: this index was built with '{stamped}' but you are "
            f"searching it with '{configured}'.\n"
            f"         The results below are meaningless. Rebuild the index "
            f"with: {rag_retrieval.rebuild_hint(engine)}\n"
        )

    try:
        vector = rag_retrieval.embed([query], engine, "query")[0]
    except rag_retrieval.RagUnavailable as e:
        sys.exit(str(e))

    shortlist = rag_retrieval.RERANK_CANDIDATES if engine == rag_retrieval.NEMOTRON else TOP_K
    results = collection.query(query_embeddings=[vector], n_results=max(TOP_K, shortlist))
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]
    documents = results["documents"][0]

    print(f"Engine: {rag_retrieval.LABELS[engine]}")
    print(f"Query:  {query}\n")
    print(f"Top {min(TOP_K, len(metadatas))} by embedding distance:")
    for rank, (meta, distance) in enumerate(
        zip(metadatas[:TOP_K], distances[:TOP_K], strict=False), start=1
    ):
        print_match(rank, meta, f"distance: {distance:.4f}")

    if engine == rag_retrieval.NEMOTRON:
        try:
            order = rag_retrieval.rerank(query, documents)
        except rag_retrieval.RagUnavailable as e:
            sys.exit(str(e))
        print(
            f"\nTop {min(TOP_K, len(order))} after reranking the {len(documents)}-entry "
            f"shortlist (the order the app routes by):"
        )
        for rank, index in enumerate(order[:TOP_K], start=1):
            print_match(rank, metadatas[index], f"was #{index + 1} by embedding distance")


if __name__ == "__main__":
    main()
