"""
AUC — updating itself, from the Settings page.

Two halves live here:

  * what the running app asks: which version is installed, what the newest
    published release on GitHub is and what changed since, and whether this
    copy of AUC is one that can update itself (update_api.py serves these);

  * the update itself, run as a script — `python updater.py run --to 1.2.0` —
    in a short-lived systemd job of its own (see launch()). It has to be
    separate from the app: updating means stopping auc.service, and stopping a
    service stops everything that was started from inside it.

Only an install made by the AUC Installer can update itself, because only
there does each version live in its own folder (installer/CONTRACT.md §1):

    ~/.local/share/auc/app/<version>/   one release, with its own backend/venv
    ~/.local/share/auc/app/current      a link to the one in use
    ~/.local/share/auc/data/            the database and photos
    ~/.local/share/auc/tools/uv         the installer's Python tool, reused here

The steps follow the installer's own Update (installer/src-tauri/src/engine/
steps/update.rs). The new version is made ready beside the old one while the
old one keeps serving. Only then is AUC paused, backed up, switched over and
started again. If the new version does not answer, the link is pointed back,
the database is put back from the backup just taken, and the old version is
started again. Nothing here ever writes to the data folder except that
restore, or touches auc.env.

Standalone, like backup.py: it imports config and backup, never app.py.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Tuple

import backup as backup_helper
import config
import httpx

# ---------------------------------------------------------------------------
# Where updates come from. Written into the code on purpose: there is no
# setting that points the updater anywhere else.
# ---------------------------------------------------------------------------
REPO = "SaltShaker87/resident-evaluations-auc"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"

SERVICE_UNIT = "auc.service"
UPDATER_UNIT = "auc-update.service"

# The Python the installer builds every version's environment with.
PYTHON_VERSION = "3.12"

# A release is about 2 MB. Anything near this is not one of ours.
MAX_DOWNLOAD_BYTES = 200 * 1000 * 1000
# The new version's Python environment (chromadb, onnxruntime) is most of this.
MIN_FREE_BYTES = 2 * 1000 * 1000 * 1000
# How long the new version gets to start answering before the update gives up on it.
START_TIMEOUT_SECONDS = 120

PRE_UPDATE_PREFIX = "auc-pre-update-"
KEEP_PRE_UPDATE_BACKUPS = 5
KEEP_UPDATE_LOGS = 10
STATUS_FILE_NAME = "update-status.json"

# Every release has these. One that does not is broken, or is not AUC.
REQUIRED_FILES = (
    "VERSION",
    "run.sh",
    "backend/app.py",
    "backend/config.py",
    "backend/requirements.txt",
    "backend/requirements-rag.txt",
    "frontend/dist/index.html",
    "nim/docker-compose.yml",
    "rag/build_index.py",
)

# What the ACGME index is built from. If none of it changed, the current
# version's index is carried over as it is; if any of it did, the index is
# rebuilt in the new version's folder before anything is switched.
INDEX_INPUTS = (
    "rag/documents",
    "rag/ontology",
    "rag/build_index.py",
    "backend/rag_retrieval.py",
)

# What the update job is given from the app's own environment: the settings
# auc.env gave the service, and whatever this machine needs to reach the
# internet (a hospital proxy, its certificates) and its own systemd.
PASSTHROUGH_PREFIXES = ("AUC_", "OLLAMA_", "UV_")
PASSTHROUGH_NAMES = (
    "HOME", "PATH", "LANG",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)

RESTART_HINT = (
    "On the AUC computer, run: systemctl --user restart auc — or open the AUC "
    "Installer and choose Repair."
)

APP_DIR = Path(__file__).resolve().parent.parent

# Tests swap this for httpx.MockTransport, so they never reach the internet.
HTTP_TRANSPORT = None


class UpdateError(Exception):
    """Something that stops an update, said in words fit to show the user."""


# ---------------------------------------------------------------------------
# Version numbers
# ---------------------------------------------------------------------------

VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(text) -> Optional[Tuple[int, int, int]]:
    """`1.2.0` or `v1.2.0` -> (1, 2, 0). Anything else -> None."""
    match = VERSION_PATTERN.match(str(text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(candidate, installed) -> bool:
    new, old = parse_version(candidate), parse_version(installed)
    return bool(new and old and new > old)


def read_version(app_dir: Path) -> str:
    """The version a copy of AUC says it is. A git checkout has no VERSION file."""
    try:
        text = (app_dir / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    return text or "dev"


# ---------------------------------------------------------------------------
# The `current` link
#
# Two primitives, so a test on a computer that may not make symlinks (Windows
# without developer mode) can stand in for them. Everything else about the
# switch — the temporary name, the atomic rename — is the real code.
# ---------------------------------------------------------------------------

def _make_link(target: Path, link: Path) -> None:
    os.symlink(target, link, target_is_directory=True)


def _read_link(link: Path) -> Optional[Path]:
    try:
        return Path(os.readlink(link))
    except OSError:
        return None


def current_target(apps_dir: Path) -> Optional[Path]:
    """The version folder app/current points at, or None if it is not a link."""
    target = _read_link(apps_dir / "current")
    if target is None:
        return None
    if not target.is_absolute():
        target = apps_dir / target
    return target.resolve()


def point_current_at(apps_dir: Path, version_dir: Path) -> None:
    """Point app/current at a version, with no moment where it does not exist.

    The link is made under a temporary name and renamed over the old one, as
    the installer does, so a service starting at the wrong instant never finds
    nothing there.
    """
    staging = apps_dir / ".current.new"
    if os.path.lexists(staging):
        staging.unlink()
    _make_link(version_dir, staging)
    os.replace(staging, apps_dir / "current")


# ---------------------------------------------------------------------------
# What kind of install this is
# ---------------------------------------------------------------------------

@dataclass
class Install:
    """The copy of AUC that is running, and whether it can update itself."""

    app_dir: Path
    version: str
    # ~/.local/share/auc for an installer install; None for anything else.
    auc_home: Optional[Path] = None
    # Why it cannot update itself, in words for the Settings page. None when it can.
    reason: Optional[str] = None

    @property
    def can_update(self) -> bool:
        return self.reason is None and self.auc_home is not None

    @property
    def apps_dir(self) -> Path:
        return self.auc_home / "app"

    @property
    def logs_dir(self) -> Path:
        return self.auc_home / "logs"

    @property
    def status_file(self) -> Path:
        return self.logs_dir / STATUS_FILE_NAME

    @property
    def uv(self) -> Path:
        return self.auc_home / "tools" / "uv"

    @property
    def python_install_dir(self) -> Path:
        return self.auc_home / "tools" / "python"

    @property
    def backups_dir(self) -> Path:
        if config.BACKUP_DIR:
            return Path(config.BACKUP_DIR).expanduser()
        return self.auc_home / "backups"

    @property
    def state_file(self) -> Path:
        """The installer's record of what it installed (CONTRACT.md §1)."""
        config_home = os.environ.get("AUC_CONFIG_HOME") or str(Path.home() / ".config" / "auc")
        return Path(config_home) / "installer-state.json"


def detect_install(app_dir: Path = APP_DIR, environ=None, which=shutil.which) -> Install:
    """Work out what kind of install this is, and why it cannot update itself if it cannot."""
    environ = os.environ if environ is None else environ
    version = read_version(app_dir)
    install = Install(app_dir=app_dir, version=version)

    if parse_version(version) is None:
        if (app_dir.parent / ".git").exists():
            install.reason = (
                "This copy of AUC runs from a git checkout, so it does not update itself. "
                "Update it from a terminal with git pull, then bash auc/setup.sh."
            )
        else:
            install.reason = (
                "This copy of AUC has no release number, so it cannot tell which updates "
                "apply to it. Update it with the AUC Installer."
            )
        return install

    apps_dir = app_dir.parent
    target = current_target(apps_dir) if apps_dir.name == "app" else None
    if target is None:
        install.reason = (
            "This copy of AUC was not installed by the AUC Installer, so it cannot update "
            "itself. Update it the way it was installed."
        )
        return install
    install.auc_home = apps_dir.parent

    if target != app_dir.resolve():
        install.reason = (
            f"AUC is running version {version}, but a different version has been installed "
            "since it started. Restart AUC (systemctl --user restart auc), then try again."
        )
        return install
    if not install.uv.is_file():
        install.reason = (
            f"The AUC Installer's Python tool is missing ({install.uv}). Open the AUC "
            "Installer and choose Repair, then try again."
        )
        return install
    if not (environ.get("INVOCATION_ID") and which("systemd-run") and which("systemctl")):
        install.reason = (
            "AUC is not running as its background service here, so it cannot restart itself "
            "after an update. Start it with systemctl --user start auc, or update it with the "
            "AUC Installer."
        )
        return install
    return install


# ---------------------------------------------------------------------------
# Asking GitHub
# ---------------------------------------------------------------------------

def _client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        transport=HTTP_TRANSPORT,
        headers={"User-Agent": f"AUC-updater/{read_version(APP_DIR)}"},
    )


def _get_json(url: str):
    try:
        with _client() as client:
            response = client.get(url, headers={"Accept": "application/vnd.github+json"})
    except httpx.HTTPError as exc:
        raise UpdateError(
            "AUC could not reach GitHub to check for updates. Check this computer's internet "
            "connection and try again."
        ) from exc
    if response.status_code in (403, 429):
        raise UpdateError(
            "GitHub turned the update check away, usually because too many checks came from "
            "this network in the last hour. Try again later."
        )
    if response.status_code == 404:
        raise UpdateError("GitHub has no published release of AUC to offer.")
    if response.status_code != 200:
        raise UpdateError(
            f"GitHub did not answer the update check properly (error {response.status_code}). "
            "Try again later."
        )
    try:
        return response.json()
    except ValueError as exc:
        raise UpdateError("GitHub's answer about updates was not in a form AUC understands.") from exc


def _get_small_text(url: str, limit: int = 4096) -> str:
    try:
        with _client() as client:
            response = client.get(url, headers={"Accept": "application/octet-stream"})
    except httpx.HTTPError as exc:
        raise UpdateError(
            "AUC could not fetch the release's fingerprint file from GitHub. Try again."
        ) from exc
    if response.status_code != 200:
        raise UpdateError(
            f"GitHub refused to send the release's fingerprint file (error {response.status_code})."
        )
    return response.text[:limit]


@dataclass
class Release:
    version: str
    tag: str
    page_url: Optional[str]
    published_at: Optional[str]
    tarball_url: str
    checksum_url: str
    # The fingerprint GitHub itself recorded when the file was uploaded, when it says.
    github_digest: Optional[str]
    changes: List[str] = field(default_factory=list)


def _sha256_value(text) -> Optional[str]:
    """`sha256:<64 hex>` (GitHub's asset digest) -> the hex, or None."""
    if not isinstance(text, str) or not text.startswith("sha256:"):
        return None
    value = text[len("sha256:"):].strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


def release_from_json(data) -> Release:
    """Read what we need from GitHub's description of one release, and refuse anything odd."""
    if not isinstance(data, dict):
        raise UpdateError("GitHub's answer about the newest release was not in a form AUC understands.")
    tag = str(data.get("tag_name") or "")
    parsed = parse_version(tag)
    if parsed is None:
        raise UpdateError(
            f"The newest release on GitHub is called “{tag}”, which is not a version number "
            "AUC recognises, so it will not install it."
        )
    version = ".".join(str(part) for part in parsed)
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError(f"Release {version} is not a finished release, so AUC will not install it.")

    assets = {
        asset.get("name"): asset
        for asset in data.get("assets") or []
        if isinstance(asset, dict)
    }
    tarball = assets.get(f"auc-{version}.tar.gz")
    checksum = assets.get(f"auc-{version}.tar.gz.sha256")
    if tarball is None:
        raise UpdateError(
            f"Release {version} has no auc-{version}.tar.gz to download yet. It may still be "
            "being published; try again in a few minutes."
        )
    if checksum is None:
        raise UpdateError(
            f"Release {version} was published without its fingerprint file "
            f"(auc-{version}.tar.gz.sha256), so AUC cannot check the download and will not "
            "install it."
        )
    # Both files must come from this project's own release, under this tag.
    expected_prefix = f"{DOWNLOAD_PREFIX}{tag}/"
    for asset in (tarball, checksum):
        url = str(asset.get("browser_download_url") or "")
        if not url.startswith(expected_prefix):
            raise UpdateError(
                f"Release {version} points its download somewhere other than this project's "
                "GitHub releases, so AUC will not use it."
            )

    return Release(
        version=version,
        tag=tag,
        page_url=data.get("html_url"),
        published_at=data.get("published_at"),
        tarball_url=tarball["browser_download_url"],
        checksum_url=checksum["browser_download_url"],
        github_digest=_sha256_value(tarball.get("digest")),
        changes=changes_from_notes(data.get("body")),
    )


def latest_release() -> Release:
    """The release GitHub marks as the latest. Drafts and pre-releases are never it."""
    return release_from_json(_get_json(f"{RELEASES_API}/latest"))


# ---------------------------------------------------------------------------
# What changed
# ---------------------------------------------------------------------------

_BY_AUTHOR = re.compile(r"\s+by\s+@[\w.-]+(?:\[bot\])?(?:\s+in\s+\S+)?\s*$")
_IN_LINK = re.compile(r"\s+in\s+https?://\S+\s*$")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
MAX_CHANGES_PER_RELEASE = 20


def _plain(line: str) -> str:
    text = line.strip()
    if text[:2] in ("* ", "- "):
        text = text[2:]
    text = _BY_AUTHOR.sub("", text)
    text = _IN_LINK.sub("", text)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    return text.replace("**", "").replace("`", "").strip()


def changes_from_notes(body) -> List[str]:
    """The list of changes from a release's notes, as plain sentences.

    The release workflow writes a "Which file do I need?" guide at the top and
    GitHub appends its own list of the pull requests merged, under "What's
    Changed". That list is what a person wants; the guide and the "Full
    Changelog" link are not.
    """
    lines = str(body or "").splitlines()

    def folded(line):
        return line.strip().lower().replace("’", "'")

    start = next((i + 1 for i, line in enumerate(lines) if folded(line).startswith("## what's changed")), None)
    if start is None:
        start = next(
            (i + 1 for i, line in enumerate(lines) if "automated release notes follow" in folded(line)),
            0,
        )

    changes = []
    skipping = False
    for line in lines[start:]:
        text = folded(line)
        if text.startswith("## "):
            # Another section. "Which file do I need?" and "New Contributors" are not changes.
            skipping = not text.startswith("## what's changed")
            continue
        if skipping or not text or text == "---" or text.startswith("**full changelog**"):
            continue
        if text.startswith("when publishing this draft"):
            continue
        plain = _plain(line)
        if plain:
            changes.append(plain)
    return changes[:MAX_CHANGES_PER_RELEASE]


def _notes_between(installed: str, latest: Release) -> List[Dict]:
    """The changes in every release after the installed one, up to the latest, newest first."""
    newest_notes = {
        "version": latest.version,
        "published_at": latest.published_at,
        "url": latest.page_url,
        "changes": latest.changes,
    }
    try:
        listed = _get_json(f"{RELEASES_API}?per_page=30")
    except UpdateError:
        return [newest_notes]
    if not isinstance(listed, list):
        return [newest_notes]

    notes = {latest.version: newest_notes}
    low, high = parse_version(installed), parse_version(latest.version)
    for data in listed:
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            continue
        parsed = parse_version(data.get("tag_name"))
        if parsed is None or not (low < parsed <= high):
            continue
        version = ".".join(str(part) for part in parsed)
        notes.setdefault(version, {
            "version": version,
            "published_at": data.get("published_at"),
            "url": data.get("html_url"),
            "changes": changes_from_notes(data.get("body")),
        })
    return sorted(notes.values(), key=lambda n: parse_version(n["version"]), reverse=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_for_update(install: Install) -> Dict:
    """Everything the Settings page shows about updates. Never raises: a failed check is a message."""
    result = {
        "installed": install.version,
        "latest": None,
        "available": False,
        "published_at": None,
        "release_url": None,
        "notes": [],
        "can_update": install.can_update,
        "reason": install.reason,
        "error": None,
        "checked_at": _now_iso(),
    }
    try:
        latest = latest_release()
    except UpdateError as exc:
        result["error"] = str(exc)
        return result
    result.update(
        latest=latest.version,
        published_at=latest.published_at,
        release_url=latest.page_url,
    )
    if is_newer(latest.version, install.version):
        result["available"] = True
        result["notes"] = _notes_between(install.version, latest)
    return result


# ---------------------------------------------------------------------------
# Downloading and checking
# ---------------------------------------------------------------------------

def download_file(url: str, dest: Path, on_progress: Optional[Callable[[int, Optional[int]], None]] = None) -> str:
    """Download to dest and return the file's SHA-256, computed as it arrives."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    try:
        with _client(timeout=60.0) as client:
            with client.stream("GET", url, headers={"Accept": "application/octet-stream"}) as response:
                if response.status_code != 200:
                    raise UpdateError(
                        f"GitHub refused the download (error {response.status_code}). Try again later."
                    )
                if response.url.scheme != "https":
                    raise UpdateError("The download was sent to an insecure address, so it was stopped.")
                total = response.headers.get("content-length")
                total = int(total) if total and total.isdigit() else None
                with open(dest, "wb") as out:
                    for chunk in response.iter_bytes(256 * 1024):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise UpdateError(
                                "The download is far larger than any AUC release, so it was stopped."
                            )
                        hasher.update(chunk)
                        out.write(chunk)
                        if on_progress:
                            on_progress(size, total)
    except httpx.HTTPError as exc:
        raise UpdateError(
            "The download from GitHub stopped part way through. Check the internet connection "
            "and try again."
        ) from exc
    return hasher.hexdigest()


def digest_from_checksum_file(text: str, filename: str) -> Optional[str]:
    """A .sha256 file is `<digest>  <filename>`. The digest, if it is for this file."""
    words = str(text or "").split()
    if not words:
        return None
    digest = words[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    if len(words) > 1 and words[1].lstrip("*") != filename:
        return None
    return digest


def verify_download(release: Release, actual_digest: str) -> None:
    """The download must match the published .sha256 file, and GitHub's own record of it."""
    filename = f"auc-{release.version}.tar.gz"
    published = digest_from_checksum_file(_get_small_text(release.checksum_url), filename)
    if published is None:
        raise UpdateError(
            f"The fingerprint file published with release {release.version} could not be read, "
            "so the download could not be checked and was not used."
        )
    if actual_digest != published:
        raise UpdateError(
            "The download does not match the fingerprint published with the release, so it was "
            "not used. This usually means the download was interrupted. Try again."
        )
    if release.github_digest and actual_digest != release.github_digest:
        raise UpdateError(
            "The download does not match the fingerprint GitHub recorded for it, so it was not used."
        )


def _check_members(members) -> None:
    """Every entry must be an ordinary file or folder inside the archive's auc/ folder."""
    for member in members:
        name = member.name
        parts = PurePosixPath(name).parts
        if (
            not parts
            or name.startswith(("/", "\\"))
            or "\\" in name
            or parts[0] != "auc"
            or ".." in parts
        ):
            raise UpdateError(
                f"The download contains a file in an unexpected place ({name}), so it was not used."
            )
        if not (member.isfile() or member.isdir()):
            raise UpdateError(
                f"The download contains something other than ordinary files and folders ({name}), "
                "so it was not used."
            )


def _check_contents(unpacked: Path, version: str) -> None:
    if not unpacked.is_dir():
        raise UpdateError("The download does not contain an auc folder, so it is not a copy of AUC.")
    missing = [name for name in REQUIRED_FILES if not (unpacked / name).is_file()]
    if missing:
        raise UpdateError(
            f"The download is missing files every AUC release has ({', '.join(missing)}), so it "
            "was not used."
        )
    stated = read_version(unpacked)
    if parse_version(stated) != parse_version(version):
        raise UpdateError(
            f"The download says it is version {stated}, not {version}, so it was not used."
        )


def _remove(path: Path) -> None:
    if os.path.islink(path) or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def unpack_release(archive: Path, apps_dir: Path, version: str) -> Path:
    """Unpack a checked download into app/<version>/ and return that folder.

    Every entry is checked before anything is written, and Python's own "data"
    filter applies the same rules again while extracting. The release's single
    auc/ wrapper is dropped, so backend/ lands where the units expect it.
    """
    staging = apps_dir / f".staging-{version}"
    _remove(staging)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _check_members(tar.getmembers())
            if hasattr(tarfile, "data_filter"):
                tar.extractall(staging, filter="data")
            else:  # Python without the filter: the checks above are what protects us
                tar.extractall(staging)
    except UpdateError:
        _remove(staging)
        raise
    except (tarfile.TarError, EOFError, zlib.error, OSError) as exc:
        _remove(staging)
        raise UpdateError("The download is not a readable AUC release, so it was not used.") from exc

    unpacked = staging / "auc"
    try:
        _check_contents(unpacked, version)
    except UpdateError:
        _remove(staging)
        raise

    target = apps_dir / version
    _remove(target)  # a half-finished earlier attempt; never the running version, which is older
    os.rename(unpacked, target)
    _remove(staging)
    for script in target.glob("*.sh"):
        script.chmod(script.stat().st_mode | 0o100)
    return target


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

def make_pre_update_backup(install: Install, from_version: str, to_version: str) -> Path:
    """Back up the database and photos, and keep only the newest few pre-update backups.

    Named apart from the nightly auc-backup-* files, so the nightly clean-up
    leaves them alone and verify-backup.sh does not mistake one for the other.
    """
    folder = install.backups_dir
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = folder / f"{PRE_UPDATE_PREFIX}{from_version}-to-{to_version}-{stamp}.zip"
    backup_helper.make_backup_zip(path)
    older = sorted(
        folder.glob(f"{PRE_UPDATE_PREFIX}*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[KEEP_PRE_UPDATE_BACKUPS:]
    for stale in older:
        stale.unlink(missing_ok=True)
    return path


def restore_database(zip_path: Path) -> bool:
    """Put the database back from a backup zip. Photos are not touched.

    Only ever called while AUC is stopped. The write-ahead log is removed first:
    left beside the restored file, SQLite would replay the failed version's
    writes onto it.
    """
    db_path = Path(backup_helper.DB_PATH)
    with zipfile.ZipFile(zip_path) as archive:
        if "auc.db" not in archive.namelist():
            return False
        restoring = db_path.with_name(db_path.name + ".restoring")
        with archive.open("auc.db") as source, open(restoring, "wb") as dest:
            shutil.copyfileobj(source, dest)
    for sidecar in ("-wal", "-shm"):
        db_path.with_name(db_path.name + sidecar).unlink(missing_ok=True)
    os.replace(restoring, db_path)
    return True


# ---------------------------------------------------------------------------
# Talking to the machine
# ---------------------------------------------------------------------------

class System:
    """Everything the update does outside its own folders: run programs, talk to
    systemd, ask the app which version is answering, wait. Tests swap it."""

    def run(self, args, *, cwd=None, env=None, timeout=600) -> Tuple[int, str]:
        try:
            done = subprocess.run(
                [str(a) for a in args],
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError:
            return 127, f"{args[0]} is not installed on this computer."
        except subprocess.TimeoutExpired as exc:
            output = exc.output.decode("utf-8", "replace") if isinstance(exc.output, bytes) else (exc.output or "")
            return 124, f"{output}\n(stopped after {timeout} seconds)"
        return done.returncode, done.stdout or ""

    def systemctl(self, *args) -> Tuple[int, str]:
        return self.run(["systemctl", "--user", *args], timeout=120)

    def answering_version(self, url: str) -> Optional[str]:
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code == 200:
                return str(response.json().get("version"))
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        return None

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()


# Tests replace this with a stand-in.
SYSTEM = System()


def _app_url(path: str) -> str:
    host = config.HOST if config.HOST not in ("", "0.0.0.0", "::") else "127.0.0.1"
    return f"http://{host}:{config.PORT}{path}"


# ---------------------------------------------------------------------------
# Progress, as the Settings page sees it
# ---------------------------------------------------------------------------

STEPS = (
    ("download", "Downloading the new version"),
    ("verify", "Checking the download is genuine and intact"),
    ("prepare", "Setting up the new version"),
    ("index", "Preparing the ACGME reference index"),
    ("backup", "Pausing AUC and backing up your database and photos"),
    ("switch", "Starting the new version"),
)

# What a finished update can end as:
#   succeeded    the new version is running
#   failed       nothing changed; the old version is running
#   rolled_back  the new version would not start; the old one is back
#   broken       neither came up; the message says what to do
#   interrupted  the job stopped without finishing (worked out by read_status)
FINISHED_STATES = ("succeeded", "failed", "rolled_back", "broken", "interrupted")


def _write_json(path: Path, data) -> None:
    """Whole file at once (temporary file, then rename), so a reader never sees half of it.

    Each write gets its own temporary file: the app (marking an update
    interrupted) and the update job can both be writing at once.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2)
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows will not replace a file someone is reading at that
                # moment (Linux always can), so give the reader a moment.
                if attempt == 19:
                    raise
                time.sleep(0.05)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class Progress:
    """The update's progress, kept in logs/update-status.json.

    A file rather than memory because the page goes on reading it after the
    restart, from the new version.
    """

    def __init__(self, path: Path, from_version: str, to_version: str, log_file: Optional[Path]):
        self.path = path
        self.data = {
            "state": "running",
            "from_version": from_version,
            "to_version": to_version,
            "started_at": _now_iso(),
            "finished_at": None,
            "message": None,
            "warnings": [],
            "log_file": str(log_file) if log_file else None,
            "steps": [
                {"id": step_id, "label": label, "status": "pending", "detail": None}
                for step_id, label in STEPS
            ],
        }
        self.current = None
        self.save()

    def save(self) -> None:
        # Best effort. What the page shows must never be what stops an update
        # part way: a rollback has to happen even if this file cannot be
        # written (a full disk, say). The log, and the end state, still say
        # what happened.
        try:
            _write_json(self.path, self.data)
        except OSError:
            pass

    def _set(self, step_id: str, status: Optional[str], detail: Optional[str]) -> None:
        for step in self.data["steps"]:
            if step["id"] == step_id:
                if status is not None:
                    step["status"] = status
                step["detail"] = detail
        self.save()

    def start(self, step_id: str, detail: Optional[str] = None) -> None:
        self.current = step_id
        self._set(step_id, "running", detail)

    def detail(self, step_id: str, detail: str) -> None:
        self._set(step_id, None, detail)

    def done(self, step_id: str, detail: Optional[str] = None, status: str = "done") -> None:
        self._set(step_id, status, detail)

    def failed(self, step_id: str, detail: Optional[str] = None) -> None:
        self._set(step_id, "failed", detail)

    def warn(self, text: str) -> None:
        self.data["warnings"].append(text)
        self.save()

    def finish(self, state: str, message: str) -> None:
        self.data.update(state=state, message=message, finished_at=_now_iso())
        self.save()


class Log:
    """One update's log: every step and every command's output, for "Copy details"."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def line(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            with open(self.path, "a", encoding="utf-8") as out:
                for row in str(text).splitlines() or [""]:
                    out.write(f"[{stamp}] {row}\n")
        except OSError:
            pass  # best effort, like Progress.save: never the reason an update stops half way


def _tail(path: Path, lines: int = 60) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def read_status(install: Install, check_running: bool = False, system: Optional[System] = None) -> Dict:
    """The last update's progress, or {"state": "idle"} if there has never been one.

    With check_running, an update recorded as running whose job is no longer
    alive is reported as interrupted — the machine restarted part way, say —
    so the page does not wait for it forever and a new one can be started.
    """
    if install.auc_home is None:
        return {"state": "idle"}

    def load():
        try:
            return json.loads(install.status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    data = load()
    if not isinstance(data, dict):
        return {"state": "idle"}

    if check_running and data.get("state") == "running":
        system = system or SYSTEM
        code, _ = system.systemctl("is-active", "--quiet", UPDATER_UNIT)
        if code != 0:
            # The job may have written its last word between our two looks.
            data = load() or data
            if data.get("state") == "running":
                data.update(
                    state="interrupted",
                    finished_at=_now_iso(),
                    message=(
                        "The last update stopped before it finished, most likely because the "
                        f"computer restarted during it. AUC is running version {install.version}. "
                        "You can try the update again."
                    ),
                )
                try:
                    _write_json(install.status_file, data)
                except OSError:
                    pass  # reported as interrupted either way; it is worked out again next time

    log_file = data.get("log_file")
    if log_file and Path(log_file).parent == install.logs_dir:
        data["log_tail"] = _tail(Path(log_file))
    return data


# ---------------------------------------------------------------------------
# Starting the update job
# ---------------------------------------------------------------------------

def _passes_through(name: str) -> bool:
    return name.startswith(PASSTHROUGH_PREFIXES) or name in PASSTHROUGH_NAMES


def launch(install: Install, version: str, system: Optional[System] = None, environ=None) -> None:
    """Start `updater.py run` as its own systemd job, outside auc.service.

    Called from the app. Returns as soon as the job is running; the job
    reports through the status file.
    """
    system = system or SYSTEM
    environ = os.environ if environ is None else environ
    # Written first, so the page shows the update starting at once.
    progress = Progress(install.status_file, install.version, version, None)

    system.systemctl("reset-failed", UPDATER_UNIT)
    backend = install.app_dir / "backend"
    args = [
        "systemd-run", "--user",
        f"--unit={UPDATER_UNIT}",
        "--collect", "--quiet",
        f"--description=AUC update to {version}",
        f"--working-directory={backend}",
    ]
    # NAME with no value: systemd-run copies it from its own environment, so
    # nothing secret ends up on a command line.
    args += [f"--setenv={name}" for name in sorted(environ) if _passes_through(name)]
    args += [sys.executable, str(backend / "updater.py"), "run", "--to", version]

    code, output = system.run(args, timeout=60)
    if code != 0:
        detail = " ".join(output.split())[-300:]
        message = f"The updater could not be started, so nothing was changed. ({detail})"
        progress.finish("failed", message)
        raise UpdateError(message)


# ---------------------------------------------------------------------------
# The update itself
# ---------------------------------------------------------------------------

def _tree_digest(path: Path) -> Optional[str]:
    """One fingerprint for a file or a whole folder, or None if it is not there."""
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        return None
    hasher = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        hasher.update(item.relative_to(path).as_posix().encode("utf-8") + b"\0")
        hasher.update(hashlib.sha256(item.read_bytes()).digest())
    return hasher.hexdigest()


def index_inputs_changed(old_dir: Path, new_dir: Path) -> bool:
    return any(_tree_digest(old_dir / name) != _tree_digest(new_dir / name) for name in INDEX_INPUTS)


def _files_differ(left: Path, right: Path) -> bool:
    try:
        return left.read_bytes() != right.read_bytes()
    except OSError:
        # Cannot compare them: assume something changed, as the installer does.
        return True


def nemotron_in_use(install: Install) -> bool:
    """Whether the installer set up the NVIDIA Nemotron containers on this machine.

    If its record cannot be read, assume they are: the cautious answer only
    costs sending this one update through the installer.
    """
    try:
        state = json.loads(install.state_file.read_text(encoding="utf-8"))
        return bool((state.get("choices") or {}).get("nemotron_enabled", True))
    except (OSError, ValueError, AttributeError):
        return True


def chromadb_installed_now() -> bool:
    """Whether the running version has the ACGME index packages."""
    return importlib.util.find_spec("chromadb") is not None


def _sentence(text) -> str:
    """A message that ends like a sentence, so another can follow it."""
    text = str(text).strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _gb(size: int) -> str:
    return f"{size / 1e9:.1f} GB"


def _mb(size: int) -> str:
    return f"{size / 1e6:.1f} MB"


class _Update:
    def __init__(self, install: Install, version: str, system: System, progress: Progress, log: Log):
        self.install = install
        self.version = version
        self.system = system
        self.progress = progress
        self.log = log
        self.old_dir = install.app_dir.resolve()
        self.from_version = install.version
        self.new_dir: Optional[Path] = None
        self.archive: Optional[Path] = None
        self.backup_zip: Optional[Path] = None
        self.switched = False

    # -- helpers ------------------------------------------------------------

    def _run(self, args, *, cwd=None, env=None, timeout=600) -> Tuple[int, str]:
        self.log.line("$ " + " ".join(str(a) for a in args))
        code, output = self.system.run(args, cwd=cwd, env=env, timeout=timeout)
        if output.strip():
            self.log.line(output.rstrip())
        self.log.line(f"(exit code {code})")
        return code, output

    def _must(self, result: Tuple[int, str], message: str) -> None:
        if result[0] != 0:
            raise UpdateError(f"{message}. The details are in the log.")

    def _systemctl(self, *args) -> Tuple[int, str]:
        self.log.line("$ systemctl --user " + " ".join(args))
        code, output = self.system.systemctl(*args)
        if output.strip():
            self.log.line(output.rstrip())
        return code, output

    def _finish(self, state: str, message: str) -> int:
        if self.progress.current and state != "succeeded":
            for step in self.progress.data["steps"]:
                if step["id"] == self.progress.current and step["status"] == "running":
                    step["status"] = "failed"
        self.log.line(message)
        self.progress.finish(state, message)
        return 0 if state == "succeeded" else 1

    def _discard_new(self) -> None:
        """Remove what this attempt unpacked. Never the running version."""
        for path in (self.archive, self.install.apps_dir / f".staging-{self.version}", self.new_dir):
            if path is None or (path.exists() and path.resolve() == self.old_dir):
                continue
            try:
                _remove(path)
            except OSError as exc:
                self.log.line(f"Could not remove {path}: {exc}")

    # -- the steps ----------------------------------------------------------

    def run(self) -> int:
        self.log.line(f"Updating AUC {self.from_version} to {self.version}.")
        try:
            self._prepare()
        except UpdateError as exc:
            self._discard_new()
            return self._finish(
                "failed",
                f"{_sentence(exc)} Nothing was changed: AUC {self.from_version} is still running.",
            )
        except Exception as exc:  # noqa: BLE001 — a bug here must still end in a clear answer
            self.log.line(traceback.format_exc())
            self._discard_new()
            return self._finish(
                "failed",
                f"Something unexpected went wrong while preparing the update ({exc}). Nothing was "
                f"changed: AUC {self.from_version} is still running.",
            )
        try:
            return self._switch()
        except Exception as exc:  # noqa: BLE001
            self.log.line(traceback.format_exc())
            if self.switched:
                return self._roll_back(f"Something unexpected went wrong while switching ({exc}).")
            return self._recover_old(f"Something unexpected went wrong while switching ({exc}).")

    def _prepare(self) -> None:
        progress, install = self.progress, self.install

        progress.start("download", "Asking GitHub for the newest release")
        release = latest_release()
        if parse_version(release.version) != parse_version(self.version):
            raise UpdateError(
                f"The newest release is now {release.version}, not {self.version}. Check for "
                "updates again to see what changed."
            )
        if not is_newer(release.version, self.from_version):
            raise UpdateError(f"AUC {self.from_version} is already up to date.")
        self.version = release.version

        free = shutil.disk_usage(install.apps_dir).free
        if free < MIN_FREE_BYTES:
            raise UpdateError(
                f"There is not enough free disk space for the update: {_gb(free)} free, and it "
                f"needs about {_gb(MIN_FREE_BYTES)}."
            )

        self.archive = install.apps_dir / f".download-{self.version}.tar.gz"
        reported = [0]

        def on_progress(done: int, total: Optional[int]) -> None:
            if done - reported[0] >= 500_000 or done == total:
                reported[0] = done
                text = f"{_mb(done)} of {_mb(total)}" if total else _mb(done)
                progress.detail("download", text)

        self.log.line(f"Downloading {release.tarball_url}")
        digest = download_file(release.tarball_url, self.archive, on_progress)
        progress.done("download", f"AUC {self.version}, {_mb(self.archive.stat().st_size)}")

        progress.start("verify")
        verify_download(release, digest)
        self.log.line(f"The download's fingerprint {digest} matches the published ones.")
        progress.done("verify", "Matches the fingerprints published with the release")

        progress.start("prepare", "Unpacking")
        self.new_dir = unpack_release(self.archive, install.apps_dir, self.version)
        self.log.line(f"Unpacked into {self.new_dir}.")

        # Before minutes are spent on Python: an update that changes how the
        # Nemotron containers start may need the NVIDIA key, which is never
        # stored, so it can only be done with the installer.
        compose = "nim/docker-compose.yml"
        if _files_differ(self.old_dir / compose, self.new_dir / compose) and nemotron_in_use(install):
            raise UpdateError(
                "This update changes how the NVIDIA Nemotron engine is started, which only the "
                "AUC Installer can do (it may need your NVIDIA key). Please update with the "
                "installer this time (auc-installer --headless update)."
            )

        rag_ready = self._python_environment()
        progress.done("prepare", f"AUC {self.version} is ready beside the current version")

        progress.start("index")
        status, detail = self._index(rag_ready)
        progress.done("index", detail, status=status)

    def _python_environment(self) -> bool:
        """Build the new version its own Python environment, as the installer does.

        Returns whether the ACGME index packages installed.
        """
        install, progress = self.install, self.progress
        env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(install.python_install_dir))
        uv = str(install.uv)
        backend = self.new_dir / "backend"
        venv = backend / "venv"
        python = venv / "bin" / "python"

        progress.detail("prepare", f"Making sure Python {PYTHON_VERSION} is installed")
        self._must(
            self._run([uv, "python", "install", PYTHON_VERSION], env=env, timeout=20 * 60),
            f"Python {PYTHON_VERSION} could not be installed",
        )
        progress.detail("prepare", "Creating the new version's Python environment")
        self._must(
            self._run([uv, "venv", "--clear", "--python", PYTHON_VERSION, venv], env=env, timeout=10 * 60),
            "The new version's Python environment could not be created",
        )
        progress.detail("prepare", "Installing the packages the new version needs")
        self._must(
            self._run(
                [uv, "pip", "install", "--python", python, "-r", "requirements.txt"],
                cwd=backend, env=env, timeout=45 * 60,
            ),
            "The packages the new version needs would not install",
        )
        progress.detail("prepare", "Installing the ACGME index packages")
        code, _ = self._run(
            [uv, "pip", "install", "--python", python, "-r", "requirements-rag.txt"],
            cwd=backend, env=env, timeout=45 * 60,
        )
        if code == 0:
            return True
        if chromadb_installed_now():
            raise UpdateError(
                "The ACGME index packages (chromadb) would not install for the new version, so it "
                "could not write summaries. The details are in the log."
            )
        message = (
            "The ACGME index packages (chromadb) did not install for the new version either, so "
            "summaries are still unavailable, as they were before the update."
        )
        self.log.line(message)
        self.progress.warn(message)
        return False

    def _index(self, rag_ready: bool) -> Tuple[str, str]:
        """Carry the ACGME index across, or rebuild it if what it is built from changed.

        The index lives inside each version's folder (rag/chroma_db), and a
        release does not contain one, so the new folder starts without it.
        """
        old_index = self.old_dir / "rag" / "chroma_db"
        new_index = self.new_dir / "rag" / "chroma_db"
        had_index = old_index.is_dir() and any(old_index.iterdir())
        if had_index:
            shutil.copytree(old_index, new_index, dirs_exist_ok=True)
            if not index_inputs_changed(self.old_dir, self.new_dir):
                self.log.line("The ACGME index was carried over unchanged.")
                return "done", "Carried over from the current version"
            self.log.line("What the ACGME index is built from has changed, so it is being rebuilt.")
        if not rag_ready:
            return "skipped", "The ACGME index packages are not installed, so there is no index to prepare"

        self.progress.detail("index", "Rebuilding the index for the new version")
        python = self.new_dir / "backend" / "venv" / "bin" / "python"
        code, _ = self._run([python, self.new_dir / "rag" / "build_index.py"], cwd=self.new_dir, timeout=60 * 60)
        if code == 0:
            return "done", "Rebuilt for the new version" if had_index else "Built for the new version"
        if had_index:
            raise UpdateError(
                "The ACGME reference index could not be rebuilt for the new version, usually "
                "because Ollama or the Nemotron containers are not answering. Without it the new "
                "version could not write summaries. The details are in the log."
            )
        message = "The ACGME reference index could not be built, so summaries are still unavailable."
        self.progress.warn(message)
        return "warning", "Could not be built, so summaries are still unavailable"

    def _start_and_wait(self, version: str) -> bool:
        code, _ = self._systemctl("restart", SERVICE_UNIT)
        if code != 0:
            return False
        url = _app_url("/api/version")
        deadline = self.system.monotonic() + START_TIMEOUT_SECONDS
        seen = None
        while True:
            seen = self.system.answering_version(url)
            if parse_version(seen) == parse_version(version):
                self.log.line(f"AUC {version} is answering at {url}.")
                return True
            if self.system.monotonic() >= deadline:
                self.log.line(f"AUC {version} did not answer at {url} within {START_TIMEOUT_SECONDS} seconds (last answer: {seen!r}).")
                return False
            self.system.sleep(2)

    def _switch(self) -> int:
        progress = self.progress
        progress.start("backup", "Pausing AUC")
        code, _ = self._systemctl("stop", SERVICE_UNIT)
        if code != 0:
            progress.failed("backup", "AUC could not be paused")
            return self._recover_old("AUC could not be paused to switch versions, so the update stopped.")

        progress.detail("backup", "Backing up your database and photos")
        try:
            self.backup_zip = make_pre_update_backup(self.install, self.from_version, self.version)
        except Exception as exc:  # noqa: BLE001
            self.log.line(traceback.format_exc())
            progress.failed("backup", "The backup failed")
            return self._recover_old(f"The backup before updating failed ({exc}), so the update stopped.")
        self.log.line(f"Backed up to {self.backup_zip}.")
        progress.done("backup", f"Saved as {self.backup_zip.name}")

        progress.start("switch", f"Starting AUC {self.version}")
        point_current_at(self.install.apps_dir, self.new_dir)
        self.switched = True
        self.log.line(f"app/current now points at {self.new_dir}.")
        if not self._start_and_wait(self.version):
            return self._roll_back(f"AUC {self.version} would not start.")

        progress.done("switch", f"AUC {self.version} is running")
        self._tidy_up()
        return self._finish(
            "succeeded",
            f"AUC is now version {self.version}. Your data and settings were kept, and a backup "
            f"was taken first ({self.backup_zip.name}).",
        )

    def _recover_old(self, what_happened: str) -> int:
        """Before the switch: start the old version again, which nothing has changed."""
        self._discard_new()
        if self._start_and_wait(self.from_version):
            return self._finish(
                "failed",
                f"{what_happened} Nothing was changed: AUC {self.from_version} is running again.",
            )
        return self._finish(
            "broken",
            f"{what_happened} Nothing was changed, but AUC {self.from_version} did not start "
            f"again afterwards. {RESTART_HINT}",
        )

    def _roll_back(self, what_happened: str) -> int:
        """After the switch: point back at the old version, restore the database, start it."""
        self.progress.failed("switch", f"AUC {self.version} did not start")
        self.log.line(f"{what_happened} Going back to AUC {self.from_version}.")
        self._systemctl("stop", SERVICE_UNIT)
        point_current_at(self.install.apps_dir, self.old_dir)
        self.log.line(f"app/current points at {self.old_dir} again.")

        restored = False
        if self.backup_zip:
            try:
                restored = restore_database(self.backup_zip)
                self.log.line(f"The database was put back from {self.backup_zip}.")
            except Exception:  # noqa: BLE001
                self.log.line(traceback.format_exc())

        if not self._start_and_wait(self.from_version):
            return self._finish(
                "broken",
                f"{what_happened} AUC went back to version {self.from_version}, but that did not "
                f"start either. Your data is safe: a backup was taken first "
                f"({self.backup_zip}). {RESTART_HINT}",
            )
        self._discard_new()
        if restored:
            database = "Your database was put back exactly as it was before the update."
        else:
            database = (
                "The database could not be put back from the backup taken before the update "
                f"({self.backup_zip}), so check that your most recent work is there."
            )
        return self._finish(
            "rolled_back",
            f"{what_happened} AUC went back to version {self.from_version}, which is running "
            f"again. {database} The details are in the log.",
        )

    def _tidy_up(self) -> None:
        """After a successful switch. Nothing here can fail the update."""
        steps = (
            self._record_version,
            self._remove_older_versions,
            lambda: self.archive and _remove(self.archive),
        )
        for step in steps:
            try:
                step()
            except Exception as exc:  # noqa: BLE001
                self.log.line(f"While tidying up: {exc}")

    def _record_version(self) -> None:
        """Keep the installer's record true, so the installer still knows what is installed."""
        path = self.install.state_file
        if not path.is_file():
            return
        state = json.loads(path.read_text(encoding="utf-8"))
        state["version"] = self.version
        _write_json(path, state)
        self.log.line(f"Recorded version {self.version} in {path}.")

    def _remove_older_versions(self) -> None:
        """Keep the version we came from, as the installer does, and remove anything older."""
        keep = {self.new_dir.resolve(), self.old_dir}
        for entry in self.install.apps_dir.iterdir():
            if entry.is_symlink() or not entry.is_dir() or parse_version(entry.name) is None:
                continue
            if entry.resolve() in keep:
                continue
            shutil.rmtree(entry)
            self.log.line(f"Removed the older version {entry.name}.")


def _prune_logs(logs_dir: Path) -> None:
    for stale in sorted(logs_dir.glob("update-*.log"), reverse=True)[KEEP_UPDATE_LOGS:]:
        stale.unlink(missing_ok=True)


def run_update(install: Install, version: str, system: Optional[System] = None) -> int:
    """Update to `version`. Returns 0 if AUC is now running it."""
    system = system or SYSTEM
    install.logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log = Log(install.logs_dir / f"update-{stamp}.log")
    _prune_logs(install.logs_dir)
    progress = Progress(install.status_file, install.version, version, log.path)
    return _Update(install, version, system, progress, log).run()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Update AUC to a newer release. The Settings page starts this through systemd-run."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run_command = commands.add_parser("run", help="update to the given version")
    run_command.add_argument("--to", required=True, help="the version to update to, e.g. 1.2.0")
    commands.add_parser("check", help="print what is installed and what is newest")
    args = parser.parse_args(argv)

    install = detect_install()
    if args.command == "check":
        print(json.dumps(check_for_update(install), indent=2))
        return 0
    if not install.can_update:
        print(install.reason)
        if install.auc_home is not None:
            Progress(install.status_file, install.version, args.to, None).finish("failed", install.reason)
        return 1
    return run_update(install, args.to)


if __name__ == "__main__":
    raise SystemExit(main())
