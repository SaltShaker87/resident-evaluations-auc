"""The index-health reporting added for the Spark migration.

The state that matters most is the embedding-model mismatch: it is the one
failure that produces no error of its own. Retrieval keeps working and returns
arbitrary results, so summaries come out grounded in ACGME material chosen
essentially at random — which looks right.

There is one index per retrieval engine, and each is checked against its own
embedder.
"""

import importlib

import pytest

chromadb = pytest.importorskip(
    "chromadb", reason="the index layer is optional; without it summaries are simply down"
)

# Stamp the collection with whatever the engine is configured to use.
CONFIGURED = object()


@pytest.fixture
def fake_index(tmp_path, monkeypatch):
    """Build a real Chroma collection with made-up vectors.

    Real embeddings would need Ollama or the Nemotron containers; the status
    checks never look at the vectors, only at the count and the stamped model.
    """

    def build(*, engine="ollama", stamp=CONFIGURED, pairs=21):
        import rag_retrieval

        ontology = rag_retrieval.load_ontology()
        subs = ontology["subcompetencies"][:pairs]

        path = tmp_path / "chroma_db"
        metadata = {"hnsw:space": "cosine"}
        if stamp is CONFIGURED:
            stamp = rag_retrieval.embedder(engine)
        if stamp is not None:
            metadata[rag_retrieval.EMBED_MODEL_KEY] = stamp

        client = chromadb.PersistentClient(path=str(path))
        collection = client.create_collection(name=rag_retrieval.COLLECTIONS[engine],
                                              metadata=metadata)
        ids, docs, embeddings, metas = [], [], [], []
        for source in ("acgme_im_milestones", "acgme_im_supplemental_guide"):
            for sub in subs:
                ids.append(f"{source}:{sub['id']}")
                docs.append(sub["name"])
                embeddings.append([0.1, 0.2, 0.3])
                metas.append({"id": sub["id"], "name": sub["name"],
                              "domain": sub["domain"], "source": f"{source}.md"})
        collection.add(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)

        monkeypatch.setattr(rag_retrieval, "CHROMA_DIR", path)
        return rag_retrieval

    return build


def test_missing_index_is_an_error_that_says_what_to_do(tmp_path, monkeypatch):
    import rag_retrieval

    monkeypatch.setattr(rag_retrieval, "CHROMA_DIR", tmp_path / "nowhere")
    status = rag_retrieval.index_status("ollama")

    assert status["level"] == "error"
    assert "build_index.py" in status["message"]


def test_missing_index_raises_a_clean_error_rather_than_crashing(tmp_path, monkeypatch):
    import rag_retrieval

    monkeypatch.setattr(rag_retrieval, "CHROMA_DIR", tmp_path / "nowhere")
    with pytest.raises(rag_retrieval.RagUnavailable):
        rag_retrieval.open_collection("ollama")


def test_a_healthy_index_reports_ready(fake_index):
    rag_retrieval = fake_index()
    status = rag_retrieval.index_status("ollama")

    assert status["level"] == "ok"
    assert status["count"] == 42
    assert status["expected_count"] == 42
    assert status["index_model"] == status["configured_model"]


def test_a_different_embedding_model_is_reported_as_fatal(fake_index, monkeypatch):
    """The silent failure this whole mechanism exists to catch."""
    rag_retrieval = fake_index(stamp="qwen3-embedding:0.6b")
    monkeypatch.setattr(rag_retrieval, "EMBED_MODEL", "nomic-embed-text")

    status = rag_retrieval.index_status("ollama")

    assert status["level"] == "error"
    assert "qwen3-embedding:0.6b" in status["message"]
    assert "nomic-embed-text" in status["message"]


def test_an_index_built_before_stamping_warns_rather_than_failing(fake_index):
    """The state the existing Ubuntu machine's index is in: usable, but a
    mismatch could not be detected."""
    rag_retrieval = fake_index(stamp=None)
    status = rag_retrieval.index_status("ollama")

    assert status["level"] == "warning"
    assert status["index_model"] is None
    assert "build_index.py" in status["message"]


def test_an_incomplete_index_warns(fake_index):
    """Fewer chunks than the ontology has sub-competencies means the reference
    documents were not read properly."""
    rag_retrieval = fake_index(pairs=9)
    status = rag_retrieval.index_status("ollama")

    assert status["level"] == "warning"
    assert status["count"] == 18
    assert status["expected_count"] == 42


def test_an_empty_index_is_an_error(tmp_path, monkeypatch):
    import rag_retrieval

    path = tmp_path / "chroma_db"
    client = chromadb.PersistentClient(path=str(path))
    client.create_collection(
        name=rag_retrieval.COLLECTIONS["ollama"],
        metadata={"hnsw:space": "cosine",
                  rag_retrieval.EMBED_MODEL_KEY: rag_retrieval.EMBED_MODEL},
    )
    monkeypatch.setattr(rag_retrieval, "CHROMA_DIR", path)

    status = rag_retrieval.index_status("ollama")
    assert status["level"] == "error"
    assert status["count"] == 0


def test_the_expected_count_comes_from_the_ontology_not_a_hard_coded_42(fake_index, monkeypatch):
    """So that editing the ontology cannot make the status line lie."""
    rag_retrieval = fake_index()
    real_loader = rag_retrieval.load_ontology
    monkeypatch.setattr(
        rag_retrieval, "load_ontology",
        lambda: {**real_loader(), "subcompetencies": real_loader()["subcompetencies"][:5]},
    )
    assert rag_retrieval.index_status("ollama")["expected_count"] == 10


# --- One index per engine -----------------------------------------------------

def test_each_engine_has_its_own_index(fake_index):
    """Building the Standard index must not make the Nemotron one look built."""
    rag_retrieval = fake_index(engine="ollama")

    assert rag_retrieval.index_status("ollama")["level"] == "ok"
    nemotron = rag_retrieval.index_status("nemotron")
    assert nemotron["level"] == "error"
    assert "--engine nemotron" in nemotron["message"]


def test_a_healthy_nemotron_index_reports_ready(fake_index):
    rag_retrieval = fake_index(engine="nemotron")
    status = rag_retrieval.index_status("nemotron")

    assert status["level"] == "ok"
    assert status["index_model"] == f"nim:{rag_retrieval.NIM_EMBED_MODEL}"


def test_an_index_embedded_by_ollama_cannot_pass_for_a_nemotron_one(fake_index):
    """Same failure as a changed model, across engines: vectors from one
    embedder searched with the other's."""
    rag_retrieval = fake_index(engine="nemotron", stamp="qwen3-embedding:0.6b")
    status = rag_retrieval.index_status("nemotron")

    assert status["level"] == "error"
    assert "AUC_NIM_EMBED_MODEL" in status["message"]


def test_an_unknown_engine_is_a_programming_error_not_a_status():
    import rag_retrieval

    with pytest.raises(ValueError):
        rag_retrieval.index_status("chroma")


# --- Building ---------------------------------------------------------------

def load_build_index():
    from conftest import AUC_DIR

    build_index_path = AUC_DIR / "rag" / "build_index.py"
    assert build_index_path.exists()
    spec = importlib.util.spec_from_file_location("build_index", build_index_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("engine", ["ollama", "nemotron"])
def test_a_built_index_is_the_one_the_status_check_expects(engine, tmp_path, monkeypatch):
    """Build and search must agree on the collection and the stamp. There is
    one definition of each, in rag_retrieval; a second one in build_index is
    how an index ends up built with one model and searched with another."""
    import rag_retrieval

    monkeypatch.setattr(rag_retrieval, "CHROMA_DIR", tmp_path / "chroma_db")
    calls = set()

    def fake_embed(texts, eng, input_type):
        calls.add((eng, input_type))
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(rag_retrieval, "embed", fake_embed)

    build_index = load_build_index()
    by_name, _ = build_index.load_ontology()
    build_index.build(engine, by_name)

    status = rag_retrieval.index_status(engine)
    assert status["level"] == "ok", status["message"]
    assert status["index_model"] == rag_retrieval.embedder(engine)
    # Reference text is embedded as a passage, never as a query.
    assert calls == {(engine, "passage")}
