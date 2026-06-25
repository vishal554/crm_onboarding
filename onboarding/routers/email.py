"""Email ingestion router: POST /api/email/inbound.

Accepts an inbound email as multipart/form-data - the email fields (sender,
subject, body, message ids) plus the attached documents as real file uploads,
the way inbound-email providers post a received message. Files are normalised
into the internal email representation, then ingestion is idempotent and hands
processing to the Celery pipeline.
"""

import base64
from typing import List

from django.conf import settings
from ninja import File, Form, Router, UploadedFile
from ninja.errors import HttpError

from onboarding.ratelimit import check_rate_limit
from onboarding.schemas import AttachmentIn, IngestResponse, InboundEmailIn
from onboarding.services.ingestion import ingest_and_enqueue

router = Router(tags=["email"])


def _client_id(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _file_ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _validate_files(files) -> None:
    """Check uploaded attachments for type and size at ingest time."""
    for f in files:
        name = f.name or "upload"
        ext = _file_ext(name)
        if ext not in settings.ALLOWED_ATTACHMENT_TYPES:
            raise HttpError(422, f"Unsupported attachment type: {name}")
        if f.size > settings.MAX_ATTACHMENT_BYTES:
            raise HttpError(
                413,
                f"Attachment {name!r} exceeds {settings.MAX_ATTACHMENT_BYTES} bytes",
            )


@router.post("/inbound", response=IngestResponse)
def email_inbound(
    request,
    body: Form[str] = "",
    attachments: List[UploadedFile] = File(default=[]),
):
    """Accept an inbound email (body + attachments); create a ticket idempotently."""
    allowed, retry_after = check_rate_limit(_client_id(request))
    if not allowed:
        raise HttpError(429, f"Rate limit exceeded. Retry in {retry_after}s.")

    files = attachments or []
    _validate_files(files)

    payload = InboundEmailIn(
        body=body or "",
        attachments=[
            AttachmentIn(
                filename=(f.name or "upload"),
                content_type=_file_ext(f.name or "upload"),
                content_base64=base64.b64encode(f.read()).decode(),
            )
            for f in files
        ],
    )

    ticket, outcome = ingest_and_enqueue(payload)

    if ticket is None:
        # Idempotent replay of an email whose ticket was since removed
        # (e.g. a reply whose original ticket was deleted out of band).
        raise HttpError(409, "Duplicate email; the original ticket no longer exists.")

    messages = {
        "created": "Onboarding ticket created; processing started.",
        "reply": f"Reply mapped to existing ticket {ticket.ticket_ref}.",
        "duplicate": "Duplicate email; existing ticket returned.",
    }
    return IngestResponse(
        ticket_id=ticket.ticket_ref,
        status=ticket.status,
        idempotent=(outcome == "duplicate"),
        message=messages[outcome],
    )
