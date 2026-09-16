#!/bin/bash
# ============================================================
# AUC — start the NVIDIA Nemotron retrieval containers
#
#   bash auc/start-nemotron.sh
#
# Starts the two containers the "NVIDIA Nemotron" retrieval engine uses
# (nim/docker-compose.yml) and waits until both answer. setup.sh runs this on
# a DGX Spark. Run it by hand to bring them up anywhere else with a capable
# NVIDIA GPU, or to bring them back after a `docker compose down`.
#
# They restart on their own after a reboot, so once is normally enough.
#
# The first run downloads the images (from nvcr.io, which needs a one-time
# `docker login nvcr.io`; this script offers to do it) and then the model
# weights. docker-compose.yml asks NIM to fetch those from NGC, which needs
# NGC_API_KEY exported in this shell. That is a credential: export it here,
# never write it into a file in the repository.
#
# Waits up to AUC_NIM_WAIT_MINUTES (default 30) for the containers to be
# ready. Exits 0 when both answer, 1 otherwise.
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/nim/docker-compose.yml"
VENV_PY="$SCRIPT_DIR/backend/venv/bin/python"
WAIT_MINUTES="${AUC_NIM_WAIT_MINUTES:-30}"

# Where the app will look for them comes from config.py, so there is one
# place to change it.
if [ -x "$VENV_PY" ]; then
    EMBED_URL=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$SCRIPT_DIR/backend'); import config; print(config.NIM_EMBED_URL)")
    RERANK_URL=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$SCRIPT_DIR/backend'); import config; print(config.NIM_RERANK_URL)")
else
    EMBED_URL="${AUC_NIM_EMBED_URL:-http://localhost:8001}"
    RERANK_URL="${AUC_NIM_RERANK_URL:-http://localhost:8002}"
fi

# ---- Can this machine run them at all? ----
if ! command -v docker &> /dev/null; then
    echo "  ✗ Docker is not installed. The Nemotron engine runs in containers."
    exit 1
fi
if ! docker info &> /dev/null; then
    echo "  ✗ Docker is installed, but this user cannot use it."
    echo "    Add yourself to the docker group, then log out and back in:"
    echo "      sudo usermod -aG docker $USER"
    exit 1
fi
if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
    echo "  ✗ Docker has no NVIDIA runtime, so the containers could not reach the GPU."
    echo "    Install the NVIDIA Container Toolkit (it comes preinstalled on DGX OS)."
    exit 1
fi
if ! docker compose version &> /dev/null; then
    echo "  ✗ 'docker compose' is not available. Install it with:"
    echo "      sudo apt install docker-compose-plugin"
    exit 1
fi

# ---- Start them ----
NIM_UID="$(id -u)"
NIM_CACHE_DIR="${NIM_CACHE_DIR:-$HOME/.cache/nim}"
export NIM_UID NIM_CACHE_DIR
for service in nemotron-embed nemotron-rerank; do
    mkdir -p "$NIM_CACHE_DIR/$service/cache" "$NIM_CACHE_DIR/$service/weights"
done

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# Must match nim/docker-compose.yml, which explains the two version numbers.
EMBED_IMAGE="nvcr.io/nim/nvidia/nemotron-3-embed-1b:2.2.2"
RERANK_IMAGE="nvcr.io/nim/nvidia/llama-nemotron-rerank-vl-1b-v2:2.3"
images_cached() {
    docker image inspect "$EMBED_IMAGE" >/dev/null 2>&1 \
        && docker image inspect "$RERANK_IMAGE" >/dev/null 2>&1
}

if [ -z "${NGC_API_KEY:-}" ]; then
    echo "  · NGC_API_KEY is not set. Fine once the model weights are downloaded;"
    echo "    on a first start the download may fail without one."
fi

echo "  · Fetching the Nemotron images (several GB the first time)..."
if ! compose pull; then
    if images_cached; then
        echo "  · Could not refresh the images; the copies already on this machine will be used."
    else
        if [ ! -t 0 ]; then
            echo "  ✗ Could not download the images. If this is a login problem, run:"
            echo "      docker login nvcr.io"
            echo "    Use a Personal Key with NGC Catalog ticked, and open the two Nemotron"
            echo "    container pages on catalog.ngc.nvidia.com once if NVIDIA asks you to"
            echo "    accept the terms."
            exit 1
        fi
        echo ""
        echo "  The images are on NVIDIA's registry, which needs a one-time login."
        echo "  The username is literally \$oauthtoken and the password is your NGC"
        echo "  API key (ngc.nvidia.com → your account → Setup → Generate API Key,"
        echo "  with NGC Catalog ticked)."
        echo ""
        # shellcheck disable=SC2016  # $oauthtoken is the literal username, not a variable
        if ! { docker login nvcr.io --username '$oauthtoken' && compose pull; }; then
            echo "  ✗ Could not download the images."
            echo "    Generate a Personal Key with NGC Catalog ticked, and open the two"
            echo "    Nemotron container pages on catalog.ngc.nvidia.com once if NVIDIA"
            echo "    asks you to accept the terms."
            exit 1
        fi
    fi
fi

if ! compose up -d; then
    echo "  ✗ The containers did not start."
    exit 1
fi

# ---- Wait until both answer ----
echo "  · Waiting for both to be ready. The first start downloads the model"
echo "    weights, which can take a while."

ready() { curl -sf --max-time 3 "$1/v1/health/ready" > /dev/null 2>&1; }

START=$(date +%s)
DEADLINE=$((START + WAIT_MINUTES * 60))
LAST_REPORT=$START
while :; do
    if ready "$EMBED_URL" && ready "$RERANK_URL"; then
        echo "  ✓ Nemotron containers ready: $EMBED_URL (embedding), $RERANK_URL (reranking)"
        exit 0
    fi

    NOW=$(date +%s)
    # A container that has died will not come good by waiting; say so now.
    if [ -n "$(compose ps --status exited --status restarting --quiet 2>/dev/null)" ]; then
        echo "  ✗ A Nemotron container stopped. Its last words:"
        compose logs --tail 30
        exit 1
    fi
    if [ "$NOW" -ge "$DEADLINE" ]; then
        echo "  ✗ Not ready after $WAIT_MINUTES minutes. The latest from the containers:"
        compose logs --tail 20
        echo ""
        echo "    They may still be downloading. Watch them with:"
        echo "      docker compose -f $COMPOSE_FILE logs -f"
        exit 1
    fi
    if [ $((NOW - LAST_REPORT)) -ge 60 ]; then
        echo "    still waiting ($(( (NOW - START) / 60 )) min)..."
        LAST_REPORT=$NOW
    fi
    sleep 5
done
