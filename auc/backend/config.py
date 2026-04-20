import os

OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "clinical-reasoning:latest")
OLLAMA_MAX_TOKENS: int = int(os.environ.get("OLLAMA_MAX_TOKENS", "2048"))
