"""Data model for the email-driven CRM onboarding system.

PostgreSQL is the source of truth. The DB-level unique constraints below are
what make the system idempotent and deduplicated regardless of app logic:

* ``RawEmail.message_id`` / ``RawEmail.content_hash`` - one ticket per email.
* ``Document.sha256``                                  - attachment dedup.
* ``IdempotencyKey.key``                               - safe request retries.
"""

import uuid

from django.db import models


def make_ticket_ref() -> str:
    """Human-friendly, collision-resistant ticket reference (e.g. TKT-9F3A1C2B)."""
    return f"TKT-{uuid.uuid4().hex[:8].upper()}"


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class TicketStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSING = "processing", "Processing"
    AWAITING_VALIDATION = "awaiting_validation", "Awaiting validation"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"
    REQUIRES_MANUAL_REVIEW = "requires_manual_review", "Requires manual review"


# States that are terminal for SLA / reprocessing purposes.
TERMINAL_TICKET_STATUSES = {
    TicketStatus.APPROVED,
    TicketStatus.REJECTED,
}


class DocumentType(models.TextChoices):
    PDF = "pdf", "PDF"
    JPG = "jpg", "JPG"
    PNG = "png", "PNG"


class DocumentStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    PARSED = "parsed", "Parsed"
    INVALID = "invalid", "Invalid"


class NotificationStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class EventType(models.TextChoices):
    EMAIL_RECEIVED = "email_received", "Email received"
    TICKET_CREATED = "ticket_created", "Ticket created"
    DOCUMENTS_UPLOADED = "documents_uploaded", "Documents uploaded"
    DOCUMENT_PARSED = "document_parsed", "Document parsed"
    VALIDATION_PASSED = "validation_passed", "Validation passed"
    VALIDATION_FAILED = "validation_failed", "Validation failed"
    NOTIFICATION_SENT = "notification_sent", "Notification sent"
    STATUS_UPDATED = "status_updated", "Status updated"
    DUPLICATE_MERGED = "duplicate_merged", "Duplicate merged"
    SLA_BREACHED = "sla_breached", "SLA breached"
    PROCESSING_FAILED = "processing_failed", "Processing failed"
    REPROCESS_REQUESTED = "reprocess_requested", "Reprocess requested"


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class RawEmail(TimestampedModel):
    """The original inbound email, stored before any processing."""

    message_id = models.CharField(max_length=512, unique=True, null=True, blank=True)
    in_reply_to = models.CharField(max_length=512, null=True, blank=True, db_index=True)
    references = models.TextField(null=True, blank=True)
    from_addr = models.EmailField(max_length=320)
    subject = models.CharField(max_length=1024, blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    raw_payload = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64, unique=True, db_index=True)
    received_at = models.DateTimeField()
    # When this email is a reply, the original ticket it was threaded onto.
    thread_ticket = models.ForeignKey(
        "Ticket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="thread_emails",
    )

    class Meta:
        db_table = "raw_emails"
        ordering = ["-received_at"]

    def __str__(self):
        return f"RawEmail<{self.message_id or self.content_hash[:12]}>"


class Ticket(TimestampedModel):
    """A CRM onboarding ticket generated from an inbound email."""

    ticket_ref = models.CharField(
        max_length=20, unique=True, default=make_ticket_ref, editable=False
    )
    status = models.CharField(
        max_length=32, choices=TicketStatus.choices, default=TicketStatus.RECEIVED, db_index=True
    )
    raw_email = models.ForeignKey(
        RawEmail, on_delete=models.PROTECT, related_name="tickets"
    )

    # Applicant data (parsed from email body, reconciled with documents).
    applicant_name = models.CharField(max_length=255, blank=True, default="")
    applicant_email = models.EmailField(max_length=320, blank=True, default="", db_index=True)
    applicant_phone = models.CharField(max_length=32, blank=True, default="", db_index=True)
    applicant_address = models.TextField(blank=True, default="")
    applicant_dob = models.DateField(null=True, blank=True)
    applicant_age = models.PositiveIntegerField(null=True, blank=True)

    # Raw parsed sources for auditability (email-extracted vs document-extracted).
    parsed_source = models.JSONField(default=dict, blank=True)

    sla_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sla_breached = models.BooleanField(default=False)

    class Meta:
        db_table = "tickets"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ticket_ref} ({self.status})"


class Document(TimestampedModel):
    """An attachment linked to a ticket, deduplicated by content hash."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="documents")
    filename = models.CharField(max_length=512)
    content_type = models.CharField(max_length=8, choices=DocumentType.choices)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    storage_path = models.CharField(max_length=1024)
    status = models.CharField(
        max_length=16, choices=DocumentStatus.choices, default=DocumentStatus.UPLOADED
    )
    metadata = models.JSONField(default=dict, blank=True)
    extracted_fields = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "documents"
        ordering = ["created_at"]
        constraints = [
            # Dedupe attachments within a ticket; identical bytes across
            # different tickets are still physically stored once (by the
            # storage layer's hash-addressed path).
            models.UniqueConstraint(
                fields=["ticket", "sha256"], name="uniq_ticket_sha256"
            )
        ]

    def __str__(self):
        return f"{self.filename} ({self.content_type})"


class TicketEvent(models.Model):
    """An immutable entry in a ticket's activity timeline / processing log."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    message = models.CharField(max_length=512, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_events"
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.ticket_id}:{self.event_type}"


class Notification(TimestampedModel):
    """A queued/sent user notification tied to a ticket."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=32, default="email")
    template = models.CharField(max_length=64)
    to_addr = models.CharField(max_length=320)
    status = models.CharField(
        max_length=16, choices=NotificationStatus.choices, default=NotificationStatus.QUEUED
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template} -> {self.to_addr} ({self.status})"


class ValidationResult(models.Model):
    """The outcome of a single validation check run against a ticket."""

    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="validation_results"
    )
    check_name = models.CharField(max_length=64, db_index=True)
    passed = models.BooleanField()
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "validation_results"
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.check_name}={'pass' if self.passed else 'fail'}"


class DeadLetter(models.Model):
    """A pipeline task that failed after exhausting its retries.

    Parked here for inspection and manual reprocessing (the dead-letter queue).
    """

    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="dead_letters"
    )
    task_name = models.CharField(max_length=128, db_index=True)
    error = models.TextField()
    reprocessed = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dead_letters"
        ordering = ["-created_at"]

    def __str__(self):
        return f"DLQ {self.ticket_id}:{self.task_name}"


class IdempotencyKey(models.Model):
    """Maps a request/email dedup key to the ticket it produced."""

    key = models.CharField(max_length=128, unique=True, db_index=True)
    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="idempotency_keys"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "idempotency_keys"

    def __str__(self):
        return self.key
