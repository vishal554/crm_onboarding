# Email-Driven CRM Onboarding Automation

A backend that ingests inbound onboarding **emails**, turns them into structured
**CRM onboarding tickets**, handles attached identity documents (Aadhaar/PAN),
runs OCR + validation, and notifies users — **asynchronously, idempotently, and
fault-tolerantly**.

Built with **Django + Django Ninja**, **PostgreSQL**, **Redis**, **Celery**
(+ Beat), **Tesseract OCR**, and **spaCy**, all orchestrated with Docker Compose.

---

## Deliverables

| Deliverable | Where |
|-------------|-------|
| Specs of each component | [`docs/SPECS.md`](docs/SPECS.md) |
| Working backend code | `onboarding/`, `config/` (run via Docker; tests in `tests/`) |
| Docker setup | `docker-compose.yml`, `Dockerfile`, `docker/entrypoint.sh` |
| Database schema | [`docs/schema.md`](docs/schema.md) (ER diagram + tables) |
| API endpoints | [API reference](#api-reference) below + Swagger at `/api/docs` |
| Sample test requests | [`samples/sample_emails.md`](samples/sample_emails.md), `requests.http`, `samples/` |
| README (architecture) | this file; flow diagram in [`docs/end_to_end_flow.mmd`](docs/end_to_end_flow.mmd) |

---

## Architecture

```
                       ┌─────────────────────────────┐
 inbound email ──POST──►  Django Ninja /email/inbound │  rate-limited, idempotent
 (body + file              │  validate → persist RawEmail
  attachments)             │  → create Ticket → enqueue │──► Redis (broker)
                           └──────────────┬─────────────┘
 admin / ops ─► Ninja read+admin APIs ─┐  │
               + Django admin (/admin) ─┴──┴─► PostgreSQL  (source of truth)
                                           ▲      │
                                           │      ▼
                       ┌───────────────────┴──────────────┐
                       │  Celery workers (queues):         │
                       │  ingest · pipeline · ocr ·        │──► local storage
                       │  notifications · dead_letter      │    (attachments,
                       │  + Beat (periodic SLA scan)       │     hash-addressed)
                       └───────────────────────────────────┘
```

**PostgreSQL is the source of truth.** Redis is the Celery broker/result backend
+ rate-limit counters. The HTTP layer stays thin — it validates, persists the raw
email, returns a ticket id, and hands everything heavy to Celery (non-blocking).

---

## Quick start

```bash
docker compose up --build
```

This brings up `postgres`, `redis`, `web` (gunicorn), `worker`, and `beat`. The
web container runs migrations + collectstatic on boot.

- API docs (Swagger): http://localhost:8000/api/docs
- Health: http://localhost:8000/api/health
- Django admin: http://localhost:8000/admin/ — create a login:
  ```bash
  docker compose exec web python manage.py createsuperuser
  ```

Try it (see [`samples/sample_emails.md`](samples/sample_emails.md) or `requests.http`):
```bash
curl -F "body=<samples/test1_match.txt" \
     -F "attachments=@samples/aadhaar_johndoe.png" \
     http://localhost:8000/api/email/inbound
```

---

## Processing pipeline

Each inbound email is processed by a Celery **chain** — every stage advances the
ticket `status` and appends a timeline event, and retries/recovers independently:

| # | Stage | Queue | What it does |
|---|-------|-------|--------------|
| 1 | `store_raw_email` | ingest | mark processing started |
| 2 | `extract_attachments` | pipeline | decode, validate, **hash-dedupe**, store, create `Document`s |
| 3 | `parse_user_info` | pipeline | extract name/email/phone/address from the body — **spaCy NER** + regex, **`phonenumbers`** for phone, sender from the `From:` header |
| 4 | `run_document_extraction` | ocr | **Tesseract OCR** → Aadhaar/PAN/DOB/name/**address**; derive age |
| 5 | `run_validation` | pipeline | required docs, format, **name/age/address consistency** (email vs document), **duplicate detection** (email/phone/doc-number/**doc-hash**) → resolve status |

Then user **notifications** are queued and sent on the `notifications` queue. A
**Beat** job periodically flags **SLA breaches**.

**Ticket states:** `received → processing → awaiting_validation →`
`approved | rejected | requires_manual_review | failed`.

---

## Data model (PostgreSQL)

| Table | Purpose / key fields |
|-------|----------------------|
| `raw_emails` | original email; `content_hash` **unique** (idempotency), `message_id`/`in_reply_to` (threading), `thread_ticket` |
| `tickets` | `ticket_ref` unique, `status`, applicant fields, `parsed_source` (JSON), `sla_due_at`, `sla_breached` |
| `documents` | attachment; `sha256` (unique **per ticket**; also matched **across tickets** for duplicate-document detection), `storage_path`, `metadata`, `extracted_fields` (JSON) |
| `ticket_events` | immutable **timeline** / processing log |
| `notifications` | queued/sent user notifications |
| `validation_results` | per-check outcomes |
| `idempotency_keys` | dedup key → ticket |
| `dead_letters` | tasks that failed after retries (**DLQ**), `reprocessed` flag |

DB-level uniqueness (content hash, idempotency key) is what makes ingestion
**race-safe idempotent**, not just app logic.

---

## API reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/email/inbound` | — | Ingest an email (`body` + `attachments` files), idempotent |
| GET | `/api/tickets` | — | List tickets (paginated, `?status=`) |
| GET | `/api/tickets/{id}` | — | Ticket detail + documents |
| GET | `/api/tickets/{id}/timeline` | — | Activity timeline |
| GET | `/api/tickets/{id}/documents` | — | Documents + extracted fields |
| PATCH | `/api/tickets/{id}/status` | `X-Admin-Key` | Admin status override (audited) |
| POST | `/api/tickets/{id}/reprocess` | `X-Admin-Key` | Re-run the pipeline |

Plus the **Django admin** at `/admin/` for full ops on every model.

---

## Feature coverage (assignment)

**Core (1–11):** email ingestion · async worker pipeline · ticket state machine ·
document handling + hash dedup · OCR document parsing (incl. **Aadhaar address**) ·
validation workflow (name/age/**address** consistency) · async notifications ·
duplicate detection (email/phone/doc-number/**doc-hash**) · idempotent processing ·
high-volume design (dedicated queues, backpressure) · failure handling (retries + DLQ).

**Additional mandatory:** ticket timeline · email thread detection (reply→original
via in-body headers) · rate limiting · admin APIs · SLA monitoring.

**Out of scope** (the *Additional Difficult Features* section — intentionally not
built): virus-scan simulation, ticket-assignment engine, priority detection,
rule-versioning reprocessing engine, dedicated event-log system, search system,
ticket locking, processing-metrics endpoint. (A basic reprocess endpoint *is*
included, as it's in the Minimum APIs.)

---

## Configuration (env vars)

Key settings (see `.env`): `POSTGRES_*`, `REDIS_URL`, `CELERY_*`,
`SLA_HOURS` (24), `SLA_CHECK_INTERVAL` (60s), `INGEST_RATE_LIMIT`/`_WINDOW`,
`MAX_ATTACHMENT_BYTES` (10MB), `ADMIN_API_KEY`, and `EMAIL_*`.

**Email:** defaults to the console backend (prints to the worker log). For real
delivery, set `EMAIL_BACKEND` to the SMTP backend and the `EMAIL_HOST*` vars
(e.g. Gmail with an App Password) — no code change.

---

## Testing

```bash
docker compose exec web pytest
```

**30 tests.** Unit tests cover document field extraction (Aadhaar/PAN/DOB/address,
Verhoeff checksum) and free-form body parsing (`phonenumbers`, `From:`-header
capture); DB tests cover idempotency, thread detection, sender capture, the
validation workflow (name/address mismatch, duplicate-document detection, the
`ticket_created` event), and the read/admin API surface.

---

## Notes & design decisions

- **Document extraction** combines heuristics (locate the field) with validation
  (PAN format, Aadhaar first-digit rule + **Verhoeff checksum**). DOB is anchored
  to a birth-date label so Issue/Print dates aren't mistaken for it.
- **OCR is best-effort**; low-confidence / inconsistent reads route a ticket to
  `requires_manual_review` rather than being trusted.
- **spaCy `en_core_web_sm`** parses names/addresses from prose; swap to a larger
  model behind the same function for higher accuracy. **Phone numbers** are parsed
  and validated with **`phonenumbers`** (libphonenumber); name/email fall back to the
  inbound `From:` header ("from mail") when the prose doesn't state them.
- **Duplicate detection** spans applicant email, phone, document number, and the
  **document content hash** — so the same identity document submitted under a
  different applicant is flagged for manual review.
- **Address** is read from the Aadhaar and reconciled against the email-provided
  address (token-overlap comparison, tolerant of formatting differences).
- The HTTP layer is synchronous (WSGI) for robust multipart handling; the
  asynchronous, non-blocking core is Celery.
