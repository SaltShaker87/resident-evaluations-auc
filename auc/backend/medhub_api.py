"""
MedHub API Integration Framework
=================================
This module is a STUB — it provides the correct structure but the actual
API calls need to be filled in once MedHub API documentation is obtained.

WHAT YOU WILL NEED TO FILL IN
------------------------------
1. BASE URL
   Set MEDHUB_API_URL in config.py (or via environment variable).
   Typical format: https://your-institution.medhub.com/api/v1

2. AUTHENTICATION
   MedHub typically uses one of:
     a) Bearer token:  Authorization: Bearer <token>
     b) API key header: X-Api-Key: <key>
     c) Basic auth with client_id / client_secret
   Set MEDHUB_API_KEY in config.py once you know the method.
   Update the `authenticate()` function below accordingly.

3. ENDPOINTS TO CALL
   Once you have the API docs, identify:
     - GET /evaluations  (or similar) — returns evaluation records
       Expected fields: resident name/id, evaluator name, rotation,
       competency domain, score, comments, evaluation date
     - Pagination parameters (page, limit, offset, etc.)
     - Date range filters to limit what is fetched

4. RESIDENT MATCHING
   The sync uses first+last name matching by default. If the API returns
   a MedHub-specific resident ID you can cross-reference, add that mapping
   here for more reliable matching.

TESTING
-------
Once credentials are available:
  1. Fill in MEDHUB_API_URL and MEDHUB_API_KEY in config.py
  2. Implement `fetch_evaluations()` to call the real endpoint
  3. Run a manual test: python -c "import medhub_api; print(medhub_api.is_configured())"
  4. Use the "Sync from MedHub API" button on the MedHub Import page
"""

import sqlite3
import uuid
from typing import Optional

import httpx

from config import MEDHUB_API_URL, MEDHUB_API_KEY


def is_configured() -> bool:
    """Return True only if both URL and key/token are set."""
    return bool(MEDHUB_API_URL.strip()) and bool(MEDHUB_API_KEY.strip())


def authenticate() -> str:
    """
    Obtain an auth token from MedHub.

    TODO: Replace this stub with the real authentication call once
    API documentation is available. Common patterns:

      # Bearer token (already have it):
      return MEDHUB_API_KEY

      # OAuth2 client credentials:
      resp = httpx.post(f"{MEDHUB_API_URL}/oauth/token", data={
          "grant_type": "client_credentials",
          "client_id": MEDHUB_API_KEY,
          "client_secret": MEDHUB_API_SECRET,
      })
      return resp.json()["access_token"]
    """
    if not is_configured():
        raise RuntimeError("MedHub API credentials are not configured. See config.py.")
    # Placeholder: treat the API key as a bearer token directly.
    return MEDHUB_API_KEY


def fetch_evaluations(auth_token: str) -> list[dict]:
    """
    Fetch all evaluation records from the MedHub API.

    TODO: Replace the NotImplementedError body with the real API call.

    Expected return format — a list of dicts with these keys
    (exact key names will come from the API response):
      {
        "resident_name": "Jane Smith",      # or split first_name / last_name
        "evaluator_name": "Dr. Jones",
        "rotation_name": "Internal Medicine",
        "competency_domain": "Patient Care",
        "score": 4.0,
        "comments": "Excellent performance...",
        "evaluation_date": "2024-11-15",
      }

    Example skeleton (fill in real URL path and field names):

        headers = {"Authorization": f"Bearer {auth_token}"}
        page, results = 1, []
        while True:
            resp = httpx.get(
                f"{MEDHUB_API_URL}/evaluations",
                headers=headers,
                params={"page": page, "per_page": 100},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            results.extend(data["evaluations"])
            if not data.get("next_page"):
                break
            page += 1
        return results
    """
    raise NotImplementedError(
        "fetch_evaluations() is not yet implemented. "
        "See medhub_api.py for instructions on what to fill in "
        "once MedHub API documentation is available."
    )


def _build_name_lookup(conn: sqlite3.Connection) -> dict[str, str]:
    """Return a dict mapping normalised resident names → resident_id."""
    rows = conn.execute(
        "SELECT id, first_name, last_name FROM residents WHERE active = 1"
    ).fetchall()
    lookup: dict[str, str] = {}
    for row in rows:
        full = f"{row['first_name']} {row['last_name']}".lower().strip()
        reversed_ = f"{row['last_name']}, {row['first_name']}".lower().strip()
        lookup[full] = row["id"]
        lookup[reversed_] = row["id"]
    return lookup


def _insert_evaluation(
    conn: sqlite3.Connection,
    resident_id: str,
    row: dict,
) -> bool:
    """
    Insert one evaluation row, returning True if inserted, False if duplicate.
    Uses INSERT OR IGNORE so the unique index handles duplicate detection.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO medhub_evaluations
            (id, resident_id, rotation_name, evaluator_name,
             evaluation_date, competency_domain, score, comments)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4())[:8],
            resident_id,
            row.get("rotation_name"),
            row.get("evaluator_name"),
            row.get("evaluation_date"),
            row.get("competency_domain"),
            row.get("score"),
            row.get("comments"),
        ),
    )
    return conn.execute("SELECT changes()").fetchone()[0] == 1


def sync_to_db(
    conn: sqlite3.Connection,
    manual_matches: Optional[dict] = None,
) -> dict:
    """
    Authenticate → fetch → insert into medhub_evaluations.

    manual_matches: optional { "CSV/API Name": "resident_id" } override dict.

    Returns { imported, skipped_duplicates, unmatched }.
    """
    auth_token = authenticate()
    evaluations = fetch_evaluations(auth_token)

    name_lookup = _build_name_lookup(conn)
    if manual_matches:
        name_lookup.update({k.lower().strip(): v for k, v in manual_matches.items()})

    imported = skipped_duplicates = 0
    unmatched: list[str] = []

    for ev in evaluations:
        csv_name = str(ev.get("resident_name", "")).strip()
        resident_id = name_lookup.get(csv_name.lower())
        if not resident_id:
            unmatched.append(csv_name)
            continue
        if _insert_evaluation(conn, resident_id, ev):
            imported += 1
        else:
            skipped_duplicates += 1

    conn.commit()
    return {
        "imported": imported,
        "skipped_duplicates": skipped_duplicates,
        "unmatched": unmatched,
    }
