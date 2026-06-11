import os

OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "clinical-reasoning:latest")
OLLAMA_MAX_TOKENS: int = int(os.environ.get("OLLAMA_MAX_TOKENS", "2048"))

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
