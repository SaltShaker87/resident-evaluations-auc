"""Which retrieval engine grounds the summaries, and whether this machine can run it.

rag_retrieval.py knows HOW each engine searches the ACGME index. This module
decides WHICH one is used:

  * The choice is made in Settings and stored in the database (app_settings).
    It is one choice for the whole app rather than one per browser, because the
    index it selects lives on the server.

  * With nothing stored, the default comes from the hardware: NVIDIA Nemotron on
    a DGX Spark, Standard (Ollama) everywhere else. It deliberately does NOT come
    from whether the Nemotron containers happen to be answering. That would swap
    the engine silently every time a container restarted, and summaries grounded
    differently from last time, with no record of why, is the kind of quiet
    failure this app refuses elsewhere. A chosen engine that is down is an error
    that says so.

    AUC_RETRIEVAL_ENGINE_DEFAULT overrides that hardware rule, for the installer
    to name a starting engine it knows works on this machine. See config.py.

  * A switch is refused unless this machine can run the engine right now and its
    index is built. "Can run" is tested rather than assumed from the hardware, so
    a program with its own NVIDIA server can run the Nemotron containers too.
"""

import functools
import shutil
import sqlite3
import subprocess

import httpx
import rag_retrieval
from config import (
    DATA_DIR,
    EMBED_MODEL,
    NIM_EMBED_URL,
    NIM_RERANK_URL,
    OLLAMA_URL,
    RETRIEVAL_ENGINE_DEFAULT,
)
from rag_retrieval import ENGINES, LABELS, NEMOTRON, OLLAMA

SETTING_KEY = "retrieval_engine"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

# Set by app.py via init_retrieval_engine() before the app starts serving requests.
_db_connection = None


def init_retrieval_engine(db_connection_factory):
    """Receive the database connection context manager from app.py."""
    global _db_connection
    _db_connection = db_connection_factory


class EngineUnavailable(Exception):
    """A switch to an engine this machine cannot run right now. The message says why."""


# ---------------------------------------------------------------------------
# Which engine
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def is_spark():
    """True on a machine built around NVIDIA's GB10 chip: the DGX Spark and the
    other makers' versions of it (the HP ZGX Nano among them).

    Asked of nvidia-smi rather than guessed from the processor, because plenty of
    ARM machines are not Sparks. Cached: the hardware does not change while the
    app is running.
    """
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "GB10" in out.stdout


def default_engine():
    """The engine with nothing chosen in Settings.

    AUC_RETRIEVAL_ENGINE_DEFAULT overrides the hardware rule, because a Spark
    whose Nemotron containers would not start during installation would
    otherwise default to an engine that is not running and fail every summary.
    An unrecognised name is ignored rather than trusted — a typo in the
    environment must not leave the app with no engine at all.
    """
    if RETRIEVAL_ENGINE_DEFAULT in ENGINES:
        return RETRIEVAL_ENGINE_DEFAULT
    return NEMOTRON if is_spark() else OLLAMA


def resolve(stored):
    """The engine in force, given what (if anything) is stored."""
    return stored if stored in ENGINES else default_engine()


def get_active():
    with _db_connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)
        ).fetchone()
    return resolve(row[0] if row else None)


def stored_on_disk():
    """The stored choice, read straight from the database file, or None.

    For scripts that run without the app (preflight, test_query). The database is
    opened immutable, so this cannot disturb a running app.
    """
    db = DATA_DIR / "auc.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        # No app_settings table: a database from before there was a choice.
        return None
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Can this machine run it
# ---------------------------------------------------------------------------

def _answers(url):
    try:
        return httpx.get(url, timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


def availability(engine):
    """Whether this machine can run the engine right now: {"available", "reason"}.

    Never raises.
    """
    rag_retrieval.check_engine(engine)

    if engine == NEMOTRON:
        for what, url in (("embedding", NIM_EMBED_URL), ("reranking", NIM_RERANK_URL)):
            if not _answers(f"{url}/v1/health/ready"):
                return {
                    "available": False,
                    "reason": (
                        f"The Nemotron {what} container is not answering at {url}. "
                        f"{rag_retrieval.NEMOTRON_START_HINT}"
                    ),
                }
        return {"available": True, "reason": "Both Nemotron containers are answering."}

    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3.0)
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
    except (httpx.HTTPError, ValueError, KeyError):
        return {"available": False, "reason": f"Ollama is not answering at {OLLAMA_URL}."}

    # By exact name, because it must be the model that built the index. Ollama
    # adds ":latest" to a name given without a tag.
    if EMBED_MODEL not in names and f"{EMBED_MODEL}:latest" not in names:
        return {
            "available": False,
            "reason": (
                f"Ollama is running but the embedding model '{EMBED_MODEL}' is not "
                f"installed. Install it with: ollama pull {EMBED_MODEL}"
            ),
        }
    return {"available": True, "reason": f"Ollama is answering and has {EMBED_MODEL}."}


# ---------------------------------------------------------------------------
# Status and switching
# ---------------------------------------------------------------------------

def describe(engine):
    """Everything Settings shows about one engine: can it run, and is its index healthy."""
    return {
        "label": LABELS[engine],
        **availability(engine),
        "index": rag_retrieval.index_status(engine),
    }


def headline(described):
    """The one status line for an engine: its index health, unless it cannot run at all."""
    line = dict(described["index"])
    if not described["available"]:
        line["level"] = "error"
        line["message"] = (
            f"{described['reason']} Summaries cannot be generated until it is "
            f"back — or choose the other engine in Settings."
        )
    return line


def status():
    """What /api/rag/status returns.

    The top-level fields describe the engine in force, exactly as they described
    the single index before there were two engines, so anything reading them keeps
    working. "engines" adds the per-engine detail Settings needs to offer a switch.
    """
    active = get_active()
    engines = {engine: describe(engine) for engine in ENGINES}
    return {
        **headline(engines[active]),
        "engine": active,
        "default_engine": default_engine(),
        "is_spark": is_spark(),
        "engines": engines,
    }


def set_active(engine):
    """Make engine the one in force.

    Refused unless this machine can run it now and its index is built: a switch
    that would leave summaries broken is not a switch.
    """
    if engine not in ENGINES:
        raise EngineUnavailable(f"Unknown retrieval engine '{engine}'.")

    ready = availability(engine)
    if not ready["available"]:
        raise EngineUnavailable(ready["reason"])

    index = rag_retrieval.index_status(engine)
    if index["level"] == "error":
        raise EngineUnavailable(index["message"])

    with _db_connection() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = datetime('now')",
            (SETTING_KEY, engine),
        )
