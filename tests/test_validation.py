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


def _ticket(content_hash, parsed_source=None, email="", phone=""):
    raw = RawEmail.objects.create(
        content_hash=content_hash, body_text="", received_at=timezone.now()
    )
    return Ticket.objects.create(
        raw_email=raw,
        parsed_source=parsed_source or {},
        applicant_email=email,
        applicant_phone=phone,
    )


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
def test_same_applicant_resubmission_is_duplicate():
    # Same applicant (email + phone) and same document -> flagged as duplicate.
    src = {"email": {"name": "Jane Smith"}, "document": {"name": "Jane Smith"}}
    first = _ticket("val-4a", src, email="jane@example.com", phone="9000000001")
    _add_doc(first, "shared-sha")
    assert run_validation(first)[0] == TicketStatus.APPROVED

    second = _ticket("val-4b", src, email="jane@example.com", phone="9000000001")
    _add_doc(second, "shared-sha")
    status, _event, _msg, duplicate = run_validation(second)
    assert status == TicketStatus.REQUIRES_MANUAL_REVIEW
    assert duplicate is not None and duplicate.id == first.id


@pytest.mark.django_db
def test_same_document_different_applicant_not_duplicate():
    # Same document bytes but a different applicant (email + phone differ):
    # under the all-details rule this is NOT a duplicate.
    src = {"email": {"name": "Jane Smith"}, "document": {"name": "Jane Smith"}}
    first = _ticket("val-5a", src, email="jane@example.com", phone="9000000001")
    _add_doc(first, "shared-sha-2")
    assert run_validation(first)[0] == TicketStatus.APPROVED

    bob = {"email": {"name": "Bob Brown"}, "document": {"name": "Bob Brown"}}
    second = _ticket("val-5b", bob, email="bob@example.com", phone="9000000002")
    _add_doc(second, "shared-sha-2")
    status, _event, _msg, duplicate = run_validation(second)
    assert status == TicketStatus.APPROVED
    assert duplicate is None


@pytest.mark.django_db
def test_shared_phone_alone_not_duplicate():
    # Same phone but different email is not enough -> not a duplicate.
    src = {"email": {"name": "Jane Smith"}, "document": {"name": "Jane Smith"}}
    first = _ticket("val-6a", src, email="jane@example.com", phone="9000000009")
    _add_doc(first, "sha-6a")
    assert run_validation(first)[0] == TicketStatus.APPROVED

    second = _ticket("val-6b", src, email="other@example.com", phone="9000000009")
    _add_doc(second, "sha-6b")
    status, *_ = run_validation(second)
    assert status == TicketStatus.APPROVED
