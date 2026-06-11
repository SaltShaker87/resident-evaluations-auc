"""
AUC — Backup helper.

Creates a single .zip containing a consistent copy of the database plus all
resident photos. Used two ways:

  * the /api/backup endpoint calls make_backup_zip() to build a download
  * the daily systemd timer runs this file as a script to drop a timestamped
    zip into AUC_BACKUP_DIR (point that at a OneDrive-synced folder so the
    backup ends up off this machine). See auc/BACKUPS.md.

Standalone on purpose: it imports only config (not app.py), so the scheduled
job stays lightweight and never pulls in the web server / RAG stack.
"""

import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from config import BACKUP_DIR, BACKUP_KEEP_DAYS

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PHOTOS_DIR = DATA_DIR / "photos"
DB_PATH = DATA_DIR / "auc.db"

BACKUP_PREFIX = "auc-backup-"


def make_backup_zip(zip_path: Path) -> Path:
    """
    Write a backup archive to zip_path: a consistent copy of the database
    (via SQLite's online-backup API, safe while the app is running) plus the
    photos directory. Returns zip_path.
    """
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    # Online backup of the live DB into an in-zip temp file. Using the backup
    # API rather than a raw file copy avoids capturing a half-written WAL.
    tmp_db = zip_path.with_suffix(".tmp.db")
    try:
        if DB_PATH.exists():
            src = sqlite3.connect(str(DB_PATH))
            try:
                dest = sqlite3.connect(str(tmp_db))
                try:
                    src.backup(dest)
                finally:
                    dest.close()
            finally:
                src.close()

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if tmp_db.exists():
                zf.write(tmp_db, arcname="auc.db")
            if PHOTOS_DIR.exists():
                for photo in sorted(PHOTOS_DIR.iterdir()):
                    if photo.is_file():
                        zf.write(photo, arcname=f"photos/{photo.name}")
    finally:
        if tmp_db.exists():
            tmp_db.unlink()

    return zip_path


def _prune(backup_dir: Path, keep_days: int) -> int:
    """Delete backups older than keep_days. Returns how many were removed."""
    if keep_days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for f in backup_dir.glob(f"{BACKUP_PREFIX}*.zip"):
        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink()
            removed += 1
    return removed


def run_scheduled_backup() -> int:
    """Entry point for the daily timer. Returns a process exit code."""
    if not BACKUP_DIR:
        print(
            "AUC_BACKUP_DIR is not set — automated backup is disabled. "
            "Set it to a synced folder (e.g. OneDrive) to enable backups. "
            "See auc/BACKUPS.md."
        )
        return 0

    backup_dir = Path(BACKUP_DIR).expanduser()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    zip_path = backup_dir / f"{BACKUP_PREFIX}{timestamp}.zip"

    try:
        make_backup_zip(zip_path)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the journal
        print(f"Backup FAILED: {exc}")
        return 1

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    pruned = _prune(backup_dir, BACKUP_KEEP_DAYS)
    print(
        f"Backup written: {zip_path} ({size_mb:.1f} MB). "
        f"Pruned {pruned} backup(s) older than {BACKUP_KEEP_DAYS} days."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_scheduled_backup())
