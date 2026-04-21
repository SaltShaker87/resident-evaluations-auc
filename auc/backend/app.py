"""
AUC — Assessments Under Curve
Backend API server for residency feedback management.
"""

import os
import csv
import io
import json
import sqlite3
import shutil
import uuid
from datetime import datetime, date
from pathlib import Path
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_MAX_TOKENS, MEDHUB_API_URL, MEDHUB_API_KEY
import medhub_api

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PHOTOS_DIR = DATA_DIR / "photos"
DB_PATH = DATA_DIR / "auc.db"

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

@contextmanager
def db_connection():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    """Create tables if they don't exist."""
    with db_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS residents (
                id TEXT PRIMARY KEY,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                pgy_year INTEGER NOT NULL,
                start_date TEXT,
                photo_filename TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                content TEXT NOT NULL,
                acgme_domain TEXT,
                sentiment TEXT CHECK(sentiment IN ('strength', 'neutral', 'concern')),
                priority TEXT CHECK(priority IN ('routine', 'important', 'urgent')) DEFAULT 'routine',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS followups (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT CHECK(priority IN ('routine', 'important', 'urgent')) DEFAULT 'routine',
                resolved INTEGER DEFAULT 0,
                resolved_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS summaries (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                ai_draft TEXT,
                approved_text TEXT,
                approved INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                approved_at TEXT,
                FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS medhub_evaluations (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                import_date TEXT DEFAULT (datetime('now')),
                rotation_name TEXT,
                evaluator_name TEXT,
                evaluation_date TEXT,
                competency_domain TEXT,
                score REAL,
                comments TEXT,
                FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_medhub_no_dupes
            ON medhub_evaluations(resident_id, evaluator_name, evaluation_date, competency_domain);
            CREATE TABLE IF NOT EXISTS medhub_sync_log (
                id TEXT PRIMARY KEY,
                sync_date TEXT DEFAULT (datetime('now')),
                sync_type TEXT NOT NULL,
                imported INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0
            );
        """)
        for col_sql in [
            "ALTER TABLE residents ADD COLUMN medical_school TEXT",
            "ALTER TABLE residents ADD COLUMN interests TEXT",
            "ALTER TABLE residents ADD COLUMN track TEXT DEFAULT 'none'",
            "ALTER TABLE notes ADD COLUMN source TEXT",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ResidentCreate(BaseModel):
    first_name: str
    last_name: str
    pgy_year: int
    start_date: Optional[str] = None
    medical_school: Optional[str] = None
    interests: Optional[str] = None
    track: Optional[str] = None

class ResidentUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    pgy_year: Optional[int] = None
    start_date: Optional[str] = None
    active: Optional[bool] = None
    medical_school: Optional[str] = None
    interests: Optional[str] = None
    track: Optional[str] = None

class BulkResidentImport(BaseModel):
    residents: List[ResidentCreate]

class NoteCreate(BaseModel):
    content: str
    acgme_domain: Optional[str] = None
    sentiment: Optional[str] = "neutral"
    priority: Optional[str] = "routine"
    source: Optional[str] = None
    note_date: Optional[str] = None

class NoteUpdate(BaseModel):
    content: Optional[str] = None
    acgme_domain: Optional[str] = None
    sentiment: Optional[str] = None
    priority: Optional[str] = None
    source: Optional[str] = None

class FollowupCreate(BaseModel):
    description: str
    priority: Optional[str] = "routine"
    note_date: Optional[str] = None

class SummaryApproval(BaseModel):
    approved_text: str

class MedhubImportRequest(BaseModel):
    rows: List[dict]
    mapping: dict
    manual_matches: dict = {}

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="AUC — Assessments Under Curve", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

# ---------------------------------------------------------------------------
# Resident endpoints
# ---------------------------------------------------------------------------

@app.get("/api/residents")
def list_residents(active_only: bool = True):
    with db_connection() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM residents WHERE active = 1 ORDER BY pgy_year, last_name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM residents ORDER BY active DESC, pgy_year, last_name"
            ).fetchall()

        residents = []
        for r in rows:
            rd = dict(r)
            # Count open follow-ups
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM followups WHERE resident_id = ? AND resolved = 0",
                (rd["id"],)
            ).fetchone()["cnt"]
            rd["open_followups"] = count
            # Count total notes
            note_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM notes WHERE resident_id = ?",
                (rd["id"],)
            ).fetchone()["cnt"]
            rd["total_notes"] = note_count
            residents.append(rd)

        return residents

@app.post("/api/residents")
def create_resident(resident: ResidentCreate):
    rid = str(uuid.uuid4())[:8]
    with db_connection() as conn:
        conn.execute(
            """INSERT INTO residents (id, first_name, last_name, pgy_year, start_date, medical_school, interests, track)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, resident.first_name, resident.last_name, resident.pgy_year, resident.start_date,
             resident.medical_school, resident.interests, resident.track or 'none')
        )
    return {"id": rid, "message": "Resident created"}

@app.post("/api/residents/bulk")
def bulk_import_residents(data: BulkResidentImport):
    created = []
    with db_connection() as conn:
        for r in data.residents:
            rid = str(uuid.uuid4())[:8]
            conn.execute(
                """INSERT INTO residents (id, first_name, last_name, pgy_year, start_date)
                   VALUES (?, ?, ?, ?, ?)""",
                (rid, r.first_name, r.last_name, r.pgy_year, r.start_date)
            )
            created.append(rid)
    return {"created": len(created), "ids": created}

@app.get("/api/residents/{resident_id}")
def get_resident(resident_id: str):
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM residents WHERE id = ?", (resident_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Resident not found")
        rd = dict(row)
        # Open follow-ups count
        rd["open_followups"] = conn.execute(
            "SELECT COUNT(*) as cnt FROM followups WHERE resident_id = ? AND resolved = 0",
            (resident_id,)
        ).fetchone()["cnt"]
        rd["total_notes"] = conn.execute(
            "SELECT COUNT(*) as cnt FROM notes WHERE resident_id = ?",
            (resident_id,)
        ).fetchone()["cnt"]
        return rd

@app.put("/api/residents/{resident_id}")
def update_resident(resident_id: str, updates: ResidentUpdate):
    fields = []
    values = []
    for field, val in updates.dict(exclude_unset=True).items():
        if field == "active":
            val = 1 if val else 0
        fields.append(f"{field} = ?")
        values.append(val)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    fields.append("updated_at = datetime('now')")
    values.append(resident_id)
    with db_connection() as conn:
        conn.execute(
            f"UPDATE residents SET {', '.join(fields)} WHERE id = ?",
            values
        )
    return {"message": "Resident updated"}

@app.delete("/api/residents/{resident_id}")
def delete_resident(resident_id: str):
    with db_connection() as conn:
        conn.execute("DELETE FROM residents WHERE id = ?", (resident_id,))
    return {"message": "Resident deleted"}

@app.post("/api/residents/{resident_id}/photo")
async def upload_photo(resident_id: str, file: UploadFile = File(...)):
    # Validate resident exists
    with db_connection() as conn:
        row = conn.execute("SELECT id FROM residents WHERE id = ?", (resident_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Resident not found")

    # Save photo
    ext = Path(file.filename).suffix or ".jpg"
    filename = f"{resident_id}{ext}"
    filepath = PHOTOS_DIR / filename
    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    # Update resident record
    with db_connection() as conn:
        conn.execute(
            "UPDATE residents SET photo_filename = ?, updated_at = datetime('now') WHERE id = ?",
            (filename, resident_id)
        )
    return {"message": "Photo uploaded", "filename": filename}

@app.get("/api/photos/{filename}")
def get_photo(filename: str):
    filepath = PHOTOS_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(filepath)

# ---------------------------------------------------------------------------
# Notes endpoints
# ---------------------------------------------------------------------------

ACGME_DOMAINS = [
    "Patient Care",
    "Medical Knowledge",
    "Systems-Based Practice",
    "Practice-Based Learning & Improvement",
    "Professionalism",
    "Interpersonal & Communication Skills",
]

@app.get("/api/domains")
def get_acgme_domains():
    return ACGME_DOMAINS

@app.get("/api/residents/{resident_id}/notes")
def list_notes(resident_id: str, domain: Optional[str] = None):
    with db_connection() as conn:
        if domain:
            rows = conn.execute(
                "SELECT * FROM notes WHERE resident_id = ? AND acgme_domain = ? ORDER BY created_at DESC",
                (resident_id, domain)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notes WHERE resident_id = ? ORDER BY created_at DESC",
                (resident_id,)
            ).fetchall()
        return [dict(r) for r in rows]

@app.post("/api/residents/{resident_id}/notes")
def create_note(resident_id: str, note: NoteCreate):
    nid = str(uuid.uuid4())[:8]
    created_at = note.note_date or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with db_connection() as conn:
        conn.execute(
            """INSERT INTO notes (id, resident_id, content, acgme_domain, sentiment, priority, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (nid, resident_id, note.content, note.acgme_domain, note.sentiment, note.priority, note.source, created_at)
        )
    return {"id": nid, "message": "Note created"}

@app.put("/api/notes/{note_id}")
def update_note(note_id: str, updates: NoteUpdate):
    fields = []
    values = []
    for field, val in updates.dict(exclude_unset=True).items():
        fields.append(f"{field} = ?")
        values.append(val)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    fields.append("updated_at = datetime('now')")
    values.append(note_id)
    with db_connection() as conn:
        conn.execute(f"UPDATE notes SET {', '.join(fields)} WHERE id = ?", values)
    return {"message": "Note updated"}

@app.delete("/api/notes/{note_id}")
def delete_note(note_id: str):
    with db_connection() as conn:
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return {"message": "Note deleted"}

# ---------------------------------------------------------------------------
# Follow-up endpoints
# ---------------------------------------------------------------------------

@app.get("/api/followups")
def list_all_followups(resolved: Optional[bool] = False):
    with db_connection() as conn:
        res_val = 1 if resolved else 0
        rows = conn.execute(
            """SELECT f.*, r.first_name, r.last_name, r.pgy_year
               FROM followups f
               JOIN residents r ON f.resident_id = r.id
               WHERE f.resolved = ?
               ORDER BY
                 CASE f.priority WHEN 'urgent' THEN 1 WHEN 'important' THEN 2 ELSE 3 END,
                 f.created_at DESC""",
            (res_val,)
        ).fetchall()
        return [dict(r) for r in rows]

@app.get("/api/residents/{resident_id}/followups")
def list_resident_followups(resident_id: str, include_resolved: bool = False):
    with db_connection() as conn:
        if include_resolved:
            rows = conn.execute(
                "SELECT * FROM followups WHERE resident_id = ? ORDER BY resolved, created_at DESC",
                (resident_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM followups WHERE resident_id = ? AND resolved = 0 ORDER BY created_at DESC",
                (resident_id,)
            ).fetchall()
        return [dict(r) for r in rows]

@app.post("/api/residents/{resident_id}/followups")
def create_followup(resident_id: str, followup: FollowupCreate):
    fid = str(uuid.uuid4())[:8]
    created_at = followup.note_date or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with db_connection() as conn:
        conn.execute(
            """INSERT INTO followups (id, resident_id, description, priority, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (fid, resident_id, followup.description, followup.priority, created_at)
        )
    return {"id": fid, "message": "Follow-up created"}

@app.put("/api/followups/{followup_id}/resolve")
def resolve_followup(followup_id: str):
    with db_connection() as conn:
        conn.execute(
            "UPDATE followups SET resolved = 1, resolved_at = datetime('now') WHERE id = ?",
            (followup_id,)
        )
    return {"message": "Follow-up resolved"}

@app.put("/api/followups/{followup_id}/unresolve")
def unresolve_followup(followup_id: str):
    with db_connection() as conn:
        conn.execute(
            "UPDATE followups SET resolved = 0, resolved_at = NULL WHERE id = ?",
            (followup_id,)
        )
    return {"message": "Follow-up reopened"}

@app.delete("/api/followups/{followup_id}")
def delete_followup(followup_id: str):
    with db_connection() as conn:
        conn.execute("DELETE FROM followups WHERE id = ?", (followup_id,))
    return {"message": "Follow-up deleted"}

# ---------------------------------------------------------------------------
# Ollama proxy endpoints
# ---------------------------------------------------------------------------

@app.get("/api/ollama/models")
async def get_ollama_models():
    """Return available Ollama model names, or an error object if Ollama is unreachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return {"error": "Could not connect to Ollama. Make sure Ollama is running (run 'ollama serve')."}

# ---------------------------------------------------------------------------
# AI Summary endpoints
# ---------------------------------------------------------------------------

@app.get("/api/residents/{resident_id}/summaries")
def list_summaries(resident_id: str):
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM summaries WHERE resident_id = ? ORDER BY created_at DESC",
            (resident_id,)
        ).fetchall()
        return [dict(r) for r in rows]

@app.post("/api/residents/{resident_id}/generate-summary")
async def generate_summary(
    resident_id: str,
    model: Optional[str] = Query(None),
):
    """Stream an AI summary as NDJSON tokens, saving to DB on completion."""
    effective_model = model or OLLAMA_MODEL

    with db_connection() as conn:
        resident = conn.execute(
            "SELECT * FROM residents WHERE id = ?", (resident_id,)
        ).fetchone()
        if not resident:
            raise HTTPException(status_code=404, detail="Resident not found")

        notes = conn.execute(
            "SELECT * FROM notes WHERE resident_id = ? ORDER BY created_at",
            (resident_id,)
        ).fetchall()

        medhub_rows = conn.execute(
            "SELECT * FROM medhub_evaluations WHERE resident_id = ? ORDER BY evaluation_date",
            (resident_id,)
        ).fetchall()

    if not notes:
        raise HTTPException(status_code=400, detail="No notes to summarize")

    notes_text = "\n".join(
        f"[{n['created_at']}] source={n['source'] or 'unknown'} "
        f"domain={n['acgme_domain'] or 'General'} content={n['content']}"
        for n in notes
    )
    medhub_text = (
        "\n".join(
            f"[{r['evaluation_date']}] rotation={r['rotation_name']} "
            f"evaluator={r['evaluator_name']} domain={r['competency_domain']} "
            f"score={r['score']} comments={r['comments']}"
            for r in medhub_rows
        ) if medhub_rows else "No MedHub data available yet"
    )

    prompt = (
        "You are helping a program director prepare for a Clinical Competency Committee meeting. "
        "Below is all available information about a resident, organized into two sections. "
        "Section 1 is manually entered committee notes, each tagged with an ACGME competency domain and source. "
        "Section 2 is formal evaluation data imported from MedHub. "
        "Write a concise narrative summary organized by ACGME domain, drawing on both sources where available. "
        "Highlight clear strengths, flag any concerns or patterns worth discussing, and suggest follow-up items. "
        "Be specific and reference the source of your observations. If there is no information for a particular domain, say so explicitly. "
        f"[Section 1 - Committee Notes: {notes_text}] "
        f"[Section 2 - MedHub Evaluations: {medhub_text}]"
    )

    sid = str(uuid.uuid4())[:8]

    async def stream_generator():
        accumulated = []
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": effective_model,
                        "prompt": prompt,
                        "stream": True,
                        "options": {
                            "temperature": 0.4,
                            "num_predict": OLLAMA_MAX_TOKENS,
                        },
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = chunk.get("response", "")
                        if token:
                            accumulated.append(token)
                            yield json.dumps({"token": token}) + "\n"
                        if chunk.get("done"):
                            break

            with db_connection() as conn:
                conn.execute(
                    "INSERT INTO summaries (id, resident_id, ai_draft) VALUES (?, ?, ?)",
                    (sid, resident_id, "".join(accumulated)),
                )
            yield json.dumps({"done": True, "id": sid}) + "\n"

        except httpx.ConnectError:
            yield json.dumps({"error": "Could not connect to Ollama. Make sure Ollama is running."}) + "\n"
        except Exception as e:
            yield json.dumps({"error": f"Error generating summary: {str(e)}"}) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")

@app.put("/api/summaries/{summary_id}/approve")
def approve_summary(summary_id: str, data: SummaryApproval):
    with db_connection() as conn:
        conn.execute(
            """UPDATE summaries
               SET approved_text = ?, approved = 1, approved_at = datetime('now')
               WHERE id = ?""",
            (data.approved_text, summary_id)
        )
    return {"message": "Summary approved"}

@app.delete("/api/summaries/{summary_id}")
def delete_summary(summary_id: str):
    with db_connection() as conn:
        conn.execute("DELETE FROM summaries WHERE id = ?", (summary_id,))
    return {"message": "Summary deleted"}

# ---------------------------------------------------------------------------
# MedHub import endpoints
# ---------------------------------------------------------------------------

@app.post("/api/medhub/parse-csv")
async def parse_medhub_csv(file: UploadFile = File(...)):
    """Read an uploaded CSV and return headers + first 5 rows for preview."""
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")
    contents = await file.read()
    try:
        text = contents.decode("utf-8-sig")  # handle BOM from Excel exports
    except UnicodeDecodeError:
        text = contents.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    rows = []
    for i, row in enumerate(reader):
        rows.append(dict(row))
        if i >= 4:
            break
    return {"headers": list(headers), "preview": rows}


def _name_lookup(conn: sqlite3.Connection) -> dict:
    """Build name→id map for all active residents (forward and reversed)."""
    result = {}
    for r in conn.execute("SELECT id, first_name, last_name FROM residents WHERE active=1").fetchall():
        full = f"{r['first_name']} {r['last_name']}".lower().strip()
        rev = f"{r['last_name']}, {r['first_name']}".lower().strip()
        result[full] = r["id"]
        result[rev] = r["id"]
    return result


@app.post("/api/medhub/import")
def import_medhub_csv(data: MedhubImportRequest):
    """
    Import CSV rows into medhub_evaluations using the provided column mapping.
    Returns counts of imported, skipped duplicates, and unmatched resident names.
    """
    mapping = data.mapping
    resident_col = mapping.get("resident_name")
    evaluator_col = mapping.get("evaluator")
    rotation_col = mapping.get("rotation")
    domain_col = mapping.get("domain")
    score_col = mapping.get("score")
    comments_col = mapping.get("comments")
    date_col = mapping.get("evaluation_date")

    with db_connection() as conn:
        name_map = _name_lookup(conn)
        # Merge in any manual overrides
        for csv_name, res_id in data.manual_matches.items():
            name_map[csv_name.lower().strip()] = res_id

        imported = skipped_duplicates = 0
        unmatched: list = []

        for idx, row in enumerate(data.rows):
            csv_name = str(row.get(resident_col, "")).strip() if resident_col else ""
            resident_id = name_map.get(csv_name.lower()) if csv_name else None
            if not resident_id:
                unmatched.append({"csv_name": csv_name, "row_index": idx})
                continue

            evaluator = str(row.get(evaluator_col, "")).strip() if evaluator_col else None
            rotation = str(row.get(rotation_col, "")).strip() if rotation_col else None
            domain = str(row.get(domain_col, "")).strip() if domain_col else None
            score_raw = row.get(score_col, "") if score_col else ""
            try:
                score = float(score_raw) if score_raw else None
            except (ValueError, TypeError):
                score = None
            comments = str(row.get(comments_col, "")).strip() if comments_col else None
            eval_date = str(row.get(date_col, "")).strip() if date_col else None

            conn.execute(
                """INSERT OR IGNORE INTO medhub_evaluations
                   (id, resident_id, rotation_name, evaluator_name,
                    evaluation_date, competency_domain, score, comments)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4())[:8], resident_id, rotation, evaluator,
                 eval_date, domain, score, comments),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            if changed:
                imported += 1
            else:
                skipped_duplicates += 1

        # Log this import
        if imported > 0 or skipped_duplicates > 0:
            conn.execute(
                "INSERT INTO medhub_sync_log (id, sync_type, imported, skipped) VALUES (?, 'csv', ?, ?)",
                (str(uuid.uuid4())[:8], imported, skipped_duplicates),
            )

    return {
        "imported": imported,
        "skipped_duplicates": skipped_duplicates,
        "unmatched": unmatched,
    }


@app.post("/api/medhub/sync")
def sync_medhub_api():
    """Trigger a live sync from the MedHub API (requires credentials in config.py)."""
    if not medhub_api.is_configured():
        return {
            "configured": False,
            "message": "MedHub API not configured — see auc/backend/config.py to add credentials.",
        }
    conn = get_db()
    try:
        result = medhub_api.sync_to_db(conn)
        conn.execute(
            "INSERT INTO medhub_sync_log (id, sync_type, imported, skipped) VALUES (?, 'api', ?, ?)",
            (str(uuid.uuid4())[:8], result["imported"], result["skipped_duplicates"]),
        )
        conn.commit()
        return {"configured": True, **result}
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MedHub API error: {exc}")
    finally:
        conn.close()


@app.get("/api/medhub/status")
def medhub_status():
    """Return import/sync statistics for the MedHub Import page."""
    with db_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM medhub_evaluations").fetchone()[0]
        last_csv_row = conn.execute(
            "SELECT sync_date FROM medhub_sync_log WHERE sync_type='csv' ORDER BY sync_date DESC LIMIT 1"
        ).fetchone()
        last_api_row = conn.execute(
            "SELECT sync_date FROM medhub_sync_log WHERE sync_type='api' ORDER BY sync_date DESC LIMIT 1"
        ).fetchone()
    return {
        "total_evaluations": total,
        "last_csv_import": last_csv_row["sync_date"] if last_csv_row else None,
        "last_api_sync": last_api_row["sync_date"] if last_api_row else None,
        "api_configured": medhub_api.is_configured(),
    }


@app.get("/api/residents/list-for-matching")
def residents_for_matching():
    """Lightweight resident list for the unmatched-name resolution dropdowns."""
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT id, first_name, last_name, pgy_year FROM residents WHERE active=1 ORDER BY last_name, first_name"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Serve frontend (production mode)
# ---------------------------------------------------------------------------

FRONTEND_DIR = BASE_DIR / "frontend" / "dist"

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        # Serve index.html for all non-API routes (SPA routing)
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
