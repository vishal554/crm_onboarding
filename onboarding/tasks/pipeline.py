"""Onboarding processing pipeline (worker-based, non-blocking).

``run_pipeline`` dispatches a Celery ``chain`` so each stage retries / recovers
independently and the ticket status + timeline advance step by step.

Stages implemented in this step:
    1. store_raw_email     - mark processing started (raw email already persisted)
    2. extract_attachments - decode, validate, hash-dedupe, store, create Documents
    3. parse_user_info     - extract applicant fields from the email body

OCR (4), validation (6) and notifications (7) are added in later steps and will
be appended to this chain.
"""

from datetime import date

from celery import chain, shared_task

from onboarding.models import (
    Document,
    DocumentStatus,
    DocumentType,
    EventType,
    Ticket,
    TicketStatus,
)
from onboarding.parsing.email_parser import parse_email_body
from onboarding.parsing.extractors import extract_identity_fields
from onboarding.parsing.ocr import run_ocr
from onboarding.services.events import add_event
from onboarding.services.notifications import queue_notification
from onboarding.tasks.base import PipelineTask
from onboarding.services.storage import decode_base64, sha256_hex, sniff_format, storage
from onboarding.services.validation import run_validation

# Map declared/ sniffed extensions onto the DocumentType enum.
_EXT_NORMALISE = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "pdf": "pdf"}


@shared_task(name="onboarding.run_pipeline", queue="pipeline")
def run_pipeline(ticket_id: int):
    """Entry point: build and dispatch the processing chain for a ticket."""
    chain(
        store_raw_email.s(ticket_id),
        extract_attachments.s(),
        parse_user_info.s(),
        run_document_extraction.s(),
        run_validation_task.s(),
    ).apply_async()
    return {"dispatched": ticket_id}


@shared_task(name="onboarding.store_raw_email", queue="ingest", base=PipelineTask)
def store_raw_email(ticket_id: int) -> int:
    """Stage 1 - confirm receipt and mark the ticket as processing."""
    ticket = Ticket.objects.get(id=ticket_id)
    if ticket.status == TicketStatus.RECEIVED:
        ticket.status = TicketStatus.PROCESSING
        ticket.save(update_fields=["status", "updated_at"])
    return ticket_id


@shared_task(name="onboarding.extract_attachments", queue="pipeline", base=PipelineTask)
def extract_attachments(ticket_id: int) -> int:
    """Stage 2 - decode attachments, dedupe by hash, store, create Documents."""
    ticket = Ticket.objects.select_related("raw_email").get(id=ticket_id)
    attachments = (ticket.raw_email.raw_payload or {}).get("attachments", [])

    created = 0
    seen_hashes: set[str] = set()
    for att in attachments:
        data = decode_base64(att["content_base64"])
        digest = sha256_hex(data)

        # Dedupe within this email and against this ticket's existing docs.
        if digest in seen_hashes or Document.objects.filter(
            ticket=ticket, sha256=digest
        ).exists():
            continue
        seen_hashes.add(digest)

        declared = _EXT_NORMALISE.get(att.get("content_type", "").lower(), "")
        sniffed = sniff_format(data)
        ext = declared or sniffed or DocumentType.PDF
        format_valid = sniffed is not None and (declared == "" or sniffed == declared)

        storage_path = storage.save(digest, ext, data)
        Document.objects.create(
            ticket=ticket,
            filename=att.get("filename", f"{digest[:12]}.{ext}"),
            content_type=ext,
            size_bytes=len(data),
            sha256=digest,
            storage_path=storage_path,
            status=DocumentStatus.UPLOADED,
            metadata={
                "declared_type": att.get("content_type", ""),
                "sniffed_type": sniffed,
                "format_valid": format_valid,
            },
        )
        created += 1

    if attachments:
        add_event(
            ticket,
            EventType.DOCUMENTS_UPLOADED,
            f"Stored {created} document(s) ({len(attachments)} received)",
            stored=created,
            received=len(attachments),
        )
    return ticket_id


@shared_task(name="onboarding.parse_user_info", queue="pipeline", base=PipelineTask)
def parse_user_info(ticket_id: int) -> int:
    """Stage 3 - extract applicant fields from the email body."""
    ticket = Ticket.objects.select_related("raw_email").get(id=ticket_id)
    parsed = parse_email_body(ticket.raw_email.body_text, ticket.raw_email.from_addr)

    ticket.applicant_name = parsed["name"] or ticket.applicant_name
    ticket.applicant_phone = parsed["phone"] or ticket.applicant_phone
    ticket.applicant_email = parsed["email"] or ticket.applicant_email
    ticket.applicant_address = parsed["address"] or ticket.applicant_address

    source = ticket.parsed_source or {}
    source["email"] = parsed
    ticket.parsed_source = source
    ticket.status = TicketStatus.PROCESSING
    ticket.save()

    add_event(
        ticket,
        EventType.STATUS_UPDATED,
        "Parsed applicant info from email body",
        **parsed,
    )
    # We now know the applicant's contact details - acknowledge receipt.
    queue_notification(ticket, "onboarding_received")
    return ticket_id


@shared_task(name="onboarding.run_document_extraction", queue="ocr", base=PipelineTask)
def run_document_extraction(ticket_id: int) -> int:
    """Stage 4 - OCR each document and extract identity fields.

    Document-sourced fields are stored per-document and merged onto the ticket
    (DOB drives applicant age). Reconciliation against the email-provided data
    happens in the validation stage.
    """
    ticket = Ticket.objects.get(id=ticket_id)
    merged: dict = {}

    for doc in ticket.documents.all():
        try:
            with open(doc.storage_path, "rb") as fh:
                text = run_ocr(fh.read(), doc.content_type)
            fields = extract_identity_fields(text)
        except Exception as exc:  # OCR is best-effort; never break the chain
            doc.metadata = {**(doc.metadata or {}), "ocr_error": str(exc)}
            doc.save(update_fields=["metadata", "updated_at"])
            add_event(
                ticket,
                EventType.DOCUMENT_PARSED,
                f"OCR failed for {doc.filename}",
                error=str(exc),
            )
            continue

        doc.extracted_fields = {**fields, "ocr_excerpt": text.strip()[:500]}
        doc.status = DocumentStatus.PARSED
        doc.save(update_fields=["extracted_fields", "status", "updated_at"])
        add_event(
            ticket,
            EventType.DOCUMENT_PARSED,
            f"Extracted identity fields from {doc.filename}",
            **fields,
        )
        # First non-empty value across documents wins; a later page (e.g. the
        # back of an Aadhaar) must not overwrite a good field from the front.
        for key, value in fields.items():
            if value not in (None, "", []) and key not in merged:
                merged[key] = value

    if merged:
        source = ticket.parsed_source or {}
        source["document"] = merged
        ticket.parsed_source = source
        if merged.get("dob"):
            ticket.applicant_dob = date.fromisoformat(merged["dob"])
        if merged.get("age") is not None:
            ticket.applicant_age = merged["age"]
        ticket.save()

    if ticket.documents.exists():
        queue_notification(ticket, "documents_processed")
    return ticket_id


@shared_task(name="onboarding.run_validation", queue="pipeline", base=PipelineTask)
def run_validation_task(ticket_id: int) -> int:
    """Stage 5 - run validation checks and set the ticket's resolved status."""
    ticket = Ticket.objects.get(id=ticket_id)
    ticket.status = TicketStatus.AWAITING_VALIDATION
    ticket.save(update_fields=["status", "updated_at"])

    status, event, message, duplicate = run_validation(ticket)

    if duplicate is not None:
        # Record the duplicate on the original ticket's timeline (merge intent).
        add_event(
            duplicate,
            EventType.DUPLICATE_MERGED,
            f"Duplicate submission received: {ticket.ticket_ref}",
            duplicate=ticket.ticket_ref,
        )
        source = ticket.parsed_source or {}
        source["duplicate_of"] = duplicate.ticket_ref
        ticket.parsed_source = source

    ticket.status = status
    ticket.save()
    add_event(ticket, event, message)

    _notify_validation_outcome(ticket, status)
    return ticket_id


def _notify_validation_outcome(ticket, status):
    """Queue the user notification(s) appropriate to the validation result."""
    if status == TicketStatus.APPROVED:
        queue_notification(ticket, "validation_passed")
        queue_notification(ticket, "ticket_approved")
    elif status == TicketStatus.REJECTED:
        if not ticket.documents.exists():
            queue_notification(ticket, "documents_missing")
        queue_notification(ticket, "ticket_rejected")
    elif status == TicketStatus.REQUIRES_MANUAL_REVIEW:
        queue_notification(ticket, "validation_failed")
