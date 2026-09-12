#!/bin/bash
# ============================================================
# AUC — can this model actually write summaries?
#
#   bash auc/check-model.sh nemotron:latest
#   bash auc/check-model.sh                   # the configured default
#   bash auc/check-model.sh gpt-oss:20b --show-output
#
# Any model in `ollama list` can be picked in Settings, but only some of them
# work. The generator needs two behaviours no model card will tell you about:
# JSON-constrained output, and quoting character-for-character. This runs the
# real code path against made-up committee comments and reports on both.
#
# Run it before trusting a new model with a meeting — in particular after
# moving to a bigger model on the Spark.
#
# Touches no database and writes nothing to the validation log.
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$SCRIPT_DIR/backend/venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "No Python environment at backend/venv. Run: bash $SCRIPT_DIR/setup.sh"
    exit 1
fi

exec "$VENV_PY" "$SCRIPT_DIR/backend/check_model.py" "$@"
