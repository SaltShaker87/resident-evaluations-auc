import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Where the data lives
#
# AUC_DATA_DIR : the folder holding auc.db, photos/ and logs/. Defaults to
#                auc/data next to the code, which is where it has always been.
#
#                Set it to put the data on a different disk, or to run the app
#                against a scratch copy without touching the real database —
#                which is what the tests do.
#
#                The database is not tracked in git, so a fresh clone starts
#                with an empty one. Whatever this points at is the thing your
#                backups are protecting; see auc/BACKUPS.md.
# ---------------------------------------------------------------------------
DATA_DIR: Path = Path(
    os.environ.get("AUC_DATA_DIR") or Path(__file__).resolve().parent.parent / "data"
)

# ---------------------------------------------------------------------------
# Where the app listens
#
# AUC_HOST : which network interfaces to accept connections on.
#            0.0.0.0 (the default, and what this has always done) means every
#            interface — anyone who can reach this machine on the network can
#            reach the app. That is fine on a trusted home network.
#
#            On hospital wifi it is not: the login page, and the password typed
#            into it, cross the network over plain HTTP where they can be read.
#            There, set this to 127.0.0.1 so only this machine can connect, and
#            put Tailscale Serve in front of it for HTTPS. Do not set it to
#            127.0.0.1 without a proxy in front, or you will lock yourself out
#            of a headless machine.
#
# AUC_PORT : the port to listen on. Default 3000.
#
# These are read by run.sh and written into the systemd unit by setup.sh, so
# changing them takes effect for the service too.
# ---------------------------------------------------------------------------
HOST: str = os.environ.get("AUC_HOST", "0.0.0.0")
PORT: int = int(os.environ.get("AUC_PORT", "3000"))

OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "clinical-reasoning:latest")
OLLAMA_MAX_TOKENS: int = int(os.environ.get("OLLAMA_MAX_TOKENS", "2048"))

# ---------------------------------------------------------------------------
# Which model writes the summaries
#
# There is no single setting for this. The model is chosen per generation, and
# the first of these that is set wins:
#
#   1. The model picked in Settings, sent by the browser as ?model= on every
#      generation request. This is what normally decides it.
#   2. AUC_SUMMARY_MODEL, read in summary_builder.py — summaries only.
#   3. OLLAMA_MODEL below — the last resort.
#
# Any model in `ollama list` can be used. If a summary ever comes out of a
# model you did not expect, that list is the order to check.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The ACGME index
#
# AUC_EMBED_MODEL : the model used BOTH to build the ACGME index and to search
#                   it. Unlike the generation model above, this one is not
#                   interchangeable. Embeddings from two different models are
#                   not comparable, so searching an index with a model other
#                   than the one that built it returns confident nonsense with
#                   no error at all.
#
#                   build_index.py stamps this name into the index, and
#                   rag_retrieval.index_status() compares the two, so a
#                   mismatch is reported rather than silently tolerated.
#
#                   If you change it, rebuild the index in the same breath:
#                     python auc/rag/build_index.py
# ---------------------------------------------------------------------------
EMBED_MODEL: str = os.environ.get("AUC_EMBED_MODEL", "qwen3-embedding:0.6b")

# ---------------------------------------------------------------------------
# The NVIDIA Nemotron retrieval engine
#
# The alternative to the Ollama embedding model above: NVIDIA's Nemotron
# embedding and reranking models, each running as a local container (a "NIM").
# Which engine is in use is chosen in Settings, not here. These only say where
# the containers are and which models they serve. start-nemotron.sh starts
# them on these ports, reachable from this machine only.
#
# AUC_NIM_EMBED_URL   / AUC_NIM_EMBED_MODEL  : the embedding container. Like
#                       AUC_EMBED_MODEL, the model is stamped into its index,
#                       so changing it means rebuilding:
#                         python auc/rag/build_index.py --engine nemotron
# AUC_NIM_RERANK_URL  / AUC_NIM_RERANK_MODEL : the reranking container.
# AUC_RERANK_CANDIDATES : how many index entries the reranker chooses among
#                         for a comment that matched no keyword.
#
# Never point these at build.nvidia.com or any other hosted endpoint. That
# would send resident comments off this machine.
# ---------------------------------------------------------------------------
NIM_EMBED_URL: str = os.environ.get("AUC_NIM_EMBED_URL", "http://localhost:8001")
NIM_EMBED_MODEL: str = os.environ.get("AUC_NIM_EMBED_MODEL", "nvidia/nemotron-3-embed-1b")
NIM_RERANK_URL: str = os.environ.get("AUC_NIM_RERANK_URL", "http://localhost:8002")
NIM_RERANK_MODEL: str = os.environ.get("AUC_NIM_RERANK_MODEL", "nvidia/llama-nemotron-rerank-vl-1b-v2")
RERANK_CANDIDATES: int = int(os.environ.get("AUC_RERANK_CANDIDATES", "10"))

# ---------------------------------------------------------------------------
# MedHub API — fill these in once API documentation is obtained.
# Set via environment variables or edit the defaults below.
#
# MEDHUB_API_URL  : Base URL for the MedHub REST API
#                   e.g. "https://your-institution.medhub.com/api/v1"
# MEDHUB_API_KEY  : API key or bearer token for authentication
#                   Check MedHub admin panel under Settings > API Access
# ---------------------------------------------------------------------------
MEDHUB_API_URL: str = os.environ.get("MEDHUB_API_URL", "")
MEDHUB_API_KEY: str = os.environ.get("MEDHUB_API_KEY", "")

# ---------------------------------------------------------------------------
# Automated backups
#
# AUC_BACKUP_DIR   : Folder the daily backup writes into. Point this at a
#                    cloud-synced folder (e.g. your institutional OneDrive)
#                    so backups end up safely off this machine. Leave empty
#                    to disable the automated backup. See auc/BACKUPS.md.
# AUC_BACKUP_KEEP_DAYS : How many days of backups to keep before pruning.
# ---------------------------------------------------------------------------
BACKUP_DIR: str = os.environ.get("AUC_BACKUP_DIR", "")
BACKUP_KEEP_DAYS: int = int(os.environ.get("AUC_BACKUP_KEEP_DAYS", "14"))
