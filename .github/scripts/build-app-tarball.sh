#!/usr/bin/env bash
# Build the AUC release tarball (auc-<version>.tar.gz) exactly as shipped on GitHub.
#
# The installer extracts this into ~/.local/share/auc/app/<version>/ — same layout
# as setup.sh expects, but without node_modules, source trees, local venv, or machine data.
#
# Usage: build-app-tarball.sh <version> <output-dir> [--skip-frontend]
#   --skip-frontend  Fail if frontend/dist is missing instead of running npm build.

set -euo pipefail

usage() {
  echo "Usage: $0 <version> <output-dir> [--skip-frontend]" >&2
  exit 1
}

VERSION=""
OUTPUT_DIR=""
SKIP_FRONTEND=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --skip-frontend)
      SKIP_FRONTEND=1
      shift
      ;;
    -h | --help)
      usage
      ;;
    *)
      if [[ -z "$VERSION" ]]; then
        VERSION=$1
      elif [[ -z "$OUTPUT_DIR" ]]; then
        OUTPUT_DIR=$1
      else
        usage
      fi
      shift
      ;;
  esac
done

[[ -n "$VERSION" && -n "$OUTPUT_DIR" ]] || usage

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
AUC_DIR="$REPO_ROOT/auc"

if [[ ! -d "$AUC_DIR" ]]; then
  echo "Expected AUC tree at $AUC_DIR" >&2
  exit 1
fi

# The VERSION file exists only inside the archive. It is written into the
# working tree because tar reads from there, and removed again on exit so a
# local run does not leave a checkout claiming to be a release.
printf '%s\n' "$VERSION" >"$AUC_DIR/VERSION"
trap 'rm -f "$AUC_DIR/VERSION"' EXIT

if [[ ! -f "$AUC_DIR/frontend/dist/index.html" ]]; then
  if [[ "$SKIP_FRONTEND" -eq 1 ]]; then
    echo "frontend/dist/index.html missing and --skip-frontend was set" >&2
    exit 1
  fi
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    echo "Building frontend (dist was missing)..."
    (
      cd "$AUC_DIR/frontend"
      if [[ -f package-lock.json ]]; then
        npm ci --no-audit --no-fund
      else
        npm install --no-audit --no-fund
      fi
      npm run build
    )
  else
    echo "frontend/dist is missing and Node.js is not available to build it" >&2
    exit 1
  fi
fi

mkdir -p "$OUTPUT_DIR"
TARBALL_NAME="auc-${VERSION}.tar.gz"
TARBALL_PATH="$OUTPUT_DIR/$TARBALL_NAME"

# Single top-level directory auc/ — matches CONTRACT §1 and what the installer downloads.
#
# The second group of excludes is about a maintainer's own machine rather than
# CI: a local .env can hold a MedHub key, and *.csv / *.zip / *.pdf are what
# resident data looks like when exported. None of it belongs in a public archive.
tar -czf "$TARBALL_PATH" -C "$REPO_ROOT" \
  --exclude='auc/frontend/node_modules' \
  --exclude='auc/frontend/src' \
  --exclude='auc/backend/venv' \
  --exclude='__pycache__' \
  --exclude='auc/data' \
  --exclude='auc/rag/chroma_db' \
  --exclude='*.db' \
  --exclude='*.db-wal' \
  --exclude='*.db-shm' \
  --exclude='*.log' \
  --exclude='.gitignore' \
  --exclude='auc/.env' \
  --exclude='auc/.env.*' \
  --exclude='*.csv' \
  --exclude='*.zip' \
  --exclude='*.pdf' \
  --exclude='*.bak' \
  auc

# sha256sum is coreutils (Linux, CI); shasum is what a Mac has.
(
  cd "$OUTPUT_DIR"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$TARBALL_NAME" >"${TARBALL_NAME}.sha256"
  else
    shasum -a 256 "$TARBALL_NAME" >"${TARBALL_NAME}.sha256"
  fi
)

echo "Wrote $TARBALL_PATH and ${TARBALL_PATH}.sha256"
