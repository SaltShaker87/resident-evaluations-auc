"""
AUC — the Settings page's view of updates.

    GET  /api/update/check    what is installed, what is newest on GitHub, and what changed
    POST /api/update/start    start updating to the version the page showed
    GET  /api/update/status   how the update is going — answered by the new version, too,
                              once AUC has restarted

The work itself is in updater.py. These routes sit behind the app-wide login
check like every other /api/ route, so only someone logged in can start an
update.

The page asks /check every time it loads, so this keeps the answer for a day
and only asks GitHub again when it is that old, or when someone presses
"Check for updates".
"""

import threading
import time

import updater
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/update", tags=["update"])

CHECK_MAX_AGE_SECONDS = 24 * 3600
# A failed check is tried again sooner: the network may only have been down.
FAILED_CHECK_MAX_AGE_SECONDS = 15 * 60
# Pressing the button again and again still asks GitHub at most this often.
REFRESH_MIN_INTERVAL_SECONDS = 30

_cache_lock = threading.Lock()
_cache = {"at": 0.0, "result": None}


def clear_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, result=None)


class StartRequest(BaseModel):
    version: str


@router.get("/check")
def check_for_update(refresh: bool = False):
    install = updater.detect_install()
    now = time.monotonic()
    with _cache_lock:
        cached, age = _cache["result"], now - _cache["at"]
    if cached and cached["installed"] == install.version:
        if refresh:
            max_age = REFRESH_MIN_INTERVAL_SECONDS
        elif cached["error"]:
            max_age = FAILED_CHECK_MAX_AGE_SECONDS
        else:
            max_age = CHECK_MAX_AGE_SECONDS
        if age < max_age:
            # Whether this copy can update itself is worked out afresh: it is
            # cheap, and it changes (AUC restarted, the installer repaired it).
            return {**cached, "can_update": install.can_update, "reason": install.reason}

    result = updater.check_for_update(install)
    with _cache_lock:
        _cache.update(at=now, result=result)
    return result


@router.post("/start")
def start_update(data: StartRequest):
    install = updater.detect_install()
    if not install.can_update:
        raise HTTPException(status_code=409, detail=install.reason)
    if updater.read_status(install, check_running=True).get("state") == "running":
        raise HTTPException(status_code=409, detail="An update is already running.")

    # Asked again rather than trusting the page, which may have been open for
    # days: only the release GitHub calls the latest right now is installed.
    try:
        release = updater.latest_release()
    except updater.UpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if updater.parse_version(data.version) != updater.parse_version(release.version):
        raise HTTPException(
            status_code=409,
            detail=(
                f"The newest version is now {release.version}. Check for updates again to see "
                "what changed."
            ),
        )
    if not updater.is_newer(release.version, install.version):
        raise HTTPException(status_code=409, detail=f"AUC {install.version} is already up to date.")

    try:
        updater.launch(install, release.version)
    except updater.UpdateError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    clear_cache()
    return updater.read_status(install)


@router.get("/status")
def update_status():
    install = updater.detect_install()
    return updater.read_status(install, check_running=install.can_update)
