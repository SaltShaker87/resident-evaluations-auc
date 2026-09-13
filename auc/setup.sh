#!/bin/bash
# ============================================================
# AUC — Assessments Under Curve
# One-time setup script
# ============================================================

set -e

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   AUC — Assessments Under Curve      ║"
echo "  ║   Setup Script                        ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# Get the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Installing a unit file without throwing away your edits
#
# OLLAMA_MODEL, AUC_BACKUP_DIR and the backup schedule live in the unit files
# and nowhere else, so re-running this script used to silently revert them.
# install_unit carries forward every Environment= line and every scheduling key
# (OnCalendar and friends) from the existing file — your value wins over the
# generated default — while regenerating everything else, since the paths have
# to change on a new machine. The previous file is kept alongside.
#
# The schedule matters on a desktop that is switched off overnight: a 02:00
# OnCalendar never fires, and Persistent=true runs it at the next boot instead.
#
# The merge is done in Python because it is fiddly enough that shell string
# surgery would be the wrong tool, and step 1 has already proved Python works.
#
#   install_unit <path> <generated-content>
# ---------------------------------------------------------------------------
install_unit() {
    local path="$1" generated="$2"
    local name; name="$(basename "$path")"

    if [ ! -f "$path" ]; then
        printf '%s' "$generated" > "$path"
        echo "  ✓ $name created"
        return 0
    fi

    local merged
    merged="$(GENERATED="$generated" EXISTING_PATH="$path" python3 - <<'PY'
import os
import re

generated = os.environ["GENERATED"]
existing = open(os.environ["EXISTING_PATH"], encoding="utf-8").read()

ENV = re.compile(r"^Environment=([^=]+)=(.*)$")

# Settings that exist only in the unit file, so regenerating them silently
# discards a deliberate choice. Environment= holds OLLAMA_MODEL and
# AUC_BACKUP_DIR; the scheduling keys hold when the backup runs, which is worth
# changing on a machine that is switched off overnight.
SCHEDULE_KEYS = (
    "OnCalendar", "OnBootSec", "OnUnitActiveSec", "OnStartupSec",
    "RandomizedDelaySec", "AccuracySec", "Persistent",
)


def env_map(text):
    out = {}
    for line in text.splitlines():
        m = ENV.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def schedule_map(text):
    """key -> every line for it, since OnCalendar may legitimately repeat."""
    out = {}
    for line in text.splitlines():
        for key in SCHEDULE_KEYS:
            if line.startswith(f"{key}="):
                out.setdefault(key, []).append(line)
    return out


mine, theirs = env_map(generated), env_map(existing)
my_schedule, their_schedule = schedule_map(generated), schedule_map(existing)

out, kept, emitted = [], [], set()
for line in generated.splitlines():
    m = ENV.match(line)
    if m and m.group(1) in theirs and theirs[m.group(1)] != m.group(2):
        line = f"Environment={m.group(1)}={theirs[m.group(1)]}"
        kept.append(line)
        out.append(line)
        continue

    scheduled = next((k for k in SCHEDULE_KEYS if line.startswith(f"{k}=")), None)
    if scheduled:
        if scheduled in emitted:
            continue  # their lines were already written in place of ours
        emitted.add(scheduled)
        if their_schedule.get(scheduled, []) != my_schedule.get(scheduled, []):
            out.extend(their_schedule[scheduled])
            kept.extend(their_schedule[scheduled])
            continue
    out.append(line)

# Scheduling keys you added that this script does not generate at all — e.g.
# an OnBootSec= to catch up after a machine that was switched off.
extra_schedule = []
for key, lines in their_schedule.items():
    if key not in my_schedule:
        extra_schedule.extend(lines)
if extra_schedule:
    kept.extend(extra_schedule)
    anchor = next(
        (i for i, line in enumerate(out)
         if any(line.startswith(f"{k}=") for k in SCHEDULE_KEYS)),
        None,
    )
    if anchor is None:
        anchor = next((i for i, line in enumerate(out) if line.strip() == "[Install]"), len(out))
        while anchor > 0 and not out[anchor - 1].strip():
            anchor -= 1
    out[anchor:anchor] = extra_schedule

# Environment= lines you added that this script does not generate at all.
extra = [f"Environment={k}={v}" for k, v in theirs.items() if k not in mine]
if extra:
    kept.extend(extra)
    insert = next((i for i, l in enumerate(out) if l.strip() == "[Install]"), len(out))
    while insert > 0 and not out[insert - 1].strip():
        insert -= 1
    out[insert:insert] = extra

print("\n".join(out))
if kept:
    print("__KEPT__")
    print("\n".join(kept))
PY
)"

    local kept=""
    if [[ "$merged" == *$'\n__KEPT__\n'* ]]; then
        kept="${merged#*$'\n__KEPT__\n'}"
        merged="${merged%%$'\n__KEPT__\n'*}"
    fi

    if [ "$merged" = "$(cat "$path")" ]; then
        echo "  · $name unchanged"
        return 0
    fi

    local backup
    backup="$path.bak-$(date +%Y%m%d-%H%M%S)"
    cp "$path" "$backup"
    printf '%s\n' "$merged" > "$path"
    echo "  ✓ $name updated (previous version kept as $(basename "$backup"))"
    if [ -n "$kept" ]; then
        echo "    Your settings were carried across, not reverted:"
        while IFS= read -r kept_line; do
            [ -n "$kept_line" ] && echo "      $kept_line"
        done <<< "$kept"
    fi
}

# ---- Step 1: Check prerequisites ----
echo "[1/7] Checking prerequisites..."

echo "  · Processor family: $(uname -m)"

# A DGX Spark (or another maker's GB10 machine) gets the NVIDIA Nemotron
# retrieval engine by default. Same rule as backend/retrieval_engine.py: the
# GPU is a GB10. Asked here directly because the Python environment the app
# would ask with does not exist yet.
IS_SPARK=0
if command -v nvidia-smi &> /dev/null \
        && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -q GB10; then
    IS_SPARK=1
    echo "  · DGX Spark detected (NVIDIA GB10): the NVIDIA Nemotron retrieval engine will be set up"
fi

if ! command -v python3 &> /dev/null; then
    echo "  ✗ Python 3 not found. Please install Python 3.10 or newer."
    exit 1
fi

# Check the version here rather than letting it fail deep inside the chromadb
# install, where the error is about onnxruntime and gives no hint that the real
# problem is the Python version.
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info[1])')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "  ✗ Python $(python3 -c 'import platform; print(platform.python_version())') is too old."
    echo ""
    echo "    3.10 is the floor and 3.11 or newer is what you want. Below 3.10"
    echo "    the ACGME index layer cannot install at all: onnxruntime publishes"
    echo "    no ARM build for older Pythons and there is no source fallback."
    echo ""
    echo "    Install a newer Python, then run this script again."
    exit 1
fi
if [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -eq 10 ]; then
    echo "  ⚠ Python $(python3 -c 'import platform; print(platform.python_version())') works, but 3.11+ is the comfortable floor."
else
    echo "  ✓ Python $(python3 -c 'import platform; print(platform.python_version())')"
fi

if ! command -v node &> /dev/null; then
    echo "  ✗ Node.js not found. Please install Node.js 18+."
    echo "    You can install it with: sudo apt install nodejs npm"
    exit 1
fi
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
if [ "$NODE_MAJOR" -lt 18 ]; then
    echo "  ✗ Node.js $(node --version) is too old — 18 or newer is required to build the interface."
    exit 1
fi
echo "  ✓ Node.js $(node --version)"

if ! command -v npm &> /dev/null; then
    echo "  ✗ npm not found. Install it with: sudo apt install npm"
    exit 1
fi

# The Python environment and the browser packages together come to roughly two
# gigabytes. Finding that out after a twenty-minute download is no fun.
AVAIL_KB=$(df -Pk "$SCRIPT_DIR" | awk 'NR==2 {print $4}')
AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
if [ "$AVAIL_KB" -lt 1048576 ]; then
    echo "  ✗ Only ${AVAIL_GB} GB free on this disk. Setup needs about 3 GB."
    exit 1
elif [ "$AVAIL_KB" -lt 3145728 ]; then
    echo "  ⚠ Only ${AVAIL_GB} GB free. Setup needs about 3 GB; this may not finish."
else
    echo "  ✓ Disk space: ${AVAIL_GB} GB free"
fi

if ! command -v ollama &> /dev/null; then
    echo "  ⚠ Ollama not found. The AI summary feature won't work until you install it."
    echo "    Install from: https://ollama.ai"
else
    echo "  ✓ Ollama found"
fi

# ---- Step 2: Set up Python backend ----
echo ""
echo "[2/7] Setting up Python backend..."

cd "$SCRIPT_DIR/backend"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  ✓ Created Python virtual environment"
fi

# Install dependencies in two stages.
#
# Stage 1 is the core application: pure Python, installs anywhere, and the app
# cannot run without it — a failure here is fatal and set -e stops the script.
#
# Stage 2 is the ACGME index layer (chromadb). It is the only package with
# compiled components, so it is the only one that can plausibly fail on an
# unfamiliar processor. A failure here costs you summary generation and nothing
# else, so it is reported as a warning and the install carries on.
# shellcheck source=/dev/null
source venv/bin/activate

pip install -q -r requirements.txt
echo "  ✓ Core Python dependencies installed"

RAG_INSTALLED=1
if pip install -q -r requirements-rag.txt; then
    echo "  ✓ ACGME index layer installed (chromadb)"
else
    RAG_INSTALLED=0
    echo ""
    echo "  ⚠ The ACGME index layer (chromadb) failed to install."
    echo ""
    echo "    The rest of the app is unaffected: residents, notes, follow-ups,"
    echo "    the CCC drawer and PDF export of approved summaries all work."
    echo ""
    echo "    What you lose is SUMMARY GENERATION. It will return a clear error"
    echo "    rather than a worse summary — there is no fallback."
    echo ""
    echo "    Most likely cause: Python is older than 3.10, which is below the"
    echo "    floor for onnxruntime's ARM builds. Yours is $(python3 --version)."
    echo "    To retry on its own:"
    echo "      cd $SCRIPT_DIR/backend && ./venv/bin/pip install -r requirements-rag.txt"
    echo ""
fi

deactivate

# ---- Step 3: Build frontend ----
echo ""
echo "[3/7] Building frontend..."

cd "$SCRIPT_DIR/frontend"

# Errors are not hidden. On an unfamiliar processor, npm complaining is the
# most useful thing it can do; the old `--silent 2>/dev/null` threw that away.
#
# npm ci is used where there is a lockfile: it installs exactly the pinned
# versions and clears node_modules first, so a directory carried over from an
# Intel machine cannot leave stale binaries behind.
if [ -f package-lock.json ]; then
    npm ci --no-audit --no-fund
else
    npm install --no-audit --no-fund
fi
npm run build
echo "  ✓ Frontend built"

# ---- Step 4: Create data directory ----
echo ""
echo "[4/7] Setting up data directory..."

mkdir -p "$SCRIPT_DIR/data/photos"
echo "  ✓ Data directory ready"

# ---- Step 5: NVIDIA Nemotron retrieval (DGX Spark only) ----
echo ""
echo "[5/7] NVIDIA Nemotron retrieval engine..."

# On a Spark this is the default engine, so setup starts its two containers.
# Anywhere else it is skipped: Standard (Ollama) is the default there, and
# Nemotron can still be added later on any machine with a capable NVIDIA GPU
# by running start-nemotron.sh. Nothing here is fatal. If it fails, the app
# still installs, Settings shows Nemotron as unavailable, and Standard can be
# chosen there.
NEMOTRON_READY=0
if [ "$IS_SPARK" -eq 0 ]; then
    echo "  · Skipped: not a DGX Spark, so the Standard (Ollama) engine is the default."
elif bash "$SCRIPT_DIR/start-nemotron.sh"; then
    NEMOTRON_READY=1
else
    echo ""
    echo "  ⚠ The Nemotron containers are not running."
    echo ""
    echo "    The app still installs. Until they run, Settings shows NVIDIA"
    echo "    Nemotron as unavailable and summaries fail with a message saying so."
    echo "    Choose Standard (Ollama) in Settings to use this machine meanwhile."
    echo ""
    echo "    To retry on its own, then build its index:"
    echo "      bash $SCRIPT_DIR/start-nemotron.sh"
    echo "      $SCRIPT_DIR/backend/venv/bin/python $SCRIPT_DIR/rag/build_index.py"
    echo ""
fi

# ---- Step 6: Build the ACGME reference index ----
echo ""
echo "[6/7] Building the ACGME reference index..."

# One index per retrieval engine this machine can run right now.
# build_index.py checks each engine itself and skips, with the reason, any it
# cannot reach, most often because the embedding model has not been pulled
# into Ollama yet. Not fatal: the rest of the app works without it, and
# Settings says what is missing.
if [ "$RAG_INSTALLED" -eq 0 ]; then
    echo "  · Skipped: the ACGME index layer (chromadb) did not install. See step 2."
elif "$SCRIPT_DIR/backend/venv/bin/python" "$SCRIPT_DIR/rag/build_index.py"; then
    echo "  ✓ ACGME index built"
else
    echo ""
    echo "  ⚠ The ACGME index was not built for every engine (the reasons are above)."
    echo "    Summary generation needs it. Once the cause is fixed, run:"
    echo "      $SCRIPT_DIR/backend/venv/bin/python $SCRIPT_DIR/rag/build_index.py"
    echo ""
fi

# ---- Step 7: Create startup script and systemd service ----
echo ""
echo "[7/7] Configuring auto-start..."

# Everything above this point has worked. If this machine has no user systemd
# session — a container, or SSH to a host without lingering — say so and stop
# here rather than failing in a way that looks like the install broke.
if ! systemctl --user show-environment &> /dev/null; then
    echo "  ⚠ No systemd user session here, so auto-start cannot be configured."
    echo ""
    echo "    The app itself is installed and ready. Start it by hand with:"
    echo "      bash $SCRIPT_DIR/run.sh"
    echo ""
    echo "    To get auto-start, run this script again from a normal login"
    echo "    session on the machine."
    exit 0
fi

# Create the run script
cat > "$SCRIPT_DIR/run.sh" << 'RUNEOF'
#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"
# shellcheck source=/dev/null
source venv/bin/activate

# Where to listen comes from backend/config.py, which honours AUC_HOST and
# AUC_PORT from the environment. Reading it here rather than repeating the
# defaults means there is one place to change them.
eval "$(python - <<'PYCFG'
import shlex

import config

print(f"AUC_HOST={shlex.quote(config.HOST)}")
print(f"AUC_PORT={shlex.quote(str(config.PORT))}")
PYCFG
)"

exec uvicorn app:app --host "$AUC_HOST" --port "$AUC_PORT"
RUNEOF
chmod +x "$SCRIPT_DIR/run.sh"
echo "  ✓ Created run.sh"

# Ask config.py where the app will actually listen, so what we print below is
# what will happen rather than a guess.
EFFECTIVE_HOST=$("$SCRIPT_DIR/backend/venv/bin/python" -c "import sys; sys.path.insert(0, '$SCRIPT_DIR/backend'); import config; print(config.HOST)")
EFFECTIVE_PORT=$("$SCRIPT_DIR/backend/venv/bin/python" -c "import sys; sys.path.insert(0, '$SCRIPT_DIR/backend'); import config; print(config.PORT)")

# Create systemd service
SERVICE_FILE="$HOME/.config/systemd/user/auc.service"
mkdir -p "$(dirname "$SERVICE_FILE")"

install_unit "$SERVICE_FILE" "$(cat << EOF
[Unit]
Description=AUC — Assessments Under Curve
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR/backend
ExecStart=$SCRIPT_DIR/run.sh
Restart=on-failure
RestartSec=5
Environment=OLLAMA_URL=http://localhost:11434
Environment=OLLAMA_MODEL=qwen3:8b

[Install]
WantedBy=default.target
EOF
)"

# Turn on lingering. Without it, user services only run while someone is
# logged in at the console — which, on a headless machine reached over SSH,
# is never. This is the single most common headless surprise, and SECURITY.md
# already flags it as needed.
if command -v loginctl &> /dev/null; then
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then
        echo "  · Lingering already enabled"
    elif loginctl enable-linger "$USER" 2>/dev/null; then
        echo "  ✓ Lingering enabled — services will start at boot without you logging in"
    else
        echo "  ⚠ Could not enable lingering. Run this yourself:"
        echo "      sudo loginctl enable-linger $USER"
        echo "    Without it, the app only starts when someone logs in at the console."
    fi
fi

# Enable the service
systemctl --user daemon-reload
systemctl --user enable auc.service
systemctl --user restart auc.service

echo "  ✓ Auto-start configured"

# ---- Automated daily backup ----
echo ""
echo "[7b/7] Configuring automated daily backup..."

# Default backup destination. Point AUC_BACKUP_DIR at a synced folder
# (e.g. your institutional OneDrive) so backups end up safely off this
# machine. See auc/BACKUPS.md for the OneDrive-on-Linux setup.
BACKUP_DIR="${AUC_BACKUP_DIR:-$HOME/auc-backups}"
mkdir -p "$BACKUP_DIR"

install_unit "$HOME/.config/systemd/user/auc-backup.service" "$(cat << EOF
[Unit]
Description=AUC daily backup (database + photos)

[Service]
Type=oneshot
WorkingDirectory=$SCRIPT_DIR/backend
ExecStart=$SCRIPT_DIR/backend/venv/bin/python backup.py
Environment=AUC_BACKUP_DIR=$BACKUP_DIR
Environment=AUC_BACKUP_KEEP_DAYS=14
EOF
)"

install_unit "$HOME/.config/systemd/user/auc-backup.timer" "$(cat << EOF
[Unit]
Description=Run AUC backup once a day

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
)"

systemctl --user daemon-reload
systemctl --user enable auc-backup.timer
systemctl --user start auc-backup.timer

echo "  ✓ Daily backup configured (writing to: $BACKUP_DIR)"
echo "    To send backups offsite, point them at OneDrive — see auc/BACKUPS.md"

echo ""
if [ "$RAG_INSTALLED" -eq 0 ]; then
    echo "  ⚠ Setup finished, but WITHOUT the ACGME index layer."
    echo "    Summary generation will not work until chromadb installs."
    echo "    Everything else is ready. See the warning in step 2 above."
    echo ""
fi
if [ "$IS_SPARK" -eq 1 ] && [ "$NEMOTRON_READY" -eq 0 ]; then
    echo "  ⚠ Setup finished, but the NVIDIA Nemotron containers are not running."
    echo "    This machine defaults to Nemotron, so summaries will not work until"
    echo "    they do, or until Standard (Ollama) is chosen in Settings."
    echo "    See the warning in step 5 above."
    echo ""
fi
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Setup complete!                     ║"
echo "  ║                                       ║"
echo "  ║   It will start automatically         ║"
echo "  ║   when your machine boots.            ║"
echo "  ║                                       ║"
echo "  ║   To stop:  systemctl --user stop auc ║"
echo "  ║   To start: systemctl --user start auc║"
echo "  ║   Logs:     journalctl --user -u auc  ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
echo "  AUC is now running at: http://localhost:$EFFECTIVE_PORT"
if [ "$EFFECTIVE_HOST" = "0.0.0.0" ]; then
    echo "  Listening on every network interface, over plain HTTP."
else
    echo "  Listening on $EFFECTIVE_HOST only."
fi
echo ""
