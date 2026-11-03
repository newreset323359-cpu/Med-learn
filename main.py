import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .db import connect, init_db
from .sources import SOURCES
from .worker import run_update_cycle

API_KEY = os.getenv("ADMIN_API_KEY", "")
INTERVAL_MINUTES = max(5, int(os.getenv("UPDATE_INTERVAL_MINUTES", "5")))
scheduler = BackgroundScheduler(timezone="UTC")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not scheduler.running:
        scheduler.add_job(run_update_cycle, "interval", minutes=INTERVAL_MINUTES,
                          id="official_source_monitor", replace_existing=True,
                          max_instances=1, coalesce=True)
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)

app = FastAPI(title="NEET PG Official Source Update API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"]
)

def require_admin(x_admin_api_key: str | None):
    if not API_KEY:
        raise HTTPException(503, "ADMIN_API_KEY is not configured; administrative actions are disabled.")
    if x_admin_api_key != API_KEY:
        raise HTTPException(401, "Invalid admin API key")

@app.get("/")
def root():
    return {
        "service": "NEET PG official source monitor",
        "scheduler_interval_minutes": INTERVAL_MINUTES,
        "scheduler_running": scheduler.running,
        "warning": "Document monitoring and extraction are automated. Cutoff rows are not automatically certified; review and validation are required.",
        "docs": "/docs",
        "health": "/api/health",
    }

@app.get("/api/health")
def health():
    with connect() as con:
        last_run = con.execute("SELECT * FROM import_runs ORDER BY id DESC LIMIT 1").fetchone()
        source_latest = []
        for s in SOURCES:
            row = con.execute("SELECT * FROM source_checks WHERE source_key=? ORDER BY id DESC LIMIT 1", (s.key,)).fetchone()
            source_latest.append(dict(row) if row else {"source_key": s.key, "source_name": s.name, "status": "never_checked"})
    return {"ok": True, "now": datetime.now(timezone.utc).isoformat(),
            "scheduler_running": scheduler.running, "interval_minutes": INTERVAL_MINUTES,
            "last_run": dict(last_run) if last_run else None, "sources": source_latest}

@app.get("/api/sources")
def sources():
    with connect() as con:
        out = []
        for s in SOURCES:
            last = con.execute("SELECT * FROM source_checks WHERE source_key=? ORDER BY id DESC LIMIT 1", (s.key,)).fetchone()
            out.append({"key": s.key, "name": s.name, "url": s.url, "counselling_system": s.counselling_system,
                        "last_check": dict(last) if last else None})
    return {"sources": out}

@app.get("/api/documents")
def documents(limit: int = 100, review_status: str | None = None):
    limit = max(1, min(limit, 500))
    with connect() as con:
        if review_status:
            rows = con.execute("SELECT * FROM documents WHERE review_status=? ORDER BY id DESC LIMIT ?",
                               (review_status, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM documents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"count": len(rows), "documents": [dict(r) for r in rows]}

@app.get("/api/runs")
def runs(limit: int = 50):
    limit = max(1, min(limit, 250))
    with connect() as con:
        rows = con.execute("SELECT * FROM import_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"runs": [dict(r) for r in rows]}

@app.get("/api/documents/{document_id}/text")
def document_text(document_id: int):
    with connect() as con:
        row = con.execute("SELECT extracted_text_path FROM documents WHERE id=?", (document_id,)).fetchone()
    if not row or not row["extracted_text_path"]:
        raise HTTPException(404, "No extracted text available for this document.")
    path = Path(row["extracted_text_path"])
    if not path.exists():
        raise HTTPException(404, "Extracted text file is no longer present.")
    return FileResponse(path, media_type="text/plain", filename=path.name)

@app.post("/api/run-now")
def run_now(x_admin_api_key: str | None = Header(default=None)):
    require_admin(x_admin_api_key)
    return run_update_cycle()

class ReviewPayload(BaseModel):
    review_status: str = Field(pattern="^(needs_review|approved|rejected)$")
    review_note: str = Field(default="", max_length=3000)

@app.post("/api/documents/{document_id}/review")
def review_document(document_id: int, payload: ReviewPayload, x_admin_api_key: str | None = Header(default=None)):
    require_admin(x_admin_api_key)
    with connect() as con:
        cur = con.execute("UPDATE documents SET review_status=?,review_note=? WHERE id=?",
                          (payload.review_status, payload.review_note, document_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Document not found.")
        row = con.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    return {"document": dict(row)}

@app.get("/api/cutoffs")
def cutoffs(system: str | None = None, institute: str | None = None, course: str | None = None, limit: int = 500):
    limit = max(1, min(limit, 5000))
    query = "SELECT c.*, d.document_url, d.sha256 AS source_hash FROM cutoff_records c JOIN documents d ON d.id=c.source_document_id WHERE 1=1"
    args = []
    if system:
        query += " AND c.counselling_system=?"; args.append(system)
    if institute:
        query += " AND c.institute LIKE ?"; args.append(f"%{institute}%")
    if course:
        query += " AND c.course LIKE ?"; args.append(f"%{course}%")
    query += " ORDER BY c.closing_air ASC LIMIT ?"; args.append(limit)
    with connect() as con:
        rows = con.execute(query, args).fetchall()
    return {"count": len(rows), "cutoffs": [dict(r) for r in rows],
            "note": "Only manually reviewed or validated records should be used for counselling decisions."}

@app.get("/api/export/documents.csv")
def export_documents():
    import csv, io
    with connect() as con:
        rows = con.execute("SELECT source_name,counselling_system,source_page_url,document_url,title,sha256,first_seen_at,last_seen_at,published_date,download_status,parser_status,extracted_chars,review_status,review_note FROM documents ORDER BY id DESC").fetchall()
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    from fastapi.responses import Response
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=neet_pg_source_documents.csv"})
