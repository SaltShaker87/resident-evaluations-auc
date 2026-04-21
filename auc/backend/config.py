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
