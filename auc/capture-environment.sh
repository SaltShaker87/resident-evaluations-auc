#!/bin/bash
# ============================================================
# AUC — capture this machine's configuration
#
#   bash auc/capture-environment.sh
#
# Section 3 of SPARK_MIGRATION.md is 33 questions about things that exist
# only as settings you made by hand — the kind that are easy to forget until
# the moment they are missing. This answers about 25 of them automatically
# and leaves the rest as blanks for you to fill in.
#
# Run it while the old machine is still in front of you. Everything here is
# read-only; nothing is changed, started or stopped.
#
# The result is written to your home directory, NOT into the repository, so
# it cannot be committed by accident. Values that look like credentials are
# redacted: API keys are reported as set or unset, never printed, and the
# password hashes in the database are never read at all.
#
# It does capture your Modelfiles. That is deliberate — the recipe for
# clinical-reasoning:latest exists in one place on Earth, and this puts a
# copy of it in a text file you can carry.
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$SCRIPT_DIR/backend"
VENV_PY="$BACKEND/venv/bin/python"
OUT="$HOME/auc-environment-$(date +%Y%m%d-%H%M%S).txt"

# $USER is not always set (a cron job, a bare container shell), and this
# script runs with set -u.
RUN_USER="${USER:-$(id -un)}"

# Anything that looks like a credential is masked before it reaches the file.
redact() {
    sed -E 's/((API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH)[A-Z_]*=)[^[:space:]]+/\1<redacted>/gI'
}

h() { printf '\n\n%s\n%s\n' "$1" "$(printf '=%.0s' $(seq 1 ${#1}))"; }
q() { printf '\n%s\n' "$1"; }
ask() { printf '\n%s\n  -> ______________________________________________\n' "$1"; }
run() {
    # run "<label>" <command...>  — label, then output or a plain "not found"
    local label="$1"; shift
    printf '\n%s\n' "$label"
    if command -v "$1" &> /dev/null; then
        "$@" 2>&1 | redact | sed 's/^/    /'
    else
        printf '    (%s is not installed)\n' "$1"
    fi
}
show() {
    local label="$1" path="$2"
    printf '\n%s\n' "$label"
    if [ -f "$path" ]; then
        printf '    %s (last modified %s)\n' "$path" "$(date -r "$path" '+%Y-%m-%d %H:%M' 2>/dev/null)"
        redact < "$path" | sed 's/^/    | /'
    else
        printf '    (no file at %s)\n' "$path"
    fi
}

{
printf 'AUC environment capture\n'
printf 'Machine : %s\n' "$(hostname)"
printf 'User    : %s\n' "$RUN_USER"
printf 'Date    : %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
printf 'Repo    : %s\n' "$SCRIPT_DIR"
printf '\nThis answers most of Section 3 of SPARK_MIGRATION.md. Lines ending in a\n'
printf 'blank are the ones only you can answer — fill them in before the Spark\n'
printf 'arrives, while this machine is still in front of you.\n'

h "The machine itself"
printf '\nProcessor family : %s\n' "$(uname -m)"
printf 'Kernel           : %s\n' "$(uname -sr)"
# shellcheck source=/dev/null
printf 'Distribution     : %s\n' "$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
printf 'Python           : %s\n' "$(python3 --version 2>&1)"
printf 'Node             : %s\n' "$(node --version 2>&1)"
run "Memory:" free -h
run "Disk:" df -h "$SCRIPT_DIR" "$HOME"

h "Q22 — Graphics hardware and CUDA"
run "nvidia-smi:" nvidia-smi

q "Q23 — Is the disk encrypted?"
if command -v lsblk &> /dev/null; then
    if lsblk -o FSTYPE 2>/dev/null | grep -q crypto_LUKS; then
        printf '    Yes — at least one LUKS-encrypted volume is present.\n'
    else
        printf '    No LUKS volume found. (This does not rule out other schemes.)\n'
    fi
    lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT 2>/dev/null | sed 's/^/    /'
fi

h "Ollama (Q1-Q5)"

q "Q1 — How is Ollama installed?"
if command -v ollama &> /dev/null; then
    printf '    Binary   : %s\n' "$(command -v ollama)"
    printf '    Version  : %s\n' "$(ollama --version 2>&1 | head -1)"
    if [ -f /etc/systemd/system/ollama.service ]; then
        printf '    Installed by the official install script (unit at /etc/systemd/system/ollama.service).\n'
    elif command -v dpkg &> /dev/null && dpkg -S "$(command -v ollama)" &> /dev/null; then
        printf '    Installed from a package: %s\n' "$(dpkg -S "$(command -v ollama)" 2>/dev/null)"
    elif command -v snap &> /dev/null && snap list ollama &> /dev/null; then
        printf '    Installed as a snap.\n'
    else
        printf '    Not a systemd install, a .deb or a snap — installed some other way.\n'
    fi
else
    printf '    Ollama is not on PATH for this user.\n'
fi

q "Q2 — System service, user service, or started by hand?"
if systemctl is-active ollama &> /dev/null; then
    printf '    System service, active.\n'
elif systemctl --user is-active ollama &> /dev/null; then
    printf '    User service, active.\n'
elif pgrep -x ollama > /dev/null; then
    printf '    Running, but not under systemd — started by hand.\n'
else
    printf '    Not currently running.\n'
fi
systemctl status ollama --no-pager -n 0 2>/dev/null | head -5 | sed 's/^/    /'

q "Q3 — Installed models (ollama list)"
if command -v ollama &> /dev/null; then
    ollama list 2>&1 | sed 's/^/    /'
else
    printf '    (ollama not installed)\n'
fi

q "Q4/Q5 — OLLAMA_HOST and OLLAMA_MODELS"
printf '    From this shell : OLLAMA_HOST=%s OLLAMA_MODELS=%s\n' \
    "${OLLAMA_HOST:-<unset>}" "${OLLAMA_MODELS:-<unset>}"
systemctl show ollama -p Environment 2>/dev/null | redact | sed 's/^/    From the unit   : /'
PROFILE_OLLAMA=$(grep -hE '^[[:space:]]*export[[:space:]]+OLLAMA_' \
    "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshrc" 2>/dev/null | redact)
if [ -n "$PROFILE_OLLAMA" ]; then
    printf '%s\n' "$PROFILE_OLLAMA" | sed 's/^/    From your shell profile: /'
fi
printf '    Model store size: %s\n' "$(du -sh "${OLLAMA_MODELS:-$HOME/.ollama}" 2>/dev/null | cut -f1)"

h "The custom fine-tune (Q6-Q8)"
printf '\nModelfiles for every installed model are captured below. These are the\n'
printf 'recipes. For a model you fine-tuned yourself this is the only written\n'
printf 'record of how it was made.\n'
if command -v ollama &> /dev/null; then
    while read -r model _; do
        [ -z "$model" ] && continue
        [ "$model" = "NAME" ] && continue
        printf '\n--- Modelfile: %s ---\n' "$model"
        ollama show --modelfile "$model" 2>&1 | sed 's/^/    /'
    done < <(ollama list 2>/dev/null)
fi

q "Q6/Q7 — Modelfiles and model weights found on this machine"
printf '    Anything below is a file the fine-tune was built from, or built with.\n'
printf '    These are the irreplaceable ones: Ollama can be reinstalled, a .gguf\n'
printf '    that exists nowhere else cannot.\n\n'

# Look in the obvious places rather than scanning the whole disk: the top of
# home, and the directories the usual fine-tuning toolchains create.
FT_FOUND=""
for dir in "$HOME" "$HOME/Documents" "$HOME/Downloads" "$HOME/work" \
           "$HOME/unsloth" "$HOME/llama.cpp" "$HOME/llama.cpp/models" "$HOME/hf-models"; do
    [ -d "$dir" ] || continue
    if [ "$dir" = "$HOME" ]; then depth=1; else depth=3; fi
    while IFS= read -r found; do
        [ -n "$found" ] || continue
        FT_FOUND+="$(printf '%8s  %s' "$(du -h "$found" 2>/dev/null | cut -f1)" "$found")"$'\n'
    done < <(find "$dir" -maxdepth "$depth" -type f \
                  \( -iname '*.gguf' -o -iname '*modelfile*' -o -iname 'adapter_model.*' \
                     -o -iname 'adapter_config.json' -o -iname '*.safetensors' \) \
                  2>/dev/null)
done

FT_FOUND=$(printf '%s' "$FT_FOUND" | sort -u -k2)
if [ -n "$FT_FOUND" ]; then
    printf '%s\n' "$FT_FOUND" | sed 's/^/    /'
else
    printf '    (nothing found in the usual places — answer the blank below)\n'
fi

# A Modelfile is a short text file and is the actual recipe, so capture what is
# in it rather than only where it is.
printf '\n    Contents of each Modelfile found:\n'
FT_MODELFILES=$(printf '%s\n' "$FT_FOUND" | awk 'NF {$1=""; sub(/^ +/, ""); print}' \
    | grep -i 'modelfile' | sort -u)
if [ -n "$FT_MODELFILES" ]; then
    while IFS= read -r mf; do
        [ -f "$mf" ] || continue
        printf '\n    --- %s ---\n' "$mf"
        redact < "$mf" 2>/dev/null | head -60 | sed 's/^/      | /'
    done <<< "$FT_MODELFILES"
else
    printf '      (none found)\n'
fi

ask "Q7 — If the file the fine-tune was built from is not listed above, where is it?"
ask "Q8 — Is any of that backed up anywhere other than this machine?"

h "The app's own service (Q9-Q12)"
show "Q9/Q12 — auc.service" "$HOME/.config/systemd/user/auc.service"
printf '\n    Any .bak files beside it are previous versions kept by setup.sh:\n'
BAKS=$(find "$HOME/.config/systemd/user" -maxdepth 1 -name '*.bak-*' 2>/dev/null | sort)
if [ -n "$BAKS" ]; then printf '%s\n' "$BAKS" | sed 's/^/      /'; else printf '      (none)\n'; fi

q "Q10 — Lingering"
printf '    %s\n' "$(loginctl show-user "$RUN_USER" -p Linger 2>/dev/null || echo 'Linger=<could not read>')"

q "Q11 — Where the repository is checked out"
printf '    %s\n' "$SCRIPT_DIR"
printf '    Branch: %s\n' "$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"
printf '    Commit: %s\n' "$(git -C "$SCRIPT_DIR" log -1 --format='%h %s' 2>/dev/null)"

h "Backups (Q13-Q16)"
show "Q13 — auc-backup.service" "$HOME/.config/systemd/user/auc-backup.service"
show "     auc-backup.timer" "$HOME/.config/systemd/user/auc-backup.timer"

q "Q15 — Timers, and when the backup last ran"
TIMERS=$(systemctl --user list-timers --all --no-pager 2>/dev/null)
if [ -n "$TIMERS" ]; then
    printf '%s\n' "$TIMERS" | sed 's/^/    /'
else
    printf '    (no systemd user session, or no timers)\n'
fi
printf '\n    Last backup run:\n'
BACKUP_LOG=$(journalctl --user -u auc-backup.service -n 5 --no-pager 2>/dev/null | redact)
if [ -n "$BACKUP_LOG" ]; then
    printf '%s\n' "$BACKUP_LOG" | sed 's/^/      /'
else
    printf '      (no journal entries — the backup has never run here)\n'
fi

q "Q14 — Is OneDrive or rclone actually set up?"
if [ -d "$HOME/.config/onedrive" ]; then
    printf '    OneDrive client configured (~/.config/onedrive exists).\n'
    printf '    Sync directory: %s\n' "$(grep -E '^sync_dir' "$HOME/.config/onedrive/config" 2>/dev/null | head -1)"
else
    printf '    No ~/.config/onedrive.\n'
fi
if command -v rclone &> /dev/null; then
    printf '    rclone remotes (names only — the config holds live tokens and is NOT read):\n'
    rclone listremotes 2>/dev/null | sed 's/^/      /' || true
else
    printf '    rclone not installed.\n'
fi

q "Backup archives actually on disk"
BACKUP_DIR=$(grep -hE '^Environment=AUC_BACKUP_DIR=' "$HOME/.config/systemd/user/auc-backup.service" 2>/dev/null | cut -d= -f3-)
BACKUP_DIR="${BACKUP_DIR:-$HOME/auc-backups}"
printf '    Backup directory: %s\n' "$BACKUP_DIR"
if [ -d "$BACKUP_DIR" ]; then
    printf '    Total size: %s\n' "$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)"
    printf '    Most recent (newest first):\n'
    find "$BACKUP_DIR" -maxdepth 1 -type f -printf '%TY-%Tm-%Td %TH:%TM  %10s  %f\n' 2>/dev/null \
        | sort -r | head -5 | sed 's/^/      /'
else
    printf '    That directory does not exist — no backups have been written there.\n'
fi

ask "Q16 — When did you last confirm a backup could be RESTORED, not just that a file appeared?"

h "Network and access (Q17-Q21)"

q "Q17 — Tailscale"
if command -v tailscale &> /dev/null; then
    tailscale status 2>&1 | head -20 | sed 's/^/    /'
    printf '    This machine: %s\n' "$(tailscale ip -4 2>/dev/null | head -1)"
else
    printf '    Tailscale is not installed on this machine.\n'
fi

q "Q18 — Firewall"
if command -v ufw &> /dev/null; then
    ufw status 2>&1 | sed 's/^/    /'
    printf '    (If that says permission denied, re-run as: sudo ufw status)\n'
else
    printf '    ufw not installed.\n'
fi

q "Q20/Q25 — What is listening, and on which addresses"
if command -v ss &> /dev/null; then
    LISTENING=$(ss -ltnp 2>/dev/null)
elif command -v netstat &> /dev/null; then
    LISTENING=$(netstat -ltnp 2>/dev/null)
else
    LISTENING=""
    printf '    (neither ss nor netstat is installed)\n'
fi
if [ -n "$LISTENING" ]; then
    printf '%s\n' "$LISTENING" | sed 's/^/    /'
elif command -v ss &> /dev/null || command -v netstat &> /dev/null; then
    printf '    (nothing listening, or not permitted to see it — try with sudo)\n'
fi

ask "Q19 — Does the hospital network require registering a device, or signing in through a web page?"
ask "Q20 — How do you reach the app today (localhost:3000, a hostname, a fixed address)?"
ask "Q21 — Does anyone other than you use the app? From what device?"

h "Other scheduled jobs (Q24)"
run "crontab:" crontab -l
printf '\nSystem timers:\n'
SYS_TIMERS=$(systemctl list-timers --all --no-pager 2>/dev/null | head -15)
if [ -n "$SYS_TIMERS" ]; then
    printf '%s\n' "$SYS_TIMERS" | sed 's/^/    /'
else
    printf '    (none, or no system systemd here)\n'
fi

h "Data (Q26-Q28)"

if [ -x "$VENV_PY" ]; then
    DATA_DIR=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$BACKEND'); import config; print(config.DATA_DIR)" 2>/dev/null)
else
    DATA_DIR="$SCRIPT_DIR/data"
fi
printf '\nData directory: %s\n' "$DATA_DIR"
printf 'Database      : %s\n' "$(du -h "$DATA_DIR/auc.db" 2>/dev/null | cut -f1 || echo 'not present')"
printf 'Photos (Q27)  : %s in %s files\n' \
    "$(du -sh "$DATA_DIR/photos" 2>/dev/null | cut -f1)" \
    "$(find "$DATA_DIR/photos" -type f 2>/dev/null | wc -l)"

q "Database contents (counts only — no names, notes or password hashes are read)"
if [ -x "$VENV_PY" ] && [ -f "$DATA_DIR/auc.db" ]; then
    DB_PATH="$DATA_DIR/auc.db" "$VENV_PY" - <<'PYDB' 2>&1 | sed 's/^/    /'
import os
import sqlite3

conn = sqlite3.connect(f"file:{os.environ['DB_PATH']}?immutable=1", uri=True)


def count(table):
    try:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]
    except Exception as e:
        return f"(no table: {e})"


for table in ("residents", "notes", "followups", "summaries", "medhub_evaluations",
              "ccc_sessions", "ccc_resident_logs", "ccc_contributions",
              "ccc_action_items"):
    print(f"{table:22s} {count(table)}")

# Whether a password exists, never the hash itself.
print(f"{'password configured':22s} "
      f"{'yes' if count('auth_config') else 'no'}")
print(f"{'active sessions':22s} {count('sessions')}")
PYDB
else
    printf '    (no database, or no Python environment to read it with)\n'
fi

q "Q26 — MedHub CSV exports found near the repo"
CSVS=$(find "$HOME" -maxdepth 2 -name '*.csv' -newermt '-2 years' 2>/dev/null | head -10)
if [ -n "$CSVS" ]; then
    printf '%s\n' "$CSVS" | sed 's/^/    /'
else
    printf '    (none found in the top two levels of your home directory)\n'
fi

ask "Q28 — Do you have the app password and recovery key written down somewhere you can reach on arrival day?"

h "The summary generator and the study (Q29-Q33)"

q "Q29 — Is AUC_SUMMARY_MODEL set anywhere?"
printf '    From this shell: AUC_SUMMARY_MODEL=%s\n' "${AUC_SUMMARY_MODEL:-<unset>}"
MODEL_SETTINGS=$(grep -hE 'AUC_SUMMARY_MODEL|OLLAMA_MODEL' \
    "$HOME/.config/systemd/user/auc.service" "$HOME/.bashrc" "$HOME/.profile" 2>/dev/null | redact)
if [ -n "$MODEL_SETTINGS" ]; then
    printf '%s\n' "$MODEL_SETTINGS" | sed 's/^/    /'
else
    printf '    (not set in the unit file or your shell profile)\n'
fi
printf '\n    Remember the precedence: the model picked in Settings is sent with every\n'
printf '    generation and wins; then AUC_SUMMARY_MODEL; then OLLAMA_MODEL.\n'

q "Q30/Q33 — The summary validation log"
LOG="$DATA_DIR/logs/summary_validation.log"
if [ -f "$LOG" ]; then
    printf '    %s — %s, last written %s\n' \
        "$LOG" "$(du -h "$LOG" | cut -f1)" "$(date -r "$LOG" '+%Y-%m-%d %H:%M')"
    printf '\n    Every run recorded, with the model each used:\n'
    grep -o 'RUN START model=[^ ]*' "$LOG" 2>/dev/null | sort | uniq -c | sed 's/^/      /'
    printf '\n    The last few runs:\n'
    grep 'RUN START' "$LOG" 2>/dev/null | tail -5 | cut -c1-160 | sed 's/^/      /'
    printf '\n    NOTE: this log is NOT included in the backup zip. For a QI study it is\n'
    printf '    research provenance — decide deliberately whether to copy it across.\n'
else
    printf '    No validation log at %s — no summaries have been generated since the rewrite.\n' "$LOG"
fi

ask "Q31 — Is the recall QI study still collecting data? When is the next CCC meeting (your real deadline)?"
ask "Q32 — Have you exported the study CSVs yet, and where did you put them?"

h "Still to do by hand"
printf '\n  [ ] Answer every blank above.\n'
printf '  [ ] Take a full backup (Settings -> Download Full Backup) and actually\n'
printf '      restore it: unzip it and open auc.db from the zip. A backup you have\n'
printf '      never restored is a hope, not a backup.\n'
printf '  [ ] Copy the Modelfiles above somewhere that is not this machine, along\n'
printf '      with any .gguf or adapter weights listed under Q6/Q7. The Modelfile\n'
printf '      is the recipe; the weights are what cannot be recreated.\n'
printf '  [ ] Ask hospital IT whether a new device needs registering, and whether\n'
printf '      devices on the wifi are allowed to talk to each other.\n'
printf '\n(end of capture)\n'
} > "$OUT" 2>&1

chmod 600 "$OUT"

echo ""
if ! tail -1 "$OUT" | grep -q '(end of capture)'; then
    echo "  ⚠ The capture stopped early — the file is INCOMPLETE."
    echo "    It ends with:"
    tail -3 "$OUT" | sed 's/^/      /'
    echo ""
    echo "    Partial file: $OUT"
    echo ""
    exit 1
fi

echo "  Captured to: $OUT"
echo "  (Outside the repository, so it cannot be committed by accident."
echo "   Readable only by you.)"
echo ""
echo "  $(grep -c '  -> ____' "$OUT" 2>/dev/null) questions are left for you to answer by hand."
echo "  Open it, fill those in, and keep it with the recovery key."
echo ""
