"""config.py is the single place every environment variable is read, so these
check the defaults have not moved and the overrides actually work."""

import importlib

import pytest


@pytest.fixture
def reloaded_config(monkeypatch):
    """config reads the environment at import time, so overrides need a reload."""

    def load(**env):
        import config

        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield load

    import config

    importlib.reload(config)


def test_defaults_are_what_the_machine_has_always_used(reloaded_config, monkeypatch):
    for key in ("AUC_HOST", "AUC_PORT", "AUC_EMBED_MODEL", "OLLAMA_URL"):
        monkeypatch.delenv(key, raising=False)
    config = reloaded_config()

    assert config.HOST == "0.0.0.0"
    assert config.PORT == 3000
    assert config.OLLAMA_URL == "http://localhost:11434"
    assert config.EMBED_MODEL == "qwen3-embedding:0.6b"


def test_host_and_port_can_be_overridden(reloaded_config):
    """The prerequisite for binding to localhost behind Tailscale Serve."""
    config = reloaded_config(AUC_HOST="127.0.0.1", AUC_PORT="3001")

    assert config.HOST == "127.0.0.1"
    assert config.PORT == 3001


def test_the_embedding_model_can_be_overridden(reloaded_config):
    config = reloaded_config(AUC_EMBED_MODEL="nomic-embed-text")
    assert config.EMBED_MODEL == "nomic-embed-text"


def test_the_data_directory_follows_the_environment(reloaded_config, tmp_path):
    config = reloaded_config(AUC_DATA_DIR=str(tmp_path / "elsewhere"))
    assert config.DATA_DIR == tmp_path / "elsewhere"


def test_every_environment_variable_is_documented(reloaded_config):
    """A variable the code reads but .env.example does not mention is one you
    will not know exists on the new machine."""
    from conftest import AUC_DIR

    example = AUC_DIR / ".env.example"
    assert example.exists(), "auc/.env.example is missing"
    documented = example.read_text(encoding="utf-8")

    backend = AUC_DIR / "backend"
    read_by_code = set()
    for source in backend.glob("*.py"):
        for line in source.read_text(encoding="utf-8").splitlines():
            if "environ.get(" in line or "getenv(" in line:
                start = line.split("(", 1)[1]
                name = start.split('"')[1] if '"' in start else start.split("'")[1]
                read_by_code.add(name)

    undocumented = sorted(name for name in read_by_code if name not in documented)
    assert not undocumented, f"read by the code but missing from .env.example: {undocumented}"
