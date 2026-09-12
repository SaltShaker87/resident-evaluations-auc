import os

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
