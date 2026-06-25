"""Ingestion: idempotency, SLA due date, and email thread detection."""

import pytest

from onboarding.models import EventType, Ticket
from onboarding.schemas import InboundEmailIn
from onboarding.services.ingestion import ingest_email


def _payload(body="", attachments=None):
    return InboundEmailIn(body=body, attachments=attachments or [])


@pytest.mark.django_db
def test_duplicate_email_is_idempotent():
    payload = _payload("I am Asha Rao, asha@example.com, 9555544433")
    t1, outcome1 = ingest_email(payload)
    t2, outcome2 = ingest_email(payload)
    assert outcome1 == "created"
    assert outcome2 == "duplicate"
    assert t1.id == t2.id
    assert Ticket.objects.count() == 1


@pytest.mark.django_db
def test_sender_address_captured_from_body_header():
    ticket, _ = ingest_email(
        _payload("From: Asha Rao <asha@example.com>\n\nPlease onboard me.")
    )
    assert ticket.raw_email.from_addr == "asha@example.com"


@pytest.mark.django_db
def test_ticket_created_event_emitted():
    ticket, _ = ingest_email(_payload("I am Asha Rao, asha@example.com, 9555544433"))
    event_types = set(ticket.events.values_list("event_type", flat=True))
    assert EventType.EMAIL_RECEIVED in event_types
    assert EventType.TICKET_CREATED in event_types


@pytest.mark.django_db
def test_sla_due_date_is_set():
    ticket, _ = ingest_email(_payload("hello there"))
    assert ticket.sla_due_at is not None


@pytest.mark.django_db
def test_reply_is_threaded_onto_original():
    original, _ = ingest_email(
        _payload("Message-ID: <m1@mail>\nI am Bob, bob@example.com, 9111122223")
    )
    reply, outcome = ingest_email(_payload("In-Reply-To: <m1@mail>\nthanks, following up"))
    assert outcome == "reply"
    assert reply.id == original.id
    assert Ticket.objects.count() == 1
