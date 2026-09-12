#!/bin/bash
# ============================================================
# AUC — prove a backup actually restores
#
#   bash auc/verify-backup.sh                  # the newest backup
#   bash auc/verify-backup.sh path/to/some.zip # a particular one
#
# A backup you have never opened is a hope, not a backup. This opens one,
# reads the database out of it, and compares what is inside against what is
# live right now.
#
# It matters more than it used to. The database is no longer tracked in git,
# so a fresh clone starts empty and nothing in the repository will ever remind
# you the database exists. These zips are the only copy.
#
# Nothing is written outside a temporary directory. The live database is
# opened read-only and immutable, so this is safe to run while the app is
# serving a committee meeting.
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$SCRIPT_DIR/backend"
VENV_PY="$BACKEND/venv/bin/python"

if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_OK=""; C_WARN=""; C_BAD=""; C_DIM=""; C_OFF=""
fi

pass() { printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$1"; }
info() { printf '  %s·%s %s\n' "$C_DIM" "$C_OFF" "$1"; }
warn() { printf '  %s⚠%s %s\n' "$C_WARN" "$C_OFF" "$1"; }
die()  { printf '\n  %s✗ %s%s\n\n' "$C_BAD" "$1" "$C_OFF"; exit 1; }

[ -x "$VENV_PY" ] || die "No Python environment at backend/venv. Run: bash $SCRIPT_DIR/setup.sh"

# --- Find the backup to check ------------------------------------------
if [ $# -ge 1 ]; then
    ZIP="$1"
    [ -f "$ZIP" ] || die "No such file: $ZIP"
else
    # Same place setup.sh points the nightly timer at.
    BACKUP_DIR=$(grep -hE '^Environment=AUC_BACKUP_DIR=' \
        "$HOME/.config/systemd/user/auc-backup.service" 2>/dev/null | cut -d= -f3-)
    BACKUP_DIR="${AUC_BACKUP_DIR:-${BACKUP_DIR:-$HOME/auc-backups}}"

    [ -d "$BACKUP_DIR" ] || die "No backup directory at $BACKUP_DIR. Settings → Download Full Backup, then pass the file to this script."

    ZIP=$(find "$BACKUP_DIR" -maxdepth 1 -name 'auc-backup-*.zip' -printf '%T@ %p\n' 2>/dev/null \
          | sort -rn | head -1 | cut -d' ' -f2-)
    [ -n "$ZIP" ] || die "No auc-backup-*.zip files in $BACKUP_DIR. Has the nightly backup ever run?"
fi

echo ""
echo "  Verifying: $ZIP"
info "$(du -h "$ZIP" | cut -f1), written $(date -r "$ZIP" '+%Y-%m-%d %H:%M')"

AGE_DAYS=$(( ( $(date +%s) - $(date -r "$ZIP" +%s) ) / 86400 ))
if [ "$AGE_DAYS" -gt 7 ]; then
    warn "This backup is $AGE_DAYS days old. Check the nightly timer: systemctl --user list-timers | grep auc"
fi

# --- Extract it somewhere disposable ------------------------------------
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if ! unzip -q "$ZIP" -d "$WORK" 2>/dev/null; then
    if ! "$VENV_PY" -c "
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])
" "$ZIP" "$WORK" 2>/dev/null; then
        die "The archive will not open. This backup is not usable — take a fresh one and check it."
    fi
fi
pass "Archive opens"

[ -f "$WORK/auc.db" ] || die "No auc.db inside the archive. This backup would not restore anything."
pass "Contains auc.db ($(du -h "$WORK/auc.db" | cut -f1))"

# --- Read both databases and compare ------------------------------------
LIVE_DATA=$("$VENV_PY" -c "import sys; sys.path.insert(0, '$BACKEND'); import config; print(config.DATA_DIR)" 2>/dev/null)

RESULT=$(BACKUP_DB="$WORK/auc.db" BACKUP_PHOTOS="$WORK/photos" \
         LIVE_DB="$LIVE_DATA/auc.db" LIVE_PHOTOS="$LIVE_DATA/photos" \
         "$VENV_PY" - <<'PY'
import os
import sqlite3
from pathlib import Path

TABLES = ["residents", "notes", "followups", "summaries", "medhub_evaluations",
          "ccc_sessions", "ccc_resident_logs", "ccc_contributions",
          "ccc_action_items", "auth_config"]


def read(db_path):
    """Counts from one database, opened read-only. None if it will not open."""
    if not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
        conn.execute("pragma quick_check").fetchone()
        out = {}
        for table in TABLES:
            try:
                out[table] = conn.execute(f"select count(*) from {table}").fetchone()[0]
            except sqlite3.Error:
                out[table] = None
        return out
    except Exception as e:
        print(f"ERROR\t{e}")
        return "broken"


backup = read(os.environ["BACKUP_DB"])
if backup == "broken":
    raise SystemExit(1)

live = read(os.environ["LIVE_DB"])

def photos(path):
    p = Path(path)
    return sum(1 for f in p.iterdir() if f.is_file()) if p.is_dir() else 0


print("INTEGRITY\tok")
for table in TABLES:
    b = backup.get(table)
    live_value = live.get(table) if live else None
    print(f"COUNT\t{table}\t{'-' if b is None else b}\t{'?' if live_value is None else live_value}")

print(f"PHOTOS\t{photos(os.environ['BACKUP_PHOTOS'])}\t{photos(os.environ['LIVE_PHOTOS'])}")
PY
)

if [ -z "$RESULT" ] || ! grep -q "^INTEGRITY" <<< "$RESULT"; then
    printf '  %s\n' "$(grep '^ERROR' <<< "$RESULT" | cut -f2-)"
    die "The database inside the archive is corrupt. This backup would not restore."
fi
pass "Database inside the archive passes an integrity check"

# --- Report --------------------------------------------------------------
echo ""
printf '  %-24s %10s %10s\n' "" "in backup" "live now"
printf '  %s\n' "$(printf '─%.0s' $(seq 1 47))"

DRIFT=0
MISSING=0
while IFS=$'\t' read -r kind name in_backup live_now; do
    case "$kind" in
        COUNT)
            if [ "$in_backup" = "-" ]; then
                printf '  %-24s %10s %10s   %s(table missing)%s\n' "$name" "absent" "$live_now" "$C_BAD" "$C_OFF"
                MISSING=$((MISSING + 1))
            elif [ "$live_now" = "?" ]; then
                printf '  %-24s %10s %10s\n' "$name" "$in_backup" "n/a"
            elif [ "$in_backup" != "$live_now" ]; then
                printf '  %-24s %10s %10s   %s(differs)%s\n' "$name" "$in_backup" "$live_now" "$C_DIM" "$C_OFF"
                DRIFT=$((DRIFT + 1))
            else
                printf '  %-24s %10s %10s\n' "$name" "$in_backup" "$live_now"
            fi
            ;;
        PHOTOS)
            if [ "$name" != "$in_backup" ]; then
                printf '  %-24s %10s %10s   %s(differs)%s\n' "photos (files)" "$name" "$in_backup" "$C_DIM" "$C_OFF"
                DRIFT=$((DRIFT + 1))
            else
                printf '  %-24s %10s %10s\n' "photos (files)" "$name" "$in_backup"
            fi
            ;;
    esac
done <<< "$RESULT"

echo ""

if [ "$MISSING" -gt 0 ]; then
    die "$MISSING table(s) are missing from the backup. It predates part of the schema and would not restore fully."
fi

RESIDENTS=$(grep -P '^COUNT\tresidents\t' <<< "$RESULT" | cut -f3)
if [ "${RESIDENTS:-0}" -eq 0 ]; then
    warn "The backup contains no residents. Correct only if the app is genuinely empty."
fi

AUTH=$(grep -P '^COUNT\tauth_config\t' <<< "$RESULT" | cut -f3)
if [ "${AUTH:-0}" -ge 1 ]; then
    pass "Password is inside the backup — restoring it keeps your existing password"
else
    warn "No password in the backup. Restoring it would ask you to create one."
fi

if [ "$DRIFT" -gt 0 ]; then
    info "Rows differ from live because the backup is a snapshot — expected unless it was taken seconds ago."
fi

printf '\n  %sThis backup restores. To use it: stop the app, replace data/auc.db and%s\n' "$C_OK" "$C_OFF"
printf '  %sdata/photos with the contents of the archive, start the app.%s\n\n' "$C_OK" "$C_OFF"
printf '  %sNote: data/logs/summary_validation.log is NOT in the backup.%s\n\n' "$C_DIM" "$C_OFF"
