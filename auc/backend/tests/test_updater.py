"""Updating AUC from the Settings page.

Everything runs against a pretend installer install in a scratch folder, a
pretend GitHub (httpx.MockTransport) and a pretend systemd, so these tests
never reach the internet, never stop a real service and never see real data.

The pretend systemd is the important part. It starts whichever version
app/current points at, and can be told that a version will not start, which
is how the rollback is tested.
"""

import hashlib
import io
import json
import os
import sqlite3
import tarfile
import types
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import update_api
import updater

# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def release_files(version):
    """The files a release is made of — the ones the updater checks for, and
    the ones the ACGME index is built from."""
    return {
        "VERSION": f"{version}\n",
        "run.sh": "#!/bin/bash\n",
        "backend/app.py": f"# AUC {version}\n",
        "backend/config.py": "# config\n",
        "backend/requirements.txt": "fastapi\n",
        "backend/requirements-rag.txt": "chromadb\n",
        "backend/rag_retrieval.py": "# retrieval\n",
        "frontend/dist/index.html": f"<html>AUC {version}</html>\n",
        "nim/docker-compose.yml": "services: {}\n",
        "rag/build_index.py": "# build the index\n",
        "rag/documents/acgme_im_milestones.md": "# Milestones\n",
        "rag/ontology/acgme_ontology.json": "{}\n",
    }


def write_tree(root: Path, files):
    # Bytes, not text: on Windows write_text would turn \n into \r\n, and the
    # installed files would no longer match the same files in a download.
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))


def make_tarball(version, files=None, extra_members=()):
    """A .tar.gz shaped like a real release: everything inside one auc/ folder."""
    tree = release_files(version)
    tree.update(files or {})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        folder = tarfile.TarInfo("auc")
        folder.type = tarfile.DIRTYPE
        folder.mode = 0o755
        tar.addfile(folder)
        for name, content in sorted(tree.items()):
            if content is None:
                continue
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"auc/{name}")
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))
        for info, data in extra_members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


RELEASE_BODY = """## Which file do I need?

- **Ubuntu PC (Intel or AMD):** `AUC-Installer-*-linux-x86_64.deb`
- **Server or scripted install:** `auc-*.tar.gz` (verify with the `.sha256` file first).

When publishing this draft, leave it as a full release (not a pre-release) and set as latest, or the installer will not see it.

---

Automated release notes follow.

## What's Changed
* Add an advisors list, and an advisor dropdown on each resident by @SaltShaker87 in https://github.com/SaltShaker87/resident-evaluations-auc/pull/2

## New Contributors
* @someone made their first contribution in https://github.com/SaltShaker87/resident-evaluations-auc/pull/9

**Full Changelog**: https://github.com/SaltShaker87/resident-evaluations-auc/compare/v1.0.3...v1.0.4
"""


class FakeGitHub:
    """GitHub's releases API and download links, from memory."""

    def __init__(self):
        self.releases = []
        self.latest = None
        self.files = {}
        self.requests = []
        self.offline = False
        self.api_status = 200

    def publish(self, version, archive=None, *, body="", checksum=None, github_digest="auto",
                with_checksum=True, link_prefix=None, latest=True, draft=False, prerelease=False):
        tag = f"v{version}"
        prefix = link_prefix or f"{updater.DOWNLOAD_PREFIX}{tag}/"
        name = f"auc-{version}.tar.gz"
        assets = []
        if archive is not None:
            digest = hashlib.sha256(archive).hexdigest()
            self.files[prefix + name] = archive
            asset = {"name": name, "browser_download_url": prefix + name}
            if github_digest == "auto":
                asset["digest"] = f"sha256:{digest}"
            elif github_digest:
                asset["digest"] = f"sha256:{github_digest}"
            assets.append(asset)
            if with_checksum:
                self.files[prefix + name + ".sha256"] = f"{checksum or digest}  {name}\n".encode()
                assets.append({"name": name + ".sha256", "browser_download_url": prefix + name + ".sha256"})
        data = {
            "tag_name": tag,
            "html_url": f"https://github.com/{updater.REPO}/releases/tag/{tag}",
            "published_at": "2026-10-01T12:00:00Z",
            "body": body,
            "draft": draft,
            "prerelease": prerelease,
            "assets": assets,
        }
        self.releases.append(data)
        if latest:
            self.latest = data
        return data

    def handler(self, request):
        url = str(request.url)
        self.requests.append(url)
        if self.offline:
            raise httpx.ConnectError("no network", request=request)
        if url == f"{updater.RELEASES_API}/latest":
            if self.api_status != 200:
                return httpx.Response(self.api_status, json={"message": "API rate limit exceeded"})
            if self.latest is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=self.latest)
        if url.startswith(f"{updater.RELEASES_API}?"):
            return httpx.Response(200, json=list(reversed(self.releases)))
        if url in self.files:
            return httpx.Response(200, content=self.files[url])
        return httpx.Response(404)

    def asked_for_latest(self):
        return self.requests.count(f"{updater.RELEASES_API}/latest")


class FakeSystem:
    """systemd, uv and the running app, as far as the updater can tell."""

    def __init__(self, apps: Path, db: Path):
        self.apps = apps
        self.db = db
        self.calls = []
        self.running = "1.0.0"
        self.will_not_start = set()
        self.failing = []  # a command containing any of these fails
        self.stop_fails = False
        self.updater_active = True
        self.new_version_writes_to_db = False
        self.clock = 0.0

    def run(self, args, *, cwd=None, env=None, timeout=600):
        args = [str(a) for a in args]
        self.calls.append(("run", args, env))
        command = " ".join(args)
        if any(text in command for text in self.failing):
            return 1, f"{command}: failed"
        if args[0] == "systemd-run":
            return 0, ""
        if len(args) > 1 and args[1] == "venv":
            python = Path(args[-1]) / "bin" / "python"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
        if args[-1].endswith("build_index.py"):
            index = Path(args[-1]).parent / "chroma_db"
            index.mkdir(exist_ok=True)
            (index / "chroma.sqlite3").write_text("rebuilt", encoding="utf-8")
        return 0, "ok"

    def systemctl(self, *args):
        self.calls.append(("systemctl", list(args), None))
        action = args[0]
        if action == "stop":
            if self.stop_fails:
                return 1, "Failed to stop auc.service"
            self.running = None
            return 0, ""
        if action in ("start", "restart"):
            version = updater.read_version(updater.current_target(self.apps))
            if version != "1.0.0" and self.new_version_writes_to_db:
                # What a failed new version might leave behind: a migrated
                # table, and a write-ahead log beside the database.
                conn = sqlite3.connect(self.db)
                conn.execute("CREATE TABLE IF NOT EXISTS added_by_new_version (x)")
                conn.commit()
                conn.close()
                self.db.with_name(self.db.name + "-wal").write_bytes(b"not a real log")
            self.running = None if version in self.will_not_start else version
            return 0, ""
        if action == "is-active":
            return (0 if self.updater_active else 3), ""
        return 0, ""

    def answering_version(self, url):
        return self.running

    def sleep(self, seconds):
        self.clock += seconds

    def monotonic(self):
        return self.clock

    def actions(self):
        """Every systemctl action and program run, in order, as short words."""
        names = []
        for kind, args, _ in self.calls:
            if kind == "systemctl":
                names.append(f"systemctl {args[0]}")
            elif args[-1].endswith("build_index.py"):
                names.append("build_index")
            else:
                names.append(Path(args[0]).name + " " + args[1])
        return names


@dataclass
class Machine:
    auc_home: Path
    apps: Path
    old: Path
    data: Path
    db: Path
    config: Path
    install: updater.Install
    system: FakeSystem

    @property
    def backups(self):
        return self.auc_home / "backups"

    def residents(self):
        conn = sqlite3.connect(self.db)
        try:
            return [row[0] for row in conn.execute("SELECT name FROM residents")]
        finally:
            conn.close()

    def tables(self):
        conn = sqlite3.connect(self.db)
        try:
            return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()

    def current(self):
        return updater.current_target(self.apps)

    def state(self):
        return json.loads((self.config / "installer-state.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def links(monkeypatch, tmp_path):
    """Real symlinks where this computer allows them; a stand-in where it does not.

    Windows without developer mode refuses to make symlinks. The stand-in is a
    small file holding the target's path, which keeps every other part of the
    switch — the temporary name, the rename over the old link — the real code.
    """
    probe = tmp_path / "symlink-probe"
    try:
        os.symlink(tmp_path, probe, target_is_directory=True)
        probe.unlink()
        return "real"
    except OSError:
        pass

    def make(target, link):
        Path(link).write_text(str(target), encoding="utf-8")

    def read(link):
        try:
            return Path(Path(link).read_text(encoding="utf-8"))
        except OSError:
            return None

    monkeypatch.setattr(updater, "_make_link", make)
    monkeypatch.setattr(updater, "_read_link", read)
    return "stand-in"


@pytest.fixture(autouse=True)
def fresh_cache():
    update_api.clear_cache()
    yield
    update_api.clear_cache()


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(updater, "HTTP_TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """An installer install of AUC 1.0.0, with residents in its database."""
    home = tmp_path / "home"
    auc_home = home / ".local" / "share" / "auc"
    apps = auc_home / "app"

    old = apps / "1.0.0"
    write_tree(old, release_files("1.0.0"))
    write_tree(old, {"backend/venv/bin/python": "", "rag/chroma_db/chroma.sqlite3": "the index"})
    # An older version still on disk, which a successful update tidies away.
    write_tree(apps / "0.9.0", release_files("0.9.0"))
    updater.point_current_at(apps, old)
    write_tree(auc_home, {"tools/uv": ""})

    data = auc_home / "data"
    write_tree(data, {"photos/ada.jpg": "a photo"})
    db = data / "auc.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE residents (name TEXT)")
    conn.execute("INSERT INTO residents VALUES ('Ada Lovelace')")
    conn.commit()
    conn.close()

    config_dir = home / ".config" / "auc"
    write_tree(config_dir, {
        "auc.env": "AUC_PORT=3000\nOLLAMA_MODEL=qwen3.5:9b\n",
        "installer-state.json": json.dumps({
            "schema": 1,
            "version": "1.0.0",
            "installed_at": "2026-09-12T23:40:11Z",
            "choices": {
                "ai_enabled": True,
                "model": "qwen3.5:9b",
                "nemotron_enabled": True,
                "network_scope": "local",
            },
            "nemotron_pending": False,
            "adopted_from": None,
        }),
    })
    monkeypatch.setenv("AUC_CONFIG_HOME", str(config_dir))

    monkeypatch.setattr(updater.backup_helper, "DB_PATH", db)
    monkeypatch.setattr(updater.backup_helper, "PHOTOS_DIR", data / "photos")
    monkeypatch.setattr(updater.config, "BACKUP_DIR", "")
    monkeypatch.setattr(updater, "MIN_FREE_BYTES", 0)
    monkeypatch.setattr(updater, "chromadb_installed_now", lambda: True)

    install = updater.detect_install(
        app_dir=old, environ={"INVOCATION_ID": "test"}, which=lambda name: f"/usr/bin/{name}"
    )
    assert install.can_update, install.reason

    system = FakeSystem(apps, db)
    monkeypatch.setattr(updater, "SYSTEM", system)
    return Machine(auc_home, apps, old, data, db, config_dir, install, system)


def update(machine, version="1.1.0"):
    code = updater.run_update(machine.install, version, system=machine.system)
    status = updater.read_status(machine.install)
    return code, status


# ---------------------------------------------------------------------------
# Version numbers and release notes
# ---------------------------------------------------------------------------


def test_versions_are_compared_as_numbers_not_text():
    assert updater.is_newer("1.10.0", "1.9.9")
    assert updater.is_newer("v1.0.5", "1.0.4")
    assert not updater.is_newer("1.0.4", "1.0.4"), "the same version is not an update"
    assert not updater.is_newer("1.0.3", "1.0.4"), "never a downgrade"
    assert not updater.is_newer("1.1.0", "dev"), "a development copy has nothing to compare"
    assert updater.parse_version("latest") is None


def test_release_notes_keep_the_changes_and_drop_the_boilerplate():
    assert updater.changes_from_notes(RELEASE_BODY) == [
        "Add an advisors list, and an advisor dropdown on each resident",
    ]


def test_release_notes_with_no_list_of_changes_come_back_empty():
    body = "## Which file do I need?\n\n- the .deb\n\n---\n\nAutomated release notes follow.\n\n**Full Changelog**: https://example.org"
    assert updater.changes_from_notes(body) == []
    assert updater.changes_from_notes("First release!\n\n**Full Changelog**: x") == ["First release!"]
    assert updater.changes_from_notes(None) == []


# ---------------------------------------------------------------------------
# What is accepted as a release
# ---------------------------------------------------------------------------


def test_a_tag_that_is_not_a_version_number_is_refused(github):
    data = github.publish("1.1.0", make_tarball("1.1.0"))
    data["tag_name"] = "nightly"
    with pytest.raises(updater.UpdateError, match="not a version number"):
        updater.release_from_json(data)


def test_a_release_without_its_fingerprint_file_is_refused(github):
    data = github.publish("1.1.0", make_tarball("1.1.0"), with_checksum=False)
    with pytest.raises(updater.UpdateError, match="fingerprint file"):
        updater.release_from_json(data)


def test_a_download_link_outside_this_projects_releases_is_refused(github):
    data = github.publish("1.1.0", make_tarball("1.1.0"), link_prefix="https://example.org/files/")
    with pytest.raises(updater.UpdateError, match="somewhere other than"):
        updater.release_from_json(data)


def test_drafts_and_pre_releases_are_refused(github):
    for flags in ({"draft": True}, {"prerelease": True}):
        data = github.publish("1.1.0", make_tarball("1.1.0"), **flags)
        with pytest.raises(updater.UpdateError, match="not a finished release"):
            updater.release_from_json(data)


# ---------------------------------------------------------------------------
# Checking for updates
# ---------------------------------------------------------------------------


def test_a_check_offers_the_newer_release_and_everything_that_changed_since(machine, github):
    github.publish("0.9.5", body="## What's Changed\n* Too old to list by @a in https://x/1", latest=False)
    github.publish("1.0.5", body="## What's Changed\n* Fixed the PDF footer by @a in https://x/2", latest=False)
    github.publish("1.2.0", body="## What's Changed\n* An unfinished draft by @a in https://x/3",
                   latest=False, draft=True)
    github.publish("1.1.0", make_tarball("1.1.0"), body=RELEASE_BODY)

    result = updater.check_for_update(machine.install)

    assert result["installed"] == "1.0.0"
    assert result["latest"] == "1.1.0"
    assert result["available"] is True
    assert result["can_update"] is True
    assert result["error"] is None
    assert [n["version"] for n in result["notes"]] == ["1.1.0", "1.0.5"], "newest first, nothing older or unfinished"
    assert result["notes"][1]["changes"] == ["Fixed the PDF footer"]


def test_a_check_with_nothing_newer_says_up_to_date(machine, github):
    github.publish("1.0.0", make_tarball("1.0.0"))
    result = updater.check_for_update(machine.install)
    assert result["available"] is False
    assert result["latest"] == "1.0.0"
    assert result["error"] is None


def test_a_check_without_internet_is_a_message_not_a_crash(machine, github):
    github.offline = True
    result = updater.check_for_update(machine.install)
    assert result["available"] is False
    assert "could not reach GitHub" in result["error"]


def test_a_check_turned_away_by_github_says_to_try_later(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"))
    github.api_status = 403
    result = updater.check_for_update(machine.install)
    assert "Try again later" in result["error"]


# ---------------------------------------------------------------------------
# Which copies of AUC can update themselves
# ---------------------------------------------------------------------------


def test_this_git_checkout_cannot_update_itself_and_says_how_to():
    install = updater.detect_install()
    assert install.version == "dev"
    assert not install.can_update
    assert "git pull" in install.reason


def test_an_installer_install_running_as_a_service_can_update_itself(machine):
    assert machine.install.can_update
    assert machine.install.auc_home == machine.auc_home


def test_a_copy_not_running_as_a_service_cannot_restart_itself(machine):
    install = updater.detect_install(app_dir=machine.old, environ={}, which=lambda name: f"/usr/bin/{name}")
    assert not install.can_update
    assert "background service" in install.reason


def test_a_copy_missing_the_installers_python_tool_says_to_repair(machine):
    (machine.auc_home / "tools" / "uv").unlink()
    install = updater.detect_install(
        app_dir=machine.old, environ={"INVOCATION_ID": "x"}, which=lambda name: f"/usr/bin/{name}"
    )
    assert not install.can_update
    assert "Repair" in install.reason


def test_a_version_folder_that_is_not_the_one_installed_cannot_update(machine):
    install = updater.detect_install(
        app_dir=machine.apps / "0.9.0", environ={"INVOCATION_ID": "x"}, which=lambda name: f"/usr/bin/{name}"
    )
    assert not install.can_update
    assert "Restart AUC" in install.reason


# ---------------------------------------------------------------------------
# Unpacking a download
# ---------------------------------------------------------------------------


def _member(name, kind=tarfile.REGTYPE, linkname=""):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    data = b"x" if kind == tarfile.REGTYPE else None
    info.size = len(data) if data else 0
    return info, data


@pytest.mark.parametrize("bad", [
    _member("auc/../escaped.txt"),
    _member("/tmp/absolute.txt"),
    _member("elsewhere/file.txt"),
    _member("auc/link-to-passwords", tarfile.SYMTYPE, "/etc/passwd"),
    _member("auc/hard-link", tarfile.LNKTYPE, "auc/VERSION"),
])
def test_an_archive_with_anything_but_ordinary_files_inside_auc_is_refused(tmp_path, bad):
    archive = tmp_path / "auc-1.1.0.tar.gz"
    archive.write_bytes(make_tarball("1.1.0", extra_members=[bad]))
    apps = tmp_path / "app"
    apps.mkdir()

    with pytest.raises(updater.UpdateError, match="was not used"):
        updater.unpack_release(archive, apps, "1.1.0")

    assert not (tmp_path / "escaped.txt").exists()
    assert list(apps.iterdir()) == [], "nothing is left behind"


def test_an_archive_missing_files_every_release_has_is_refused(tmp_path):
    archive = tmp_path / "auc-1.1.0.tar.gz"
    archive.write_bytes(make_tarball("1.1.0", files={"frontend/dist/index.html": None}))
    with pytest.raises(updater.UpdateError, match="frontend/dist/index.html"):
        updater.unpack_release(archive, tmp_path, "1.1.0")


def test_an_archive_that_says_it_is_another_version_is_refused(tmp_path):
    archive = tmp_path / "auc-1.1.0.tar.gz"
    archive.write_bytes(make_tarball("1.1.0", files={"VERSION": "9.9.9\n"}))
    with pytest.raises(updater.UpdateError, match="9.9.9"):
        updater.unpack_release(archive, tmp_path, "1.1.0")


def test_a_good_archive_lands_in_its_own_version_folder(tmp_path):
    archive = tmp_path / "auc-1.1.0.tar.gz"
    archive.write_bytes(make_tarball("1.1.0"))
    target = updater.unpack_release(archive, tmp_path, "1.1.0")
    assert target == tmp_path / "1.1.0"
    assert (target / "backend" / "app.py").read_text(encoding="utf-8") == "# AUC 1.1.0\n"
    assert not (target / "auc").exists(), "the auc/ wrapper is dropped"
    assert not (tmp_path / ".staging-1.1.0").exists()


# ---------------------------------------------------------------------------
# The whole update
# ---------------------------------------------------------------------------


def test_a_successful_update_switches_over_and_keeps_the_data(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"), body=RELEASE_BODY)
    env_before = (machine.config / "auc.env").read_bytes()

    code, status = update(machine)

    assert code == 0, status["message"]
    assert status["state"] == "succeeded"
    assert "AUC is now version 1.1.0" in status["message"]
    assert all(step["status"] == "done" for step in status["steps"]), status["steps"]
    assert machine.current() == (machine.apps / "1.1.0").resolve()
    assert machine.system.running == "1.1.0"

    # The data is where it was, untouched.
    assert machine.residents() == ["Ada Lovelace"]
    assert (machine.data / "photos" / "ada.jpg").read_text(encoding="utf-8") == "a photo"
    assert (machine.config / "auc.env").read_bytes() == env_before

    # A backup was taken, and it holds the database and the photos.
    backups = list(machine.backups.glob("auc-pre-update-1.0.0-to-1.1.0-*.zip"))
    assert len(backups) == 1
    import zipfile
    with zipfile.ZipFile(backups[0]) as archive:
        assert set(archive.namelist()) == {"auc.db", "photos/ada.jpg"}

    # The ACGME index came across, since nothing it is built from changed.
    assert (machine.apps / "1.1.0" / "rag" / "chroma_db" / "chroma.sqlite3").read_text(encoding="utf-8") == "the index"
    assert "build_index" not in machine.system.actions()

    # The installer still knows the truth, and its other records are kept.
    assert machine.state()["version"] == "1.1.0"
    assert machine.state()["choices"]["nemotron_enabled"] is True

    # The version we came from is kept for a rollback; anything older is not.
    assert (machine.apps / "1.0.0").is_dir()
    assert not (machine.apps / "0.9.0").exists()
    assert not list(machine.apps.glob(".download-*")), "the download is tidied away"
    assert Path(status["log_file"]).is_file()


def test_the_current_version_keeps_running_until_the_new_one_is_ready(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0", files={"rag/documents/acgme_im_milestones.md": "# New\n"}))

    code, status = update(machine)

    assert code == 0, status["message"]
    actions = machine.system.actions()
    stop = actions.index("systemctl stop")
    assert actions[:stop] == [
        "uv python", "uv venv", "uv pip", "uv pip", "build_index",
    ], "everything slow happens while the old version is still serving"
    assert actions[stop + 1:] == ["systemctl restart"]
    # And the Python tools are pointed at the installer's own Python.
    uv_env = next(env for kind, args, env in machine.system.calls if kind == "run" and args[1] == "venv")
    assert uv_env["UV_PYTHON_INSTALL_DIR"] == str(machine.auc_home / "tools" / "python")


def test_a_download_that_does_not_match_its_fingerprint_changes_nothing(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"), checksum="0" * 64)

    code, status = update(machine)

    assert code == 1
    assert status["state"] == "failed"
    assert "does not match the fingerprint" in status["message"]
    assert "Nothing was changed" in status["message"]
    assert [s["status"] for s in status["steps"]][:2] == ["done", "failed"]
    assert machine.current() == machine.old.resolve()
    assert "systemctl stop" not in machine.system.actions(), "AUC was never paused"
    assert not (machine.apps / "1.1.0").exists()
    assert not machine.backups.exists()


def test_a_download_that_does_not_match_githubs_own_record_is_refused(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"), github_digest="1" * 64)

    code, status = update(machine)

    assert code == 1
    assert "fingerprint GitHub recorded" in status["message"]
    assert machine.current() == machine.old.resolve()


def test_a_new_version_that_will_not_start_is_undone_and_the_database_put_back(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"))
    machine.system.will_not_start = {"1.1.0"}
    machine.system.new_version_writes_to_db = True

    code, status = update(machine)

    assert code == 1
    assert status["state"] == "rolled_back"
    assert "went back to version 1.0.0" in status["message"]
    assert "put back exactly as it was" in status["message"]
    assert machine.current() == machine.old.resolve()
    assert machine.system.running == "1.0.0"
    assert machine.residents() == ["Ada Lovelace"]
    assert "added_by_new_version" not in machine.tables(), "the failed version's changes are gone"
    assert not machine.db.with_name("auc.db-wal").exists()
    assert not (machine.apps / "1.1.0").exists()
    assert machine.state()["version"] == "1.0.0", "the installer's record is unchanged"


def test_when_neither_version_starts_the_message_says_what_to_do(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"))
    machine.system.will_not_start = {"1.1.0", "1.0.0"}

    code, status = update(machine)

    assert code == 1
    assert status["state"] == "broken"
    assert "systemctl --user restart auc" in status["message"]
    assert "a backup was taken first" in status["message"]


def test_a_disk_that_fills_up_mid_switch_does_not_stop_the_rollback(machine, github, monkeypatch):
    """Once the switch has begun, neither the progress file nor the log can be
    written. The rollback must still happen: what the page shows is never what
    an update depends on."""
    github.publish("1.1.0", make_tarball("1.1.0"))
    machine.system.will_not_start = {"1.1.0"}
    machine.system.new_version_writes_to_db = True

    disk_full = {"now": False}
    real_write = updater._write_json
    real_point = updater.point_current_at

    def write(path, data):
        if disk_full["now"]:
            raise OSError("No space left on device")
        return real_write(path, data)

    def point(apps, target):
        real_point(apps, target)
        disk_full["now"] = True

    real_open = open

    def guarded_open(path, *args, **kwargs):
        if disk_full["now"] and str(path).endswith(".log"):
            raise OSError("No space left on device")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(updater, "_write_json", write)
    monkeypatch.setattr(updater, "point_current_at", point)
    monkeypatch.setattr("builtins.open", guarded_open)

    code = updater.run_update(machine.install, "1.1.0", system=machine.system)

    monkeypatch.setattr("builtins.open", real_open)
    assert code == 1
    assert machine.current() == machine.old.resolve()
    assert machine.system.running == "1.0.0"
    assert machine.residents() == ["Ada Lovelace"]
    assert "added_by_new_version" not in machine.tables()


def test_a_failed_backup_stops_the_update_and_restarts_the_current_version(machine, github, monkeypatch):
    github.publish("1.1.0", make_tarball("1.1.0"))

    def no_disk(_path):
        raise OSError("No space left on device")

    monkeypatch.setattr(updater.backup_helper, "make_backup_zip", no_disk)

    code, status = update(machine)

    assert code == 1
    assert status["state"] == "failed"
    assert "backup before updating failed" in status["message"]
    assert "AUC 1.0.0 is running again" in status["message"]
    assert machine.current() == machine.old.resolve()
    assert machine.system.running == "1.0.0"
    assert not (machine.apps / "1.1.0").exists()


def test_if_auc_cannot_be_paused_nothing_is_switched(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"))
    machine.system.stop_fails = True

    code, status = update(machine)

    assert code == 1
    assert status["state"] == "failed"
    assert machine.current() == machine.old.resolve()
    assert not machine.backups.exists()


def test_packages_that_will_not_install_change_nothing(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"))
    machine.system.failing = ["-r requirements.txt"]

    code, status = update(machine)

    assert code == 1
    assert "would not install" in status["message"]
    assert "systemctl stop" not in machine.system.actions()
    assert not (machine.apps / "1.1.0").exists()


def test_index_packages_that_will_not_install_cancel_the_update_if_they_work_now(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"))
    machine.system.failing = ["requirements-rag.txt"]

    code, status = update(machine)

    assert code == 1
    assert "chromadb" in status["message"]
    assert machine.current() == machine.old.resolve()


def test_index_packages_that_did_not_work_before_either_do_not_hold_the_update_up(machine, github, monkeypatch):
    github.publish("1.1.0", make_tarball("1.1.0"))
    machine.system.failing = ["requirements-rag.txt"]
    monkeypatch.setattr(updater, "chromadb_installed_now", lambda: False)

    code, status = update(machine)

    assert code == 0, status["message"]
    assert status["warnings"], "the missing index packages are still mentioned"


def test_an_index_that_cannot_be_rebuilt_cancels_the_update(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0", files={"rag/ontology/acgme_ontology.json": '{"new": 1}\n'}))
    machine.system.failing = ["build_index.py"]

    code, status = update(machine)

    assert code == 1
    assert "index could not be rebuilt" in status["message"]
    assert "systemctl stop" not in machine.system.actions()


def test_a_change_to_how_nemotron_starts_is_left_to_the_installer(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0", files={"nim/docker-compose.yml": "services: {new: {}}\n"}))

    code, status = update(machine)

    assert code == 1
    assert "auc-installer --headless update" in status["message"]
    assert "uv venv" not in machine.system.actions(), "stopped before minutes were spent on Python"


def test_a_change_to_nemotron_does_not_matter_where_nemotron_is_not_used(machine, github):
    state = machine.state()
    state["choices"]["nemotron_enabled"] = False
    (machine.config / "installer-state.json").write_text(json.dumps(state), encoding="utf-8")
    github.publish("1.1.0", make_tarball("1.1.0", files={"nim/docker-compose.yml": "services: {new: {}}\n"}))

    code, status = update(machine)

    assert code == 0, status["message"]


def test_the_release_must_still_be_the_newest_when_the_update_runs(machine, github):
    github.publish("1.1.0", make_tarball("1.1.0"))

    code, status = update(machine, version="1.0.9")

    assert code == 1
    assert "newest release is now 1.1.0" in status["message"]


def test_too_little_disk_space_stops_the_update_before_it_downloads(machine, github, monkeypatch):
    github.publish("1.1.0", make_tarball("1.1.0"))
    monkeypatch.setattr(updater, "MIN_FREE_BYTES", 2 * 10**9)
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda _path: types.SimpleNamespace(free=10**8))

    code, status = update(machine)

    assert code == 1
    assert "not enough free disk space" in status["message"]
    assert not any(url.endswith(".tar.gz") for url in github.requests)


def test_only_the_newest_five_pre_update_backups_are_kept(machine, github):
    machine.backups.mkdir()
    for i in range(6):
        old = machine.backups / f"auc-pre-update-0.0.{i}-to-0.0.{i + 1}-2026-01-0{i + 1}_000000.zip"
        old.write_bytes(b"old")
        os.utime(old, (1_700_000_000 + i, 1_700_000_000 + i))
    nightly = machine.backups / "auc-backup-2026-01-01_020000.zip"
    nightly.write_bytes(b"nightly")
    github.publish("1.1.0", make_tarball("1.1.0"))

    code, _ = update(machine)

    assert code == 0
    kept = sorted(p.name for p in machine.backups.glob("auc-pre-update-*.zip"))
    assert len(kept) == 5
    assert any("1.0.0-to-1.1.0" in name for name in kept)
    assert nightly.exists(), "the nightly backups are not this cleanup's business"


def test_an_update_that_died_part_way_is_reported_as_interrupted(machine):
    updater.Progress(machine.install.status_file, "1.0.0", "1.1.0", None)
    machine.system.updater_active = False

    status = updater.read_status(machine.install, check_running=True, system=machine.system)

    assert status["state"] == "interrupted"
    assert "try the update again" in status["message"]
    assert updater.read_status(machine.install)["state"] == "interrupted", "and it is remembered"


# ---------------------------------------------------------------------------
# The web routes
# ---------------------------------------------------------------------------


def test_the_update_routes_need_a_login(client):
    assert client.get("/api/update/check").status_code == 401
    assert client.get("/api/update/status").status_code == 401
    assert client.post("/api/update/start", json={"version": "9.9.9"}).status_code == 401


def test_the_check_route_asks_github_once_and_then_remembers(logged_in, machine, github, monkeypatch):
    monkeypatch.setattr(updater, "detect_install", lambda *a, **k: machine.install)
    github.publish("1.1.0", make_tarball("1.1.0"), body=RELEASE_BODY)

    first = logged_in.get("/api/update/check").json()
    second = logged_in.get("/api/update/check").json()

    assert first["available"] is True
    assert first["notes"][0]["changes"] == ["Add an advisors list, and an advisor dropdown on each resident"]
    assert second == first
    assert github.asked_for_latest() == 1, "the page asks on every load; GitHub is asked once a day"


def test_starting_refuses_a_copy_that_cannot_update_itself(logged_in, monkeypatch):
    install = updater.Install(app_dir=Path("/somewhere"), version="dev", reason="From a git checkout.")
    monkeypatch.setattr(updater, "detect_install", lambda *a, **k: install)

    response = logged_in.post("/api/update/start", json={"version": "1.1.0"})

    assert response.status_code == 409
    assert response.json()["detail"] == "From a git checkout."


def test_starting_refuses_a_version_that_is_no_longer_the_newest(logged_in, machine, github, monkeypatch):
    monkeypatch.setattr(updater, "detect_install", lambda *a, **k: machine.install)
    github.publish("1.2.0", make_tarball("1.2.0"))

    response = logged_in.post("/api/update/start", json={"version": "1.1.0"})

    assert response.status_code == 409
    assert "1.2.0" in response.json()["detail"]


def test_starting_refuses_while_an_update_is_running(logged_in, machine, github, monkeypatch):
    monkeypatch.setattr(updater, "detect_install", lambda *a, **k: machine.install)
    github.publish("1.1.0", make_tarball("1.1.0"))
    updater.Progress(machine.install.status_file, "1.0.0", "1.1.0", None)

    response = logged_in.post("/api/update/start", json={"version": "1.1.0"})

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


def test_starting_runs_the_update_as_its_own_systemd_job(logged_in, machine, github, monkeypatch):
    monkeypatch.setattr(updater, "detect_install", lambda *a, **k: machine.install)
    monkeypatch.setenv("MEDHUB_API_KEY", "a-secret-key")
    monkeypatch.setenv("AUC_PORT", "3000")
    github.publish("1.1.0", make_tarball("1.1.0"))

    response = logged_in.post("/api/update/start", json={"version": "1.1.0"})

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "running"
    assert response.json()["to_version"] == "1.1.0"

    launched = next(args for kind, args, _ in machine.system.calls if kind == "run" and args[0] == "systemd-run")
    assert "--user" in launched
    assert f"--unit={updater.UPDATER_UNIT}" in launched
    assert launched[-4:] == [str(machine.old / "backend" / "updater.py"), "run", "--to", "1.1.0"]
    passed = [arg for arg in launched if arg.startswith("--setenv=")]
    assert "--setenv=AUC_PORT" in passed
    assert all(arg.count("=") == 1 for arg in passed), "names only: values never reach a command line"
    assert not any("MEDHUB" in arg or "a-secret-key" in arg for arg in launched)


def test_a_job_that_will_not_start_is_reported_and_recorded(logged_in, machine, github, monkeypatch):
    monkeypatch.setattr(updater, "detect_install", lambda *a, **k: machine.install)
    github.publish("1.1.0", make_tarball("1.1.0"))
    machine.system.failing = ["systemd-run"]

    response = logged_in.post("/api/update/start", json={"version": "1.1.0"})

    assert response.status_code == 500
    assert "could not be started" in response.json()["detail"]
    assert updater.read_status(machine.install)["state"] == "failed"


def test_the_status_route_reports_the_last_update(logged_in, machine, monkeypatch):
    monkeypatch.setattr(updater, "detect_install", lambda *a, **k: machine.install)
    assert logged_in.get("/api/update/status").json() == {"state": "idle"}

    log = updater.Log(machine.install.logs_dir / "update-2026-10-20_120000.log")
    log.line("Downloading")
    progress = updater.Progress(machine.install.status_file, "1.0.0", "1.1.0", log.path)
    progress.finish("failed", "The download stopped part way through.")

    body = logged_in.get("/api/update/status").json()
    assert body["state"] == "failed"
    assert body["message"] == "The download stopped part way through."
    assert "Downloading" in body["log_tail"]
