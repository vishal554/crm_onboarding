"""Validation workflow outcomes."""

import pytest
from django.utils import timezone

from onboarding.models import (
    Document,
    DocumentStatus,
    RawEmail,
    Ticket,
    TicketStatus,
)
from onboarding.services.validation import run_validation


def _ticket(content_hash, parsed_source=None):
    raw = RawEmail.objects.create(
        content_hash=content_hash, body_text="", received_at=timezone.now()
    )
    return Ticket.objects.create(raw_email=raw, parsed_source=parsed_source or {})


def _add_doc(ticket, sha):
    Document.objects.create(
        ticket=ticket,
        filename="a.png",
        content_type="png",
        size_bytes=10,
        sha256=sha,
        storage_path="/tmp/a.png",
        status=DocumentStatus.PARSED,
        metadata={"format_valid": True},
    )


@pytest.mark.django_db
def test_missing_documents_rejected():
    ticket = _ticket("val-1")
    status, *_ = run_validation(ticket)
    assert status == TicketStatus.REJECTED


@pytest.mark.django_db
def test_name_mismatch_routes_to_manual_review():
    ticket = _ticket(
        "val-2",
        {"email": {"name": "Bob Brown"}, "document": {"name": "Jane Smith"}},
    )
    _add_doc(ticket, "val-2-sha")
    status, *_ = run_validation(ticket)
    assert status == TicketStatus.REQUIRES_MANUAL_REVIEW


@pytest.mark.django_db
def test_consistent_data_approved():
    ticket = _ticket(
        "val-3",
        {"email": {"name": "Jane Smith"}, "document": {"name": "Jane Smith"}},
    )
    _add_doc(ticket, "val-3-sha")
    status, *_ = run_validation(ticket)
    assert status == TicketStatus.APPROVED


@pytest.mark.django_db
def test_address_mismatch_routes_to_manual_review():
    ticket = _ticket(
        "val-addr-1",
        {
            "email": {"name": "Jane Smith", "address": "12 Hill Road, Pune"},
            "document": {
                "name": "Jane Smith",
                "address": "45 Park Street, Mumbai, Maharashtra - 400058",
            },
        },
    )
    _add_doc(ticket, "val-addr-1-sha")
    status, *_ = run_validation(ticket)
    assert status == TicketStatus.REQUIRES_MANUAL_REVIEW


@pytest.mark.django_db
def test_matching_address_approved():
    ticket = _ticket(
        "val-addr-2",
        {
            "email": {"name": "Jane Smith", "address": "45 Park Street, Mumbai"},
            "document": {
                "name": "Jane Smith",
                "address": "S/O X, 45 Park Street, Andheri, Mumbai - 400058",
            },
        },
    )
    _add_doc(ticket, "val-addr-2-sha")
    status, *_ = run_validation(ticket)
    assert status == TicketStatus.APPROVED


@pytest.mark.django_db
def test_duplicate_identity_document_routes_to_manual_review():
    # Same document bytes (hash) submitted under two different applicants:
    # the first stays clean, the second must be flagged as a duplicate.
    shared_sha = "shared-doc-sha"
    first = _ticket(
        "val-4a",
        {"email": {"name": "Jane Smith"}, "document": {"name": "Jane Smith"}},
    )
    _add_doc(first, shared_sha)
    assert run_validation(first)[0] == TicketStatus.APPROVED

    second = _ticket(
        "val-4b",
        {"email": {"name": "Bob Brown"}, "document": {"name": "Bob Brown"}},
    )
    _add_doc(second, shared_sha)
    status, _event, _msg, duplicate = run_validation(second)
    assert status == TicketStatus.REQUIRES_MANUAL_REVIEW
    assert duplicate is not None and duplicate.id == first.id
