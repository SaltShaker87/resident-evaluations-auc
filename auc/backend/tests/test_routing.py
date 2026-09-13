"""How a comment that matches no ACGME keyword finds its sub-competency.

Keyword routing is the same under both engines. The difference is the fallback:
Standard takes the nearest index entry; NVIDIA Nemotron shortlists by embedding
and lets the reranker choose.
"""

import pytest
import rag_retrieval

# Must match no keyword in the ontology — checked below rather than assumed.
UNMATCHED = "Xyzzy plugh."


class FakeCollection:
    """Stands in for a Chroma collection: returns a fixed shortlist, nearest first."""

    def __init__(self, ids):
        self.ids = ids
        self.n_results = None

    def query(self, query_embeddings, n_results):
        self.n_results = n_results
        ids = self.ids[:n_results]
        return {
            "metadatas": [[{"id": i} for i in ids]],
            "documents": [[f"descriptor for {i}" for i in ids]],
        }


@pytest.fixture
def ontology():
    return rag_retrieval.load_ontology()


def fake_embed(texts, engine, input_type):
    return [[0.0, 0.0, 0.0] for _ in texts]


def test_the_unmatched_comment_really_matches_no_keyword(ontology):
    lowered = UNMATCHED.lower()
    assert not any(
        kw.lower() in lowered for sub in ontology["subcompetencies"] for kw in sub["keywords"]
    )


def test_nemotron_follows_the_reranker_not_the_nearest_vector(ontology, monkeypatch):
    embedded, reranked = [], []

    def recording_embed(texts, engine, input_type):
        embedded.append((engine, input_type))
        return fake_embed(texts, engine, input_type)

    def fake_rerank(query, passages):
        reranked.append((query, passages))
        return [1, 0]  # the second-nearest entry is the better match

    monkeypatch.setattr(rag_retrieval, "embed", recording_embed)
    monkeypatch.setattr(rag_retrieval, "rerank", fake_rerank)
    collection = FakeCollection(["PC1", "PROF1"])

    routed = rag_retrieval.route_comments(
        [{"text": UNMATCHED, "label": "note"}], ontology, collection, "nemotron"
    )

    assert list(routed) == ["PROF1"]
    assert routed["PROF1"][0]["routed_by"] == "reranked"
    # The comment is embedded as a query, against a shortlist of the configured size.
    assert embedded == [("nemotron", "query")]
    assert collection.n_results == rag_retrieval.RERANK_CANDIDATES
    assert reranked == [(UNMATCHED, ["descriptor for PC1", "descriptor for PROF1"])]


def test_standard_takes_the_nearest_entry_and_never_reranks(ontology, monkeypatch):
    def no_rerank(*_args):
        raise AssertionError("the Standard engine must not call the reranker")

    monkeypatch.setattr(rag_retrieval, "embed", fake_embed)
    monkeypatch.setattr(rag_retrieval, "rerank", no_rerank)
    collection = FakeCollection(["PC1", "PROF1"])

    routed = rag_retrieval.route_comments(
        [{"text": UNMATCHED, "label": "note"}], ontology, collection, "ollama"
    )

    assert list(routed) == ["PC1"]
    assert routed["PC1"][0]["routed_by"] == "semantic"
    assert collection.n_results == 1


@pytest.mark.parametrize("engine", rag_retrieval.ENGINES)
def test_a_keyword_match_never_touches_an_embedder(ontology, monkeypatch, engine):
    def unreachable(*_args):
        raise AssertionError("a keyword match must not need the embedder or reranker")

    monkeypatch.setattr(rag_retrieval, "embed", unreachable)
    monkeypatch.setattr(rag_retrieval, "rerank", unreachable)

    routed = rag_retrieval.route_comments(
        [{"text": "Excellent history taking on a difficult admission.", "label": "note"}],
        ontology, FakeCollection([]), engine,
    )

    assert "PC1" in routed
    assert routed["PC1"][0]["routed_by"] == "keyword"


# --- Talking to the Nemotron containers ---------------------------------------

def test_a_nemotron_container_that_is_not_running_is_a_clear_error(monkeypatch):
    # Port 9 is the discard port: nothing listens there, so the connection is refused.
    monkeypatch.setattr(rag_retrieval, "NIM_EMBED_URL", "http://127.0.0.1:9")

    with pytest.raises(rag_retrieval.RagUnavailable, match="start-nemotron.sh"):
        rag_retrieval.embed(["anything"], "nemotron", "query")


def test_nemotron_embeddings_come_back_in_input_order(monkeypatch):
    monkeypatch.setattr(
        rag_retrieval, "_nim_post",
        lambda *_a: {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]},
    )
    assert rag_retrieval.embed(["a", "b"], "nemotron", "passage") == [[1.0], [2.0]]


def test_the_rerank_order_is_by_score_whatever_order_it_arrives_in(monkeypatch):
    monkeypatch.setattr(
        rag_retrieval, "_nim_post",
        lambda *_a: {"rankings": [
            {"index": 0, "logit": -3.0},
            {"index": 2, "logit": 1.5},
            {"index": 1, "logit": -0.5},
        ]},
    )
    assert rag_retrieval.rerank("query", ["a", "b", "c"]) == [2, 1, 0]
