"""The retrieval-engine choice.

NVIDIA Nemotron by default on a DGX Spark, Standard (Ollama) everywhere else, and
switchable in Settings — but only to an engine this machine can actually run,
because a switch that leaves summaries broken is not a switch.
"""

import subprocess

import pytest


@pytest.fixture
def machine(monkeypatch):
    """Make the hardware, each engine's availability and each index's health
    whatever a test says. The real checks would need a GPU, Ollama, the
    Nemotron containers and a built index."""
    import rag_retrieval
    import retrieval_engine

    state = {
        "spark": False,
        "available": {"ollama": True, "nemotron": True},
        "index": {"ollama": "ok", "nemotron": "ok"},
    }

    def availability(engine):
        up = state["available"][engine]
        return {"available": up, "reason": f"{engine} is {'running' if up else 'not running'}"}

    def index_status(engine):
        level = state["index"][engine]
        return {"level": level, "message": f"{engine} index is {level}", "engine": engine}

    monkeypatch.setattr(retrieval_engine, "is_spark", lambda: state["spark"])
    monkeypatch.setattr(retrieval_engine, "availability", availability)
    monkeypatch.setattr(rag_retrieval, "index_status", index_status)
    return state


# --- The default ------------------------------------------------------------

def test_a_spark_defaults_to_nemotron(logged_in, machine):
    machine["spark"] = True
    status = logged_in.get("/api/rag/status").json()

    assert status["engine"] == "nemotron"
    assert status["default_engine"] == "nemotron"
    assert status["is_spark"] is True


def test_any_other_machine_defaults_to_standard(logged_in, machine):
    status = logged_in.get("/api/rag/status").json()

    assert status["engine"] == "ollama"
    assert status["default_engine"] == "ollama"


def test_a_chosen_engine_that_goes_down_is_an_error_not_a_quiet_swap(logged_in, machine):
    """The default comes from the hardware, not from what happens to be running.
    Otherwise a container restart would silently change how summaries are grounded."""
    machine["spark"] = True
    machine["available"]["nemotron"] = False
    status = logged_in.get("/api/rag/status").json()

    assert status["engine"] == "nemotron"
    assert status["level"] == "error"
    assert "nemotron is not running" in status["message"]
    assert "Settings" in status["message"]


def test_status_describes_both_engines(logged_in, machine):
    machine["available"]["nemotron"] = False
    engines = logged_in.get("/api/rag/status").json()["engines"]

    assert set(engines) == {"ollama", "nemotron"}
    assert engines["ollama"]["available"] is True
    assert engines["nemotron"]["available"] is False
    assert engines["nemotron"]["reason"] == "nemotron is not running"
    assert engines["nemotron"]["index"]["level"] == "ok"
    assert engines["nemotron"]["label"] == "NVIDIA Nemotron"


# --- The default set in the environment ---------------------------------------

@pytest.fixture
def engine_default(monkeypatch):
    """Set AUC_RETRIEVAL_ENGINE_DEFAULT. config reads the environment once, at
    import time, so the name has to be replaced where the module holds it."""
    import retrieval_engine

    def set_to(value):
        monkeypatch.setattr(retrieval_engine, "RETRIEVAL_ENGINE_DEFAULT", value)

    return set_to


def test_the_environment_default_picks_standard_on_a_spark(logged_in, machine, engine_default):
    """What the installer writes on a Spark whose Nemotron containers would not
    start: summaries work from the first minute rather than failing against an
    engine that is not running."""
    machine["spark"] = True
    engine_default("ollama")
    status = logged_in.get("/api/rag/status").json()

    assert status["engine"] == "ollama"
    assert status["default_engine"] == "ollama"
    assert status["is_spark"] is True


def test_the_environment_default_picks_nemotron_on_another_machine(logged_in, machine, engine_default):
    engine_default("nemotron")
    status = logged_in.get("/api/rag/status").json()

    assert status["engine"] == "nemotron"
    assert status["default_engine"] == "nemotron"


def test_a_choice_made_in_settings_beats_the_environment_default(logged_in, machine, engine_default):
    engine_default("nemotron")
    response = logged_in.put("/api/rag/engine", json={"engine": "ollama"})

    assert response.status_code == 200, response.text
    assert logged_in.get("/api/rag/status").json()["engine"] == "ollama"


def test_an_unknown_environment_default_is_ignored(logged_in, machine, engine_default):
    """A typo there must leave the hardware rule in charge, not the app without
    an engine at all."""
    machine["spark"] = True
    engine_default("nemotron-lightning")

    assert logged_in.get("/api/rag/status").json()["default_engine"] == "nemotron"


# --- Switching ----------------------------------------------------------------

def test_a_choice_made_in_settings_outlives_the_default(logged_in, machine):
    machine["spark"] = True
    response = logged_in.put("/api/rag/engine", json={"engine": "ollama"})

    assert response.status_code == 200, response.text
    assert response.json()["engine"] == "ollama"
    assert logged_in.get("/api/rag/status").json()["engine"] == "ollama"


def test_a_switch_to_an_engine_this_machine_cannot_run_is_refused(logged_in, machine):
    machine["available"]["nemotron"] = False
    response = logged_in.put("/api/rag/engine", json={"engine": "nemotron"})

    assert response.status_code == 409
    assert response.json()["detail"] == "nemotron is not running"
    assert logged_in.get("/api/rag/status").json()["engine"] == "ollama"


def test_a_switch_to_an_engine_without_an_index_is_refused(logged_in, machine):
    machine["index"]["nemotron"] = "error"
    response = logged_in.put("/api/rag/engine", json={"engine": "nemotron"})

    assert response.status_code == 409
    assert "nemotron index is error" in response.json()["detail"]


def test_an_index_that_only_warns_does_not_block_a_switch(logged_in, machine):
    machine["index"]["nemotron"] = "warning"
    response = logged_in.put("/api/rag/engine", json={"engine": "nemotron"})

    assert response.status_code == 200, response.text
    assert response.json()["engine"] == "nemotron"


def test_an_unknown_engine_is_rejected(logged_in, machine):
    assert logged_in.put("/api/rag/engine", json={"engine": "chroma"}).status_code == 422


def test_switching_requires_a_login(client, machine):
    assert client.put("/api/rag/engine", json={"engine": "ollama"}).status_code == 401


def test_scripts_can_read_the_choice_without_the_app(logged_in, machine):
    """preflight.sh and test_query.py read it straight from the database file."""
    import retrieval_engine

    assert retrieval_engine.stored_on_disk() is None
    logged_in.put("/api/rag/engine", json={"engine": "nemotron"})
    assert retrieval_engine.stored_on_disk() == "nemotron"


# --- Recognising a Spark --------------------------------------------------------

@pytest.fixture
def fresh_is_spark():
    import retrieval_engine

    retrieval_engine.is_spark.cache_clear()
    yield retrieval_engine
    retrieval_engine.is_spark.cache_clear()


@pytest.mark.parametrize("gpus, expected", [
    ("NVIDIA GB10\n", True),
    # The current machine.
    ("NVIDIA GeForce GTX 1080 Ti\nNVIDIA GeForce GTX 1080\nNVIDIA GeForce GTX 1080\n", False),
])
def test_a_spark_is_recognised_by_its_gpu(fresh_is_spark, monkeypatch, gpus, expected):
    monkeypatch.setattr(fresh_is_spark.shutil, "which", lambda _name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        fresh_is_spark.subprocess, "run",
        lambda args, **_kw: subprocess.CompletedProcess(args, 0, stdout=gpus, stderr=""),
    )
    assert fresh_is_spark.is_spark() is expected


def test_a_machine_without_an_nvidia_driver_is_not_a_spark(fresh_is_spark, monkeypatch):
    monkeypatch.setattr(fresh_is_spark.shutil, "which", lambda _name: None)
    assert fresh_is_spark.is_spark() is False
