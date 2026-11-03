# NEET PG Official-Source Auto-Update Backend

This is a deployable backend service for the NEET PG counselling website. It checks official source pages on a **server-side five-minute schedule**, even when the browser is closed, as long as the server/container remains running.

## What it does

- Checks MCC PG archive and Maharashtra CET Cell PG counselling pages.
- Finds relevant linked PDF/CSV/Excel/Word documents on the official source hosts.
- Downloads documents and calculates SHA-256 hashes.
- Detects new document versions and updates `last_seen_at` when a URL/hash is unchanged.
- Extracts searchable text from PDFs and HTML where possible.
- Saves check history, document metadata, extraction status, and review queue in SQLite.
- Provides JSON API endpoints and a CSV export of discovered documents.
- Does **not** fabricate cutoffs or automatically certify parsed ranks. New documents enter `needs_review`; unsupported scanned PDFs are marked for OCR/manual review.

## Current monitored sources

- MCC PG archive: https://mcc.nic.in/archive-pg/
- Maharashtra CET Cell CAP 2026–27: https://cetcell.mahacet.org/cap-_2026-27/
- Maharashtra NEET-PGM 2025 portal: https://medicalug2025.mahacet.org/NEET-PGM-2025/login

Official websites can change structure, require sessions, block automated requests, or publish files that require OCR. A source failure is logged as a failure; it is not treated as evidence that no updates exist.

## Run locally with Docker

1. Install Docker Desktop or Docker Engine.
2. Copy `.env.example` to `.env`.
3. Set `ADMIN_API_KEY` to a long random secret.
4. Run:

```bash
docker compose up --build -d
```

5. Open `http://localhost:8000/docs` for the API and `http://localhost:8000/api/health` for scheduler/source status.

The scheduler starts automatically and runs every five minutes. The first cycle runs on the schedule; to trigger an immediate cycle, use `POST /api/run-now` with the `X-Admin-Api-Key` header.

## API endpoints

- `GET /api/health` — scheduler, latest run, latest source statuses.
- `GET /api/sources` — monitored sources and their latest check.
- `GET /api/documents?limit=100` — recent discovered documents.
- `GET /api/documents?review_status=needs_review` — review queue.
- `GET /api/documents/{id}/text` — extracted text, when available.
- `GET /api/runs` — history of update cycles.
- `GET /api/export/documents.csv` — export document provenance.
- `GET /api/cutoffs` — cutoff records table (empty until validated rows are inserted).
- `POST /api/run-now` — trigger an immediate check; requires `X-Admin-Api-Key`.
- `POST /api/documents/{id}/review` — mark a document approved/rejected/needs_review; requires admin key.

## Hook it into the existing HTML frontend

Use the deployed backend URL in your frontend's update dashboard. Call `GET /api/health`, `/api/documents`, and `/api/runs` to show live server state. For manual checks, send `POST /api/run-now` with `X-Admin-Api-Key`. Do not expose the admin key in a public static HTML file; proxy this action through an authenticated server endpoint or use an admin-only dashboard.

## Production deployment checklist

- Deploy the container to a persistent host (VPS/container platform) that stays running.
- Use HTTPS behind a reverse proxy/platform TLS.
- Use a strong `ADMIN_API_KEY`; restrict `CORS_ORIGINS` to the actual frontend origin.
- Back up the `data/` volume.
- Monitor logs and health endpoint.
- Add OCR if scanned PDFs need it.
- Before using any cutoff, extract the relevant fields into `cutoff_records`, reconcile against official tables, and approve the source document. This implementation intentionally does not promote raw PDF text into verified cutoffs.

## Important constraints

- This backend can check sources every five minutes, but cannot guarantee the official portals are always reachable.
- Some portal pages may require browser JavaScript/session cookies and may not expose document links to a simple HTTP client.
- PDF extraction works only for text-based PDFs; scanned documents need OCR.
- A document being downloaded is not the same as a validated cutoff dataset.
- The service is not yet publicly deployed; it is a ready-to-run backend package.
