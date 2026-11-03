import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/neet_pg_updates.sqlite3")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS source_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL,
  http_status INTEGER,
  detail TEXT,
  discovered_documents INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT NOT NULL,
  counselling_system TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_page_url TEXT NOT NULL,
  document_url TEXT NOT NULL,
  title TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  content_type TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  published_date TEXT,
  round_label TEXT,
  download_status TEXT NOT NULL,
  parser_status TEXT NOT NULL DEFAULT 'not_attempted',
  extracted_text_path TEXT,
  extracted_chars INTEGER DEFAULT 0,
  review_status TEXT NOT NULL DEFAULT 'needs_review',
  review_note TEXT,
  UNIQUE(document_url, sha256)
);
CREATE TABLE IF NOT EXISTS import_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  documents_seen INTEGER DEFAULT 0,
  new_documents INTEGER DEFAULT 0,
  changed_documents INTEGER DEFAULT 0,
  extracted_documents INTEGER DEFAULT 0,
  errors INTEGER DEFAULT 0,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS cutoff_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  counselling_system TEXT NOT NULL,
  year TEXT,
  round_label TEXT,
  quota TEXT,
  state TEXT,
  institute TEXT NOT NULL,
  course TEXT NOT NULL,
  category TEXT,
  candidate_category TEXT,
  closing_air INTEGER,
  source_document_id INTEGER NOT NULL,
  validation_status TEXT NOT NULL DEFAULT 'pending_review',
  created_at TEXT NOT NULL,
  FOREIGN KEY(source_document_id) REFERENCES documents(id)
);
CREATE TABLE IF NOT EXISTS saved_choices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_key TEXT NOT NULL,
  counselling_system TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with connect() as con:
        con.executescript(SCHEMA)
