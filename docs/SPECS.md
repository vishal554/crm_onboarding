# Component Specifications

Spec for each component of the Email-Driven CRM Onboarding system: its
**responsibility**, **key inputs/outputs**, and the **invariants** it must hold.
Components are grouped as they are in the source tree. PostgreSQL is the source of
truth throughout; Redis is the Celery broker/result backend and the rate-limit store.

See also: [`schema.md`](schema.md) (data model), [`end_to_end_flow.mmd`](end_to_end_flow.mmd)
(flow diagram), and the README for the architecture overview and API reference.

---

## `config/` — Django project

### `config/api.py` — HTTP surface
- **Responsibility:** the root `NinjaAPI` instance; mounts the `email` and `tickets`
  routers under `/api` and exposes `GET /api/health`.
- **I/O:** in — HTTP requests; out — JSON responses, OpenAPI schema at `/api/openapi.json`,
  Swagger UI at `/api/docs`.
- **Invariants:** `/health` performs a real `SELECT 1` DB connectivity check and
  reports `degraded` if it fails.

### `config/celery.py` — async runtime
- **Responsibility:** the Celery app; autodiscovers tasks and defines the Beat schedule.
- **I/O:** in — task messages from Redis; out — task execution across queues
  `ingest · pipeline · ocr · notifications · dead_letter`.
- **Invariants:** Beat runs `onboarding.check_sla` every `SLA_CHECK_INTERVAL` seconds
  (default 60). A `debug.ping` task exists for liveness checks.

### `config/settings.py` — configuration
- **Responsibility:** env-driven settings (DB, Redis/Celery, storage, SLA, rate limit,
  admin key, email backend).
- **Invariants:** Postgres is used when `POSTGRES_HOST` is set, else sqlite for local
  runs. Celery uses `acks_late` + `reject_on_worker_lost` + `prefetch_multiplier=1`
  for at-least-once, backpressure-friendly processing. `ALLOWED_ATTACHMENT_TYPES =
  pdf/jpg/jpeg/png`; `MAX_ATTACHMENT_BYTES` default 10 MB.

---

## `onboarding/models.py` — data model
- **Responsibility:** the 7 entities (`RawEmail`, `Ticket`, `Document`, `TicketEvent`,
  `Notification`, `ValidationResult`, `DeadLetter`) and enums
  (`TicketStatus`, `DocumentType`, `DocumentStatus`, `NotificationStatus`, `EventType`).
- **Invariants (DB-level, what makes the system correct regardless of app logic):**
  - `raw_emails.content_hash` **unique** → one ticket per distinct email (idempotency).
  - `documents (ticket, sha256)` **unique** → per-ticket attachment dedup.
  - `tickets.ticket_ref` **unique**, human-friendly (`TKT-XXXXXXXX`).
  - `TERMINAL_TICKET_STATUSES = {approved, rejected}` (excluded from SLA/reprocess).
- Full column/constraint detail in [`schema.md`](schema.md).

---

## `onboarding/routers/` — API endpoints

### `email.py` — `POST /api/email/inbound`
- **Responsibility:** accept an inbound email as `multipart/form-data` (`body` text +
  `attachments` file uploads), validate attachments, and hand off to ingestion.
- **I/O:** in — `body: str`, `attachments: list[UploadedFile]`; out — `IngestResponse`
  (`ticket_id`, `status`, `idempotent`, `message`).
- **Invariants:** rate-limited per client; rejects unsupported types (422) and
  oversized files (413); a duplicate replay whose ticket was since removed returns a
  clean **409** (never a 500); the handler is **synchronous** (WSGI) for robust
  multipart handling — heavy work is delegated to Celery.

### `tickets.py` — read + admin
- **Responsibility:** `GET /tickets` (paginated, `?status=`), `GET /tickets/{id}`,
  `/timeline`, `/documents` (open); `PATCH /tickets/{id}/status` and
  `POST /tickets/{id}/reprocess` (admin).
- **Invariants:** mutating endpoints require the `X-Admin-Key` header; status changes
  and reprocesses are audited on the timeline; reprocess marks open dead-letters
  reprocessed and re-dispatches the pipeline.

---

## `onboarding/services/` — domain logic

| Service | Responsibility | Key invariants |
|---------|----------------|----------------|
| `ingestion.py` | Hash the email, enforce idempotency, detect replies, create `RawEmail`+`Ticket`, enqueue pipeline. Captures sender from the in-body `From:` header. | Single `@transaction.atomic` + `get_or_create` on `content_hash` → race-safe idempotency. Pipeline enqueued only `on_commit` and only for new tickets. Emits `email_received` + `ticket_created`. |
| `dedupe.py` | `find_duplicate(ticket)` — earliest *other* ticket whose **shared details all agree**: same applicant email **and** phone, plus document number **and** a shared document hash when both tickets carry them. | Identity (email+phone) is required; a single coincidental match isn't enough. Only matches tickets with a lower id (the first of a pair stays clean). |
| `validation.py` | Run checks (required doc, format, name/age/**address** consistency, duplicate) and resolve terminal status. | Missing docs → `rejected`; any mismatch/duplicate/bad-format → `requires_manual_review`; all pass → `approved`. Persists a `ValidationResult` per check. |
| `storage.py` | Hash-addressed local attachment storage; base64 decode; magic-byte format sniffing. | Identical bytes stored once on disk (`sha256[:2]/sha256.ext`). `sniff_format` validates by magic bytes, not extension. |
| `notifications.py` | Render templates and queue a `Notification`, then dispatch the async send task. | Persisted as `queued` before the send task runs. |
| `events.py` | `add_event(ticket, type, message, **payload)` — append an immutable `TicketEvent`. | The single writer for the timeline / processing log. |
| `failures.py` | `mark_failed(ticket_id, task, exc)` — move ticket to `failed`, log it, route to the DLQ. | Called only after a `PipelineTask` exhausts retries. |

---

## `onboarding/parsing/` — extraction

### `email_parser.py` — applicant fields from the body
- **Responsibility:** extract `name`, `email`, `phone`, `address` from free-form prose
  or `Label: value` lines; parse inline `Message-ID`/`In-Reply-To`/`References`/`From`
  headers.
- **Strategy / invariants:** explicit `Label:` lines win; **email** via regex (skipping
  `Message-ID`/`In-Reply-To`/`References` lines so their ids aren't mistaken for an
  address) with the `From:` address as fallback; **phone** via `phonenumbers` (libphonenumber, region
  `IN`, validated → national number) with a regex fallback; **name** via spaCy
  `PERSON` NER → phrase regex → `From:` display name; **address** via phrase regex →
  spaCy GPE/LOC.

### `ocr.py` — document text
- **Responsibility:** `run_ocr(bytes, content_type)` → text via Tesseract; PDFs are
  rasterised with pdf2image/poppler first.
- **Invariants:** engine-agnostic signature (swappable backend); images and PDFs both
  supported.

### `extractors.py` — identity fields from OCR text
- **Responsibility:** extract `document_number` (PAN regex / Aadhaar), `name`, `dob`
  (+derived `age`), and `address` (Aadhaar) with validation rules.
- **Invariants:** Aadhaar must satisfy first-digit 2–9 + **Verhoeff checksum**; DOB is
  anchored to a birth-date label (never Issue/Print date); name heuristics avoid card
  keywords; address captured from the `Address:` label up to the trailing PIN.

---

## `onboarding/tasks/` — Celery pipeline

### `pipeline.py` — the processing chain
- **Responsibility:** `run_pipeline` dispatches a `chain`: `store_raw_email →
  extract_attachments → parse_user_info → run_document_extraction →
  run_validation_task`. Each stage advances `Ticket.status` and appends a `TicketEvent`.
- **I/O:** in — `ticket_id`; out — side effects (Documents, parsed fields, validation
  results, queued notifications), each stage returns `ticket_id` to the next.
- **Invariants:** stages subclass `PipelineTask`; attachment extraction is
  hash-deduped and idempotent (safe to re-run on reprocess); the document's DOB/age
  populate the ticket, and its address back-fills `applicant_address` when the email
  gave none (email input still wins when present).

### `base.py` — `PipelineTask`
- **Responsibility:** base task giving auto-retry (3×, exponential backoff + jitter)
  then dead-letter + `failed` status on final failure.
- **Invariants:** reused by every pipeline stage; new stages must subclass it.

### `notifications.py` — async delivery
- **Responsibility:** render + send a queued `Notification` via Django email; emit
  `notification_sent`.
- **Invariants:** retries on send failure; marks `failed` (with an event) when there is
  no recipient address.

### `sla.py` — SLA monitor (Beat)
- **Responsibility:** `check_sla` flags unresolved tickets past `sla_due_at`.
- **Invariants:** skips terminal statuses; sets `sla_breached` once and emits
  `sla_breached`.

### `deadletter.py` — DLQ sink
- **Responsibility:** `record_dead_letter` persists a `DeadLetter` row for inspection /
  manual reprocessing.

---

## `onboarding/deps.py` — auth
- **Responsibility:** `AdminAuth` (`X-Admin-Key` header) gating mutating endpoints.
- **Invariants:** constant key compare against `settings.ADMIN_API_KEY`.

## `onboarding/ratelimit.py` — ingestion protection
- **Responsibility:** Redis fixed-window limiter (`INGEST_RATE_LIMIT` per
  `INGEST_RATE_WINDOW`).
- **Invariants:** per-client (X-Forwarded-For / REMOTE_ADDR); returns
  `(allowed, retry_after)`; synchronous Redis client (no event-loop churn).
