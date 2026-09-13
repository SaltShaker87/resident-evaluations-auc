#!/bin/bash
# ============================================================
# AUC — preflight
#
# Checks every environmental assumption the app makes and prints one
# pass/warn/fail line for each, with what to do about the failures.
#
#   bash auc/preflight.sh
#
# It only reads. Nothing here changes anything, so it is safe to run at any
# time — including while the app is serving a committee meeting.
#
# Run it after setup on a new machine, and whenever something stops working.
# "Run preflight" is a better first instruction than "read the manual again".
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$SCRIPT_DIR/backend"
VENV_PY="$BACKEND/venv/bin/python"

PASS=0
WARN=0
FAIL=0

if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_OK=""; C_WARN=""; C_BAD=""; C_DIM=""; C_OFF=""
fi

pass() { PASS=$((PASS + 1)); printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$1"; }
info() { printf '  %s·%s %s\n' "$C_DIM" "$C_OFF" "$1"; }

# warn/fail take a message and an optional "what to do" line.
warn() {
    WARN=$((WARN + 1))
    printf '  %s⚠%s %s\n' "$C_WARN" "$C_OFF" "$1"
    [ -n "$2" ] && printf '      %s→ %s%s\n' "$C_DIM" "$2" "$C_OFF"
    return 0
}
fail() {
    FAIL=$((FAIL + 1))
    printf '  %s✗%s %s\n' "$C_BAD" "$C_OFF" "$1"
    [ -n "$2" ] && printf '      %s→ %s%s\n' "$C_DIM" "$2" "$C_OFF"
    return 0
}

section() { printf '\n%s\n' "$1"; }

echo ""
echo "  AUC preflight — $(date '+%Y-%m-%d %H:%M')"
echo "  $SCRIPT_DIR"

# ---------------------------------------------------------------------------
section "Machine"
# ---------------------------------------------------------------------------

info "Processor family: $(uname -m)"
info "Kernel: $(uname -sr)"

if command -v free &> /dev/null; then
    info "Memory: $(free -h | awk 'NR==2 {print $7 " available of " $2}')"
fi

AVAIL_KB=$(df -Pk "$SCRIPT_DIR" | awk 'NR==2 {print $4}')
AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
if [ "$AVAIL_KB" -lt 1048576 ]; then
    fail "Disk space: ${AVAIL_GB} GB free" "Free some space — backups and model downloads need room."
else
    pass "Disk space: ${AVAIL_GB} GB free"
fi

if command -v python3 &> /dev/null; then
    PY_MINOR=$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)
    PY_VER=$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)
    if [ "$PY_MINOR" -lt 10 ]; then
        fail "Python $PY_VER — too old" "3.10 is the floor; below it the ACGME index layer cannot install."
    elif [ "$PY_MINOR" -eq 10 ]; then
        warn "Python $PY_VER — works, but 3.11+ is the comfortable floor" ""
    else
        pass "Python $PY_VER"
    fi
else
    fail "Python 3 not found" "Install Python 3.11 or newer."
fi

if command -v node &> /dev/null; then
    NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
    if [ "$NODE_MAJOR" -lt 18 ]; then
        fail "Node.js $(node --version) — too old" "18 or newer is needed to rebuild the interface."
    else
        pass "Node.js $(node --version)"
    fi
else
    warn "Node.js not found" "Only needed to rebuild the interface, not to run it."
fi

# ---------------------------------------------------------------------------
section "Application"
# ---------------------------------------------------------------------------

if [ ! -x "$VENV_PY" ]; then
    fail "Python environment missing at backend/venv" "Run: bash $SCRIPT_DIR/setup.sh"
else
    pass "Python environment present ($("$VENV_PY" -c 'import platform; print(platform.python_version())'))"

    # Installing and importing are different things. On an unfamiliar processor
    # a package can install and then fail to load a compiled library, which is
    # exactly the failure this is here to catch.
    for mod in fastapi uvicorn httpx fpdf anyio multipart; do
        if out=$("$VENV_PY" -c "import $mod" 2>&1); then
            pass "imports: $mod"
        else
            fail "cannot import $mod" "$(echo "$out" | tail -1)"
        fi
    done

    if out=$("$VENV_PY" -c "import chromadb; print(chromadb.__version__)" 2>&1); then
        pass "imports: chromadb $out"
    else
        fail "cannot import chromadb — summary generation will not work" \
             "Install it: $BACKEND/venv/bin/pip install -r $BACKEND/requirements-rag.txt"
        echo "      $(echo "$out" | tail -1)"
    fi
fi

if [ -f "$SCRIPT_DIR/frontend/dist/index.html" ]; then
    pass "Interface built (frontend/dist)"
    FONT_COUNT=$(find "$SCRIPT_DIR/frontend/dist/fonts" -name '*.woff2' 2>/dev/null | wc -l)
    if [ "$FONT_COUNT" -ge 8 ]; then
        pass "Typefaces bundled locally ($FONT_COUNT files — no outside request on page load)"
    else
        warn "Only $FONT_COUNT bundled font files in dist" "Rebuild: cd $SCRIPT_DIR/frontend && npm run build"
    fi
else
    fail "Interface not built" "Run: cd $SCRIPT_DIR/frontend && npm ci && npm run build"
fi

# ---------------------------------------------------------------------------
section "Data"
# ---------------------------------------------------------------------------

if [ -x "$VENV_PY" ]; then
    DATA_DIR=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$BACKEND'); import config; print(config.DATA_DIR)" 2>/dev/null)
else
    DATA_DIR="$SCRIPT_DIR/data"
fi
info "Data directory: $DATA_DIR"

DB="$DATA_DIR/auc.db"
if [ ! -f "$DB" ]; then
    warn "No database yet at $DB" "Normal on a fresh install — the app creates one and asks you to set a password."
elif [ ! -x "$VENV_PY" ]; then
    warn "Database present but cannot be inspected without the Python environment" "Run: bash $SCRIPT_DIR/setup.sh"
else
    # Read it with Python's own sqlite3 rather than the sqlite3 command, which
    # is not installed by default on a fresh machine. Read-only: the database
    # is opened in immutable mode so this cannot disturb a running app.
    DBINFO=$(DB_PATH="$DB" PHOTOS_DIR="$DATA_DIR/photos" "$VENV_PY" - <<'PYDB'
import os
import sqlite3
from pathlib import Path

db = os.environ["DB_PATH"]
try:
    # immutable=1 opens it read-only and without locking, so this cannot
    # disturb the app even mid-write.
    conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True)

    def one(query):
        return conn.execute(query).fetchone()[0]

    photos = Path(os.environ["PHOTOS_DIR"])
    values = [
        one("select count(*) from residents"),
        one("select count(*) from notes"),
        one("select count(*) from summaries"),
        one("select count(*) from auth_config"),
        one("select count(*) from residents "
            "where photo_filename is not null and photo_filename != ''"),
        sum(1 for f in photos.iterdir() if f.is_file()) if photos.is_dir() else 0,
    ]
except Exception as e:
    # Nothing is printed until every query has succeeded, so a partial read
    # cannot be mistaken for a good one.
    print("error")
    print(e)
else:
    print("ok")
    print("\n".join(str(v) for v in values))
PYDB
)
    if [ "$(echo "$DBINFO" | head -1)" != "ok" ]; then
        fail "Database is unreadable: $(echo "$DBINFO" | tail -n +2 | head -1)" \
             "Restore from a backup — see auc/BACKUPS.md."
    else
        RESIDENTS=$(echo "$DBINFO" | sed -n '2p')
        NOTES=$(echo "$DBINFO" | sed -n '3p')
        SUMMARIES=$(echo "$DBINFO" | sed -n '4p')
        HAS_PW=$(echo "$DBINFO" | sed -n '5p')
        CLAIMED=$(echo "$DBINFO" | sed -n '6p')
        ON_DISK=$(echo "$DBINFO" | sed -n '7p')

        pass "Database readable — $RESIDENTS residents, $NOTES notes, $SUMMARIES summaries"

        if [ "$HAS_PW" -ge 1 ]; then
            pass "Password configured"
        else
            warn "No password configured" "Open the app; it will ask you to create one. Keep the recovery key."
        fi

        # A row claiming a photo whose file is missing renders as a broken
        # image — the sign that the database copied across but photos/ did not.
        if [ "$CLAIMED" -gt "$ON_DISK" ]; then
            fail "$CLAIMED residents have a photo recorded but only $ON_DISK files exist" \
                 "Photos did not all copy across. Restore data/photos from a backup."
        else
            pass "Photos: $ON_DISK files on disk, $CLAIMED residents reference one"
        fi
    fi
fi

if mkdir -p "$DATA_DIR/logs" 2>/dev/null && [ -w "$DATA_DIR/logs" ]; then
    pass "Log directory writable (the summary validator writes here)"
else
    fail "Cannot write to $DATA_DIR/logs" "Summary runs log their quote validation here. Fix the permissions."
fi

if [ -f "$BACKEND/fonts/DejaVuSans.ttf" ]; then
    pass "DejaVu fonts bundled with the app (PDF export renders dashes and curly quotes)"
elif [ -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]; then
    pass "DejaVu fonts installed on the system (PDF export renders dashes and curly quotes)"
else
    warn "DejaVu fonts not found" "sudo apt install fonts-dejavu-core — without it PDFs fall back to a basic font."
fi

# ---------------------------------------------------------------------------
section "Retrieval engine, Ollama and the ACGME index"
# ---------------------------------------------------------------------------

if [ -x "$VENV_PY" ]; then
    OLLAMA_URL=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$BACKEND'); import config; print(config.OLLAMA_URL)" 2>/dev/null)
    EMBED_MODEL=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$BACKEND'); import config; print(config.EMBED_MODEL)" 2>/dev/null)

    # The engine in force, by the app's own rule: the choice made in Settings,
    # or AUC_RETRIEVAL_ENGINE_DEFAULT, or the default for this hardware. The
    # database is opened immutable.
    ENGINE_INFO=$("$VENV_PY" -c "
import sys
sys.path.insert(0, '$BACKEND')
import config
import retrieval_engine as r
stored = r.stored_on_disk()
print(r.resolve(stored))
if stored in r.ENGINES:
    print('chosen in Settings')
elif config.RETRIEVAL_ENGINE_DEFAULT in r.ENGINES:
    print('the default set in the environment')
else:
    print('the default for this machine')
print('yes' if r.is_spark() else 'no')
" 2>/dev/null)
    ENGINE=$(echo "$ENGINE_INFO" | sed -n '1p')
    ENGINE_WHY=$(echo "$ENGINE_INFO" | sed -n '2p')
    IS_SPARK=$(echo "$ENGINE_INFO" | sed -n '3p')
else
    OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
    EMBED_MODEL="${AUC_EMBED_MODEL:-qwen3-embedding:0.6b}"
fi
ENGINE="${ENGINE:-ollama}"

if [ "$IS_SPARK" = "yes" ]; then
    info "DGX Spark detected (NVIDIA GB10): NVIDIA Nemotron is this machine's default engine"
fi
if [ "$ENGINE" = "nemotron" ]; then
    info "Retrieval engine: NVIDIA Nemotron (${ENGINE_WHY:-unknown})"
else
    info "Retrieval engine: Standard (Ollama) (${ENGINE_WHY:-assumed; no Python environment to ask})"
fi

# Ollama writes the summaries whichever engine does the retrieval, so it is
# needed either way. Only the embedding model belongs to one engine.

if MODELS=$(curl -sf --max-time 5 "$OLLAMA_URL/api/tags" 2>/dev/null); then
    NAMES=$(echo "$MODELS" | "$VENV_PY" -c "import json,sys; print('\n'.join(m['name'] for m in json.load(sys.stdin).get('models', [])))" 2>/dev/null)
    COUNT=$(echo "$NAMES" | grep -c . || true)
    if [ "${COUNT:-0}" -eq 0 ]; then
        fail "Ollama is running but has no models" "Pull one: ollama pull qwen3.5:4b"
    else
        pass "Ollama answering at $OLLAMA_URL — $COUNT models"

        # Any of these can write summaries; the app lets you pick per run in
        # Settings. So this only lists them rather than insisting on one.
        echo "$NAMES" | grep -v "^$EMBED_MODEL\$" | sed 's/^/      /'

        # The embedding model is the opposite: the name must match exactly,
        # because it must be the model the index was built with.
        if echo "$NAMES" | grep -qx "$EMBED_MODEL"; then
            pass "Embedding model present by exact name: $EMBED_MODEL"
        elif [ "$ENGINE" = "ollama" ]; then
            fail "Embedding model '$EMBED_MODEL' is not installed" "ollama pull $EMBED_MODEL"
        else
            info "Standard engine's embedding model ($EMBED_MODEL) is not installed; only needed to switch to Standard"
        fi
    fi
else
    fail "Ollama not answering at $OLLAMA_URL" "Start it (systemctl status ollama), or point OLLAMA_URL elsewhere."
fi

# The Nemotron containers matter when that engine is in force, and are worth
# a mention on a Spark, where it is the default.
if [ -x "$VENV_PY" ]; then
    NIM=$("$VENV_PY" -c "
import sys
sys.path.insert(0, '$BACKEND')
import retrieval_engine as r
a = r.availability('nemotron')
print('yes' if a['available'] else 'no')
print(a['reason'])
" 2>/dev/null)
    NIM_UP=$(echo "$NIM" | head -1)
    NIM_REASON=$(echo "$NIM" | tail -n +2)
    if [ "$NIM_UP" = "yes" ]; then
        pass "Nemotron containers answering (embedding and reranking)"
    elif [ "$ENGINE" = "nemotron" ]; then
        fail "Nemotron containers not answering — summary generation will not work" \
             "${NIM_REASON:-bash $SCRIPT_DIR/start-nemotron.sh} Or choose Standard (Ollama) in Settings."
    elif [ "$IS_SPARK" = "yes" ]; then
        info "Nemotron containers not running (Standard is in use). Start them: bash $SCRIPT_DIR/start-nemotron.sh"
    fi
fi

# The index check is the app's own, so preflight and Settings can never
# disagree about what healthy looks like. It is the index of the engine in
# force; each engine has its own.
if [ -x "$VENV_PY" ]; then
    STATUS=$("$VENV_PY" -c "
import sys
sys.path.insert(0, '$BACKEND')
import rag_retrieval
s = rag_retrieval.index_status('$ENGINE')
print(s['level'])
print(s['message'])
" 2>/dev/null)
    LEVEL=$(echo "$STATUS" | head -1)
    MESSAGE=$(echo "$STATUS" | tail -n +2)
    case "$LEVEL" in
        ok)      pass "$MESSAGE" ;;
        warning) warn "ACGME index" "$MESSAGE" ;;
        *)       fail "ACGME index — summary generation will not work" "${MESSAGE:-Could not check the index.}" ;;
    esac
fi

# ---------------------------------------------------------------------------
section "Service"
# ---------------------------------------------------------------------------

if ! command -v systemctl &> /dev/null || ! systemctl --user show-environment &> /dev/null; then
    warn "No systemd user session here, so the service cannot be checked" \
         "Run this from a normal login session on the machine."
else
    for unit in auc.service auc-backup.timer; do
        if systemctl --user is-enabled --quiet "$unit" 2>/dev/null; then
            pass "$unit enabled"
        else
            fail "$unit not enabled" "Run: bash $SCRIPT_DIR/setup.sh"
        fi
    done

    if systemctl --user is-active --quiet auc.service 2>/dev/null; then
        pass "auc.service running"
    else
        fail "auc.service not running" "systemctl --user status auc"
    fi

    NEXT=$(systemctl --user list-timers auc-backup.timer --no-pager --no-legend 2>/dev/null | awk '{print $1, $2, $3}')
    [ -n "$NEXT" ] && info "Next backup: $NEXT"

    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then
        pass "Lingering enabled — the app starts at boot without anyone logging in"
    else
        fail "Lingering is NOT enabled" "sudo loginctl enable-linger $USER — without it the app does not start at boot."
    fi
fi

if [ -x "$VENV_PY" ]; then
    PORT=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$BACKEND'); import config; print(config.PORT)" 2>/dev/null)
    HOST=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$BACKEND'); import config; print(config.HOST)" 2>/dev/null)
else
    PORT="${AUC_PORT:-3000}"
    HOST="${AUC_HOST:-0.0.0.0}"
fi

if curl -sf --max-time 5 "http://localhost:$PORT/api/auth/status" > /dev/null 2>&1; then
    pass "App answering on port $PORT"
else
    fail "App not answering on port $PORT" "journalctl --user -u auc -n 50"
fi

if [ "$HOST" = "0.0.0.0" ]; then
    info "Listening on every interface over plain HTTP. Fine on a trusted network."
    info "On hospital wifi, set AUC_HOST=127.0.0.1 and put Tailscale Serve in front."
else
    pass "Listening on $HOST only"
fi

# ---------------------------------------------------------------------------
plural() { [ "$1" -eq 1 ] && echo "$2" || echo "$3"; }
printf '\n  %d passed, %d %s, %d %s\n\n' \
    "$PASS" "$WARN" "$(plural "$WARN" warning warnings)" "$FAIL" "$(plural "$FAIL" failure failures)"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
