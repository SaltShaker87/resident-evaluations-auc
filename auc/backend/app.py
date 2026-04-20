"""
AUC — Assessments Under Curve
Backend API server for residency feedback management.
"""

import os
import json
import sqlite3
import shutil
import uuid
from datetime import datetime, date
from pathlib import Path
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PHOTOS_DIR = DATA_DIR / "photos"
DB_PATH = DATA_DIR / "auc.db"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")

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
        """)
        for col_sql in [
            "ALTER TABLE residents ADD COLUMN medical_school TEXT",
            "ALTER TABLE residents ADD COLUMN interests TEXT",
            "ALTER TABLE residents ADD COLUMN track TEXT DEFAULT 'none'",
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

class NoteUpdate(BaseModel):
    content: Optional[str] = None
    acgme_domain: Optional[str] = None
    sentiment: Optional[str] = None
    priority: Optional[str] = None

class FollowupCreate(BaseModel):
    description: str
    priority: Optional[str] = "routine"

class SummaryApproval(BaseModel):
    approved_text: str

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
    with db_connection() as conn:
        conn.execute(
            """INSERT INTO notes (id, resident_id, content, acgme_domain, sentiment, priority)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (nid, resident_id, note.content, note.acgme_domain, note.sentiment, note.priority)
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
    with db_connection() as conn:
        conn.execute(
            """INSERT INTO followups (id, resident_id, description, priority)
               VALUES (?, ?, ?, ?)""",
            (fid, resident_id, followup.description, followup.priority)
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
async def generate_summary(resident_id: str):
    """Generate an AI summary from all notes for this resident."""
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

        followups = conn.execute(
            "SELECT * FROM followups WHERE resident_id = ? AND resolved = 0",
            (resident_id,)
        ).fetchall()

    if not notes:
        raise HTTPException(status_code=400, detail="No notes to summarize")

    # Build the prompt
    resident_name = f"Dr. {dict(resident)['first_name']} {dict(resident)['last_name']}"
    pgy = dict(resident)["pgy_year"]

    notes_text = ""
    for n in notes:
        nd = dict(n)
        notes_text += f"\n- [{nd['created_at']}] ({nd['acgme_domain'] or 'General'}, {nd['sentiment']}, {nd['priority']}): {nd['content']}"

    followup_text = ""
    if followups:
        followup_text = "\n\nOpen follow-up items:\n"
        for f in followups:
            fd = dict(f)
            followup_text += f"- [{fd['priority']}] {fd['description']}\n"

    prompt = f"""You are helping a clinical competency committee review the progress of an internal medicine resident. 

Resident: {resident_name} (PGY-{pgy})

Below are all documented feedback notes and observations for this resident. Each note includes a date, ACGME competency domain, sentiment (strength/neutral/concern), and priority level.

Notes:{notes_text}
{followup_text}

Please provide a structured summary organized as follows:

## Overall Assessment
A brief 2-3 sentence overview of this resident's trajectory.

## Strengths
List the key strengths with supporting evidence from the notes.

## Areas for Growth
List areas where improvement is needed, referencing specific observations.

## Recommended Follow-Up Actions
Specific, actionable items the program should track going forward.

Keep the tone professional, balanced, and constructive. Be specific — reference actual observations rather than making generic statements."""

    # Call Ollama
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.4,
                        "num_predict": 2048,
                    }
                }
            )
            response.raise_for_status()
            result = response.json()
            ai_text = result.get("response", "")
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="Could not connect to Ollama. Make sure Ollama is running (run 'ollama serve' in a terminal)."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating summary: {str(e)}")

    # Save draft
    sid = str(uuid.uuid4())[:8]
    with db_connection() as conn:
        conn.execute(
            """INSERT INTO summaries (id, resident_id, ai_draft) VALUES (?, ?, ?)""",
            (sid, resident_id, ai_text)
        )

    return {"id": sid, "ai_draft": ai_text}

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
