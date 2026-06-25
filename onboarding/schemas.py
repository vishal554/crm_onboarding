"""Ninja / Pydantic request + response schemas."""

from datetime import date, datetime
from typing import List, Optional

from ninja import Schema


class AttachmentIn(Schema):
    filename: str
    content_type: str            # declared type / extension (pdf, jpg, png)
    content_base64: str          # raw bytes, base64-encoded


class InboundEmailIn(Schema):
    body: str = ""
    attachments: List[AttachmentIn] = []


class IngestResponse(Schema):
    ticket_id: str               # human-friendly ticket_ref
    status: str
    idempotent: bool             # True when this email was a duplicate
    message: str


class TicketActionResponse(Schema):
    ticket_id: str
    status: str
    message: str


class DocumentOut(Schema):
    id: int
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: str
    metadata: dict
    extracted_fields: dict
    created_at: datetime


class TimelineEventOut(Schema):
    event_type: str
    message: str
    payload: dict
    created_at: datetime


class TicketOut(Schema):
    ticket_ref: str
    status: str
    applicant_name: str
    applicant_email: str
    applicant_phone: str
    applicant_address: str
    applicant_dob: Optional[date]
    applicant_age: Optional[int]
    sla_due_at: Optional[datetime]
    sla_breached: bool
    created_at: datetime
    updated_at: datetime


class TicketDetailOut(TicketOut):
    parsed_source: dict
    documents: List[DocumentOut]

    @staticmethod
    def resolve_documents(obj):
        return list(obj.documents.all())


class StatusUpdateIn(Schema):
    status: str
    note: str = ""
