"""
AUC — Authentication module.

Single shared password protecting the whole app, with:
- scrypt password hashing (Python stdlib, no extra dependencies)
- server-side sessions stored in SQLite, delivered via an HttpOnly cookie
- a one-time recovery key for password resets
- throttling of repeated failed login/recovery attempts
"""

import hashlib
import hmac
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE = "auc_session"
SESSION_SECONDS = 30 * 24 * 3600  # 30 days
MIN_PASSWORD_LENGTH = 8

# Failed-attempt throttling: after this many consecutive failures, each
# further attempt is locked out for an exponentially growing delay.
THROTTLE_FREE_ATTEMPTS = 5
THROTTLE_MAX_DELAY = 300  # seconds

# Set by app.py via init_auth() before the app starts serving requests.
_db_connection = None


def init_auth(db_connection_factory):
    """Receive the database connection context manager from app.py."""
    global _db_connection
    _db_connection = db_connection_factory


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def _hash_secret(secret: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=64
    )


def _verify_secret(secret: str, salt_hex: str, hash_hex: str) -> bool:
    expected = bytes.fromhex(hash_hex)
    actual = _hash_secret(secret, bytes.fromhex(salt_hex))
    return hmac.compare_digest(actual, expected)


def _new_recovery_key() -> str:
    raw = secrets.token_hex(8).upper()  # 16 hex characters / 64 bits
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def _normalize_recovery_key(key: str) -> str:
    return key.replace("-", "").replace(" ", "").strip().upper()


def _set_credentials(conn, password: str) -> str:
    """Store a new password + recovery key pair. Returns the recovery key."""
    pw_salt = secrets.token_bytes(16)
    pw_hash = _hash_secret(password, pw_salt)
    recovery_key = _new_recovery_key()
    rk_salt = secrets.token_bytes(16)
    rk_hash = _hash_secret(_normalize_recovery_key(recovery_key), rk_salt)
    conn.execute(
        """INSERT INTO auth_config (id, password_hash, password_salt, recovery_hash, recovery_salt)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             password_hash = excluded.password_hash,
             password_salt = excluded.password_salt,
             recovery_hash = excluded.recovery_hash,
             recovery_salt = excluded.recovery_salt,
             updated_at = datetime('now')""",
        (pw_hash.hex(), pw_salt.hex(), rk_hash.hex(), rk_salt.hex()),
    )
    return recovery_key


def _get_auth_config(conn):
    return conn.execute("SELECT * FROM auth_config WHERE id = 1").fetchone()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_session(conn) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.execute(
        "INSERT INTO sessions (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
        (_token_hash(token), now, now + SESSION_SECONDS),
    )
    return token


def _set_session_cookie(response: Response, token: str):
    # Secure flag intentionally omitted: the app currently runs over plain
    # HTTP on a trusted network. Revisit when HTTPS is added (see SECURITY.md).
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


def is_authenticated(request: Request) -> bool:
    """Used by the app-wide middleware to gate /api/* routes."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    with _db_connection() as conn:
        row = conn.execute(
            "SELECT expires_at FROM sessions WHERE token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
    return bool(row and row["expires_at"] > time.time())


# ---------------------------------------------------------------------------
# Failed-attempt throttling (in-memory, global — the app has one password)
# ---------------------------------------------------------------------------

_throttle_lock = threading.Lock()
_failed_attempts = 0
_locked_until = 0.0


def _check_throttle():
    with _throttle_lock:
        remaining = _locked_until - time.time()
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {int(remaining) + 1} seconds.",
        )


def _record_failure():
    global _failed_attempts, _locked_until
    with _throttle_lock:
        _failed_attempts += 1
        if _failed_attempts >= THROTTLE_FREE_ATTEMPTS:
            delay = min(2 ** (_failed_attempts - THROTTLE_FREE_ATTEMPTS + 1), THROTTLE_MAX_DELAY)
            _locked_until = time.time() + delay


def _record_success():
    global _failed_attempts, _locked_until
    with _throttle_lock:
        _failed_attempts = 0
        _locked_until = 0.0


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SetupRequest(BaseModel):
    password: str


class LoginRequest(BaseModel):
    password: str


class RecoverRequest(BaseModel):
    recovery_key: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def _validate_new_password(password: str):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
def auth_status(request: Request):
    with _db_connection() as conn:
        configured = _get_auth_config(conn) is not None
    return {
        "setup_required": not configured,
        "authenticated": configured and is_authenticated(request),
    }


@router.post("/setup")
def setup_password(data: SetupRequest, response: Response):
    _validate_new_password(data.password)
    with _db_connection() as conn:
        if _get_auth_config(conn) is not None:
            raise HTTPException(status_code=409, detail="A password is already set.")
        recovery_key = _set_credentials(conn, data.password)
        token = _create_session(conn)
    _set_session_cookie(response, token)
    return {"message": "Password created", "recovery_key": recovery_key}


@router.post("/login")
def login(data: LoginRequest, response: Response):
    _check_throttle()
    with _db_connection() as conn:
        config = _get_auth_config(conn)
        if config is None:
            raise HTTPException(status_code=409, detail="No password set yet — complete setup first.")
        if not _verify_secret(data.password, config["password_salt"], config["password_hash"]):
            _record_failure()
            raise HTTPException(status_code=401, detail="Incorrect password.")
        _record_success()
        token = _create_session(conn)
    _set_session_cookie(response, token)
    return {"message": "Logged in"}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with _db_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"message": "Logged out"}


@router.post("/recover")
def recover_password(data: RecoverRequest, response: Response):
    _check_throttle()
    _validate_new_password(data.new_password)
    with _db_connection() as conn:
        config = _get_auth_config(conn)
        if config is None:
            raise HTTPException(status_code=409, detail="No password set yet — complete setup first.")
        supplied = _normalize_recovery_key(data.recovery_key)
        if not _verify_secret(supplied, config["recovery_salt"], config["recovery_hash"]):
            _record_failure()
            raise HTTPException(status_code=401, detail="Recovery key is incorrect.")
        _record_success()
        new_recovery_key = _set_credentials(conn, data.new_password)
        # Log every device out, then log this one back in.
        conn.execute("DELETE FROM sessions")
        token = _create_session(conn)
    _set_session_cookie(response, token)
    return {"message": "Password reset", "recovery_key": new_recovery_key}


@router.post("/change-password")
def change_password(data: ChangePasswordRequest, request: Request, response: Response):
    # /api/auth/* is exempt from the app-wide gate, so check explicitly here.
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    _validate_new_password(data.new_password)
    with _db_connection() as conn:
        config = _get_auth_config(conn)
        if config is None or not _verify_secret(
            data.current_password, config["password_salt"], config["password_hash"]
        ):
            raise HTTPException(status_code=401, detail="Current password is incorrect.")
        new_recovery_key = _set_credentials(conn, data.new_password)
        conn.execute("DELETE FROM sessions")
        token = _create_session(conn)
    _set_session_cookie(response, token)
    return {"message": "Password changed", "recovery_key": new_recovery_key}
