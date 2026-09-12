#!/bin/bash
# ============================================================
# AUC — run every check
#
#   bash auc/check.sh
#
# Lint and tests, backend and frontend, in one command. Run it before pushing
# and after pulling onto a new machine.
#
# This is about the CODE. Its companion, auc/preflight.sh, is about the
# MACHINE — models, index, services, disk. Two commands, and between them
# they cover "is this healthy" without reading a manual.
#
# The tests never touch real data: they point AUC_DATA_DIR at a scratch
# directory before importing the app, and assert that it worked.
#
# First time on a machine:
#   auc/backend/venv/bin/pip install -r auc/backend/requirements-dev.txt
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PY="$SCRIPT_DIR/backend/venv/bin/python"

FAILURES=()

if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_OK=""; C_BAD=""; C_DIM=""; C_OFF=""
fi

step() { printf '\n%s── %s %s\n' "$C_DIM" "$1" "$C_OFF"; }

record() {
    if [ "$1" -eq 0 ]; then
        printf '  %s✓ %s%s\n' "$C_OK" "$2" "$C_OFF"
    else
        printf '  %s✗ %s%s\n' "$C_BAD" "$2" "$C_OFF"
        FAILURES+=("$2")
    fi
}

if [ ! -x "$VENV_PY" ]; then
    echo "No Python environment at backend/venv. Run: bash $SCRIPT_DIR/setup.sh"
    exit 1
fi

if ! "$VENV_PY" -c "import pytest, ruff" &> /dev/null; then
    echo "The checking tools are not installed. Run:"
    echo "  $SCRIPT_DIR/backend/venv/bin/pip install -r $SCRIPT_DIR/backend/requirements-dev.txt"
    exit 1
fi

step "Python lint (ruff)"
(cd "$REPO_ROOT" && "$VENV_PY" -m ruff check .)
record $? "ruff"

step "Python tests (pytest)"
(cd "$REPO_ROOT" && "$VENV_PY" -m pytest)
record $? "pytest"

step "Shell scripts (shellcheck)"
if command -v shellcheck &> /dev/null; then
    shellcheck "$SCRIPT_DIR"/*.sh
    record $? "shellcheck"
else
    printf '  %s· shellcheck not installed — skipped (sudo apt install shellcheck)%s\n' "$C_DIM" "$C_OFF"
fi

step "Frontend lint (eslint)"
if [ -d "$SCRIPT_DIR/frontend/node_modules" ]; then
    (cd "$SCRIPT_DIR/frontend" && npm run --silent lint)
    record $? "eslint"
else
    printf '  %s· node_modules missing — skipped (cd frontend && npm ci)%s\n' "$C_DIM" "$C_OFF"
fi

step "Frontend build (vite)"
if [ -d "$SCRIPT_DIR/frontend/node_modules" ]; then
    (cd "$SCRIPT_DIR/frontend" && npm run --silent build > /dev/null)
    record $? "vite build"
else
    printf '  %s· node_modules missing — skipped%s\n' "$C_DIM" "$C_OFF"
fi

echo ""
if [ ${#FAILURES[@]} -eq 0 ]; then
    printf '  %sEverything passed.%s\n\n' "$C_OK" "$C_OFF"
    exit 0
fi

printf '  %sFailed: %s%s\n\n' "$C_BAD" "${FAILURES[*]}" "$C_OFF"
exit 1
