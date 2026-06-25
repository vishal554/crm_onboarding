# CLAUDE.md

Guidance for working in this repository.

## Project

Email-Driven CRM Onboarding Automation. Ingests inbound onboarding **emails**
(body + file attachments), turns them into **CRM onboarding tickets**, runs an
asynchronous Celery pipeline (attachment dedupe/store → applicant parsing → OCR →
validation), sends user notifications, and exposes read/admin APIs.

Stack: **Django + Django Ninja**, **PostgreSQL**, **Redis**, **Celery** (+ Beat),
**Tesseract OCR**, **spaCy**. Runs entirely via Docker Compose.

## Commands

Everything runs in containers — do not run app code on the host.

```bash
docker compose up -d --build      # start: web, worker, beat, postgres, redis
docker compose logs -f worker     # watch pipeline / notification / SLA tasks
docker compose exec web pytest    # run the test suite (tests/)
docker compose exec web python manage.py makemigrations onboarding
docker compose exec web python manage.py migrate
docker compose exec web python manage.py shell
docker compose exec web python manage.py createsuperuser   # for /admin
docker compose down               # stop
```

- Docker Desktop must be running first. If `docker` can't connect, start it:
  `"/c/Program Files/Docker/Docker/Docker Desktop.exe"` then wait for the engine.
- Source is bind-mounted (`.:/app`), so Python changes apply on
  `docker compose restart web worker beat` — **no rebuild needed**.
- Rebuild only when `requirements.txt` or the `Dockerfile` change.
- URLs: API docs `http://localhost:8000/api/docs`, admin `/admin/`, health `/api/health`.

## Architecture

- **`config/`** — Django project. `api.py` (NinjaAPI + routers), `celery.py`
  (Celery app + Beat schedule), `settings.py` (env-driven).
- **`onboarding/`** — the app.
  - `models.py` — RawEmail, Ticket, Document, TicketEvent, Notification,
    ValidationResult, IdempotencyKey, DeadLetter. PostgreSQL is the source of truth.
  - `routers/` — `email.py` (`POST /email/inbound`), `tickets.py` (list/detail/
    timeline/documents/status/reprocess).
  - `services/` — `ingestion.py`, `dedupe.py`, `validation.py`, `storage.py`,
    `notifications.py`, `events.py`, `failures.py`.
  - `parsing/` — `email_parser.py` (spaCy NER + regex), `ocr.py` (Tesseract),
    `extractors.py` (Aadhaar/PAN/DOB, Verhoeff checksum).
  - `tasks/` — Celery tasks: `pipeline.py` (the chain), `notifications.py`,
    `sla.py`, `deadletter.py`, `base.py` (retry/DLQ base task).
  - `deps.py` — `X-Admin-Key` API-key auth for mutating endpoints.

### Pipeline (Celery chain in `tasks/pipeline.py`)

`run_pipeline` dispatches: `store_raw_email → extract_attachments →
parse_user_info → run_document_extraction → run_validation`. Each stage advances
`Ticket.status` and appends a `TicketEvent`. Notifications and the SLA scan run on
their own queues. Queues: `ingest, pipeline, ocr, notifications, dead_letter`.

## Conventions & invariants

- **HTTP handlers are synchronous** (WSGI/gunicorn) for robust multipart handling.
  The asynchronous, non-blocking work is Celery — keep it that way; don't make the
  endpoints `async` (it reintroduces the event-loop/multipart bugs we already hit).
- **Idempotency** is enforced at the DB level: `RawEmail.content_hash` unique
  (hash of body + attachment bytes). Don't bypass `get_or_create` in ingestion.
- **Document dedup** is per-ticket: `UniqueConstraint(ticket, sha256)`. Storage is
  hash-addressed so identical bytes are stored once on disk.
- **Pipeline tasks** subclass `PipelineTask` (`tasks/base.py`) → auto-retry (3×,
  backoff+jitter) then dead-letter + `failed` status. Reuse it for new stages.
- **Add a new pipeline stage**: write the task in `pipeline.py` (`base=PipelineTask`),
  add it to the `chain()` in `run_pipeline`, export it in `tasks/__init__.py`
  (so autodiscover registers it), and emit a `TicketEvent`.
- **Extraction**: locate fields with heuristics, then *validate* with rules
  (PAN regex, Aadhaar first-digit 2–9 + Verhoeff, DOB anchored to a birth-date
  label — never Issue/Print date). Low-confidence/mismatch → `requires_manual_review`.
- **Request shape**: `POST /email/inbound` takes only `body` + `attachments`
  (multipart). Applicant fields and threading headers are parsed *from the body* —
  don't add request params without checking the assignment PDF first.
- After model changes, always create + apply a migration (commands above).

## Notes

- Email uses the **console backend** by default (prints to the worker log; no real
  delivery). Switch `EMAIL_BACKEND`/`EMAIL_*` env vars for SMTP — no code change.
- Postgres/Redis ports are **not** published to the host (avoids clashing with a
  local Postgres/Redis); inter-container traffic uses the compose network.
- Out of scope by design (the assignment's "Additional Difficult Features"):
  virus-scan sim, assignment engine, priority detection, rule-versioning reprocess
  engine, dedicated event-log system, search, ticket locking, metrics endpoint.
- See `README.md` for the full architecture write-up and API reference, and
  `docs/end_to_end_flow.mmd` for the end-to-end flow diagram.
