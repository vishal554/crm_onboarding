"""Inbound email ingestion: hashing, idempotency, ticket creation.

This is the synchronous core called (via ``sync_to_async``) from the async
``POST /email/inbound`` handler. Keeping it sync lets us use a single DB
transaction + ``get_or_create`` so idempotency holds even under concurrent
submissions of the same email.
"""

import datetime
import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from onboarding.models import (
    EventType,
    IdempotencyKey,
    RawEmail,
    Ticket,
    TicketStatus,
)
from onboarding.parsing.email_parser import parse_email_headers, parse_from_header
from onboarding.services.events import add_event
from onboarding.services.storage import decode_base64


def _find_thread_parent(headers):
    """Find the original ticket a reply belongs to, via In-Reply-To/References."""
    refs = []
    if headers.get("in_reply_to"):
        refs.append(headers["in_reply_to"])
    if headers.get("references"):
        refs.extend(headers["references"].split())
    refs = [r.strip() for r in refs if r.strip()]
    if not refs:
        return None
    parent_raw = (
        RawEmail.objects.filter(message_id__in=refs, tickets__isnull=False)
        .order_by("created_at")
        .first()
    )
    return parent_raw.tickets.first() if parent_raw else None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_content_hash(payload) -> str:
    """Stable 64-char idempotency anchor for an inbound email.

    Hashes the email's meaningful content - the body and the bytes of each
    attachment - so a repeated email maps to the same ticket (idempotency by
    hash, per the spec).
    """
    attachment_hashes = sorted(
        _sha256_bytes(decode_base64(att.content_base64))
        for att in payload.attachments
    )
    canonical = json.dumps(
        {
            "body": payload.body or "",
            "attachments": attachment_hashes,
        },
        sort_keys=True,
    )
    return _sha256_text(canonical)


@transaction.atomic
def ingest_email(payload):
    """Persist a RawEmail and resolve it against existing tickets.

    Returns ``(ticket, outcome)`` where outcome is one of:
      * ``"created"``   - new onboarding ticket (run the pipeline)
      * ``"reply"``     - reply mapped onto an existing ticket (no new pipeline)
      * ``"duplicate"`` - idempotent replay of an email we already have
    """
    headers = parse_email_headers(payload.body)
    _, from_email = parse_from_header(headers.get("from", ""))
    content_hash = compute_content_hash(payload)
    parent = _find_thread_parent(headers)

    raw, created = RawEmail.objects.get_or_create(
        content_hash=content_hash,
        defaults={
            "message_id": headers["message_id"] or None,
            "in_reply_to": headers["in_reply_to"] or None,
            "references": headers["references"] or None,
            "from_addr": from_email,
            "body_text": payload.body or "",
            "raw_payload": payload.model_dump(mode="json"),
            "received_at": timezone.now(),
            "thread_ticket": parent,
        },
    )

    if not created:
        existing = raw.tickets.first() or raw.thread_ticket
        return existing, "duplicate"

    if parent is not None:
        # Reply: thread it onto the original ticket; no new onboarding/pipeline.
        add_event(
            parent,
            EventType.STATUS_UPDATED,
            f"Reply received and mapped to {parent.ticket_ref}",
            message_id=headers["message_id"],
            in_reply_to=headers["in_reply_to"],
        )
        return parent, "reply"

    ticket = Ticket.objects.create(
        raw_email=raw,
        status=TicketStatus.RECEIVED,
        sla_due_at=raw.received_at + datetime.timedelta(hours=settings.SLA_HOURS),
    )
    add_event(ticket, EventType.EMAIL_RECEIVED, "Inbound email received")
    add_event(
        ticket,
        EventType.TICKET_CREATED,
        f"Onboarding ticket {ticket.ticket_ref} created",
    )
    IdempotencyKey.objects.create(key=content_hash, ticket=ticket)
    return ticket, "created"


def ingest_and_enqueue(payload):
    """Ingest the email and, for a new onboarding, enqueue the pipeline."""
    ticket, outcome = ingest_email(payload)
    if outcome == "created":
        # Imported here to avoid a circular import at module load.
        from onboarding.tasks import run_pipeline

        transaction.on_commit(lambda: run_pipeline.delay(ticket.id))
    return ticket, outcome
