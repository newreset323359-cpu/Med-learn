import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .db import connect
from .sources import SOURCES

USER_AGENT = os.getenv(
    "FETCH_USER_AGENT",
    "NEETPGCounsellingDataMonitor/1.0 (official-source monitoring; contact: admin-configure-me)"
)
TIMEOUT = int(os.getenv("FETCH_TIMEOUT_SECONDS", "25"))
MAX_DOCUMENT_MB = int(os.getenv("MAX_DOCUMENT_MB", "80"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DOC_DIR = DATA_DIR / "documents"
TEXT_DIR = DATA_DIR / "extracted_text"
DOC_DIR.mkdir(parents=True, exist_ok=True)
TEXT_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*;q=0.8"})

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()

def clean_title(text, fallback):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:400] or fallback

def same_origin_allowed(source_url, target_url):
    # Only follow links on the source page's own host or official government hosts.
    a, b = urlparse(source_url), urlparse(target_url)
    host = (b.hostname or "").lower()
    if host == (a.hostname or "").lower():
        return True
    allowed_suffixes = (".gov.in", ".nic.in", ".mahacet.org", ".s3waas.gov.in", ".amazonaws.com")
    return host.endswith(allowed_suffixes)

def discover_links(source, html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    found = {}
    for a in soup.select("a[href]"):
        href = urldefrag(urljoin(source.url, a.get("href", "")))[0]
        if not href.startswith(("http://", "https://")) or not same_origin_allowed(source.url, href):
            continue
        title = clean_title(a.get_text(" ", strip=True), Path(urlparse(href).path).name or href)
        path = urlparse(href).path.lower()
        label = (title + " " + path).lower()
        # Relevant files and pages. Keep HTML links that sound like result/selection/seat matrix documents.
        is_file = path.endswith((".pdf", ".xlsx", ".xls", ".csv", ".doc", ".docx"))
        is_relevant = any(term in label for term in (
            "neet-pg", "neet pg", "pgm", "selection", "allot", "result", "seat matrix",
            "seat-matrix", "round", "counselling", "counseling", "merit list", "merit-list",
            "vacancy", "notice", "information bulletin", "schedule"
        ))
        if is_file or is_relevant:
            found[href] = title
    return list(found.items())[:250]

def extract_pdf_text(path):
    try:
        reader = PdfReader(str(path))
        chunks = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                chunks.append("")
        return "\n\n".join(chunks), len(reader.pages)
    except Exception as exc:
        raise RuntimeError(f"PDF text extraction failed: {exc}") from exc

def download_document(source, document_url, title):
    response = SESSION.get(document_url, timeout=TIMEOUT, stream=True, allow_redirects=True)
    response.raise_for_status()
    ctype = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    chunks, total = [], 0
    for chunk in response.iter_content(1024 * 128):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_DOCUMENT_MB * 1024 * 1024:
            raise RuntimeError(f"Document exceeds {MAX_DOCUMENT_MB} MB safety limit")
        chunks.append(chunk)
    body = b"".join(chunks)
    digest = sha256(body)
    path_suffix = ".pdf" if (document_url.lower().split("?")[0].endswith(".pdf") or ctype == "application/pdf" or body[:4] == b"%PDF") else ".bin"
    path = DOC_DIR / f"{digest}{path_suffix}"
    if not path.exists():
        path.write_bytes(body)

    text_path, extracted_chars, parser_status = None, 0, "not_supported"
    if path_suffix == ".pdf" or body[:4] == b"%PDF":
        try:
            text, pages = extract_pdf_text(path)
            out = TEXT_DIR / f"{digest}.txt"
            out.write_text(text, encoding="utf-8", errors="replace")
            text_path, extracted_chars, parser_status = str(out), len(text), ("extracted" if len(text.strip()) > 100 else "needs_ocr_or_manual_review")
        except Exception as exc:
            parser_status = "extraction_failed:" + str(exc)[:250]
    elif "text/html" in ctype or body.lstrip().startswith(b"<"):
        try:
            soup = BeautifulSoup(body, "html.parser")
            text = soup.get_text("\n", strip=True)
            out = TEXT_DIR / f"{digest}.txt"
            out.write_text(text, encoding="utf-8", errors="replace")
            text_path, extracted_chars, parser_status = str(out), len(text), "html_extracted"
        except Exception as exc:
            parser_status = "extraction_failed:" + str(exc)[:250]

    return {
        "status_code": response.status_code, "content_type": ctype, "body": body, "sha256": digest,
        "path": str(path), "text_path": text_path, "extracted_chars": extracted_chars,
        "parser_status": parser_status, "final_url": response.url,
        "last_modified": response.headers.get("Last-Modified"),
    }

def save_source_check(source, status, http_status=None, detail="", doc_count=0):
    with connect() as con:
        con.execute(
            """INSERT INTO source_checks(source_key,source_name,source_url,checked_at,status,http_status,detail,discovered_documents)
               VALUES(?,?,?,?,?,?,?,?)""",
            (source.key, source.name, source.url, now(), status, http_status, detail[:2000], doc_count)
        )

def check_one_source(source):
    resp = SESSION.get(source.url, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "").lower()
    if "html" not in ctype and not resp.text.lstrip().startswith(("<", "<!DOCTYPE")):
        save_source_check(source, "reachable_unparsed", resp.status_code,
                          "Source responded, but response did not look like HTML. No data inferred.")
        return {"source": source.key, "status": "reachable_unparsed", "documents": 0}

    links = discover_links(source, resp.text)
    new_docs, changed_docs, errors = 0, 0, []
    for url, title in links:
        try:
            # For linked HTML pages, avoid downloading every generic link. Download files and relevant named pages only.
            parsed = urlparse(url)
            is_file = parsed.path.lower().endswith((".pdf", ".xlsx", ".xls", ".csv", ".doc", ".docx"))
            if not is_file:
                continue
            downloaded = download_document(source, url, title)
            timestamp = now()
            with connect() as con:
                existing = con.execute(
                    "SELECT id,sha256 FROM documents WHERE document_url=? ORDER BY id DESC LIMIT 1", (url,)
                ).fetchone()
                if existing and existing["sha256"] == downloaded["sha256"]:
                    con.execute("UPDATE documents SET last_seen_at=? WHERE id=?", (timestamp, existing["id"]))
                    continue
                if existing:
                    changed_docs += 1
                else:
                    new_docs += 1
                con.execute(
                    """INSERT OR IGNORE INTO documents
                    (source_key,counselling_system,source_name,source_page_url,document_url,title,sha256,content_type,
                     first_seen_at,last_seen_at,published_date,round_label,download_status,parser_status,
                     extracted_text_path,extracted_chars,review_status,review_note)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (source.key, source.counselling_system, source.name, source.url, url, title,
                     downloaded["sha256"], downloaded["content_type"], timestamp, timestamp,
                     downloaded["last_modified"], None, "downloaded", downloaded["parser_status"],
                     downloaded["text_path"], downloaded["extracted_chars"], "needs_review",
                     "New/changed official-source document. Cutoff rows are not promoted automatically.")
                )
        except Exception as exc:
            errors.append(f"{url}: {str(exc)[:250]}")

    detail = f"Found {len(links)} relevant links; {new_docs} new document versions; {changed_docs} changed URLs; {len(errors)} download errors."
    if errors:
        detail += " Errors: " + " | ".join(errors[:8])
    save_source_check(source, "checked_with_errors" if errors else "checked", resp.status_code, detail, len(links))
    return {"source": source.key, "status": "checked_with_errors" if errors else "checked",
            "documents_discovered": len(links), "new_documents": new_docs,
            "changed_documents": changed_docs, "errors": errors[:10], "detail": detail}

def run_update_cycle():
    started = now()
    with connect() as con:
        cur = con.execute("INSERT INTO import_runs(started_at,status) VALUES(?,?)", (started, "running"))
        run_id = cur.lastrowid
    results, new_docs, changed_docs, errors = [], 0, 0, 0
    try:
        for source in SOURCES:
            try:
                result = check_one_source(source)
                new_docs += result.get("new_documents", 0)
                changed_docs += result.get("changed_documents", 0)
                errors += len(result.get("errors", []))
                results.append(result)
            except Exception as exc:
                errors += 1
                detail = f"{type(exc).__name__}: {str(exc)[:700]}"
                save_source_check(source, "failed", None, detail, 0)
                results.append({"source": source.key, "status": "failed", "detail": detail})
        status = "completed_with_errors" if errors or any(r["status"] == "failed" for r in results) else "completed"
        with connect() as con:
            con.execute(
                """UPDATE import_runs SET finished_at=?,status=?,documents_seen=?,new_documents=?,
                changed_documents=?,extracted_documents=(SELECT COUNT(*) FROM documents WHERE parser_status IN ('extracted','html_extracted')),
                errors=?,detail=? WHERE id=?""",
                (now(), status, sum(r.get("documents_discovered", 0) for r in results),
                 new_docs, changed_docs, errors, str(results)[:8000], run_id)
            )
        return {"run_id": run_id, "started_at": started, "finished_at": now(), "status": status,
                "new_documents": new_docs, "changed_documents": changed_docs, "errors": errors, "sources": results}
    except Exception as exc:
        with connect() as con:
            con.execute("UPDATE import_runs SET finished_at=?,status=?,detail=? WHERE id=?",
                        (now(), "failed", str(exc)[:2000], run_id))
        raise
