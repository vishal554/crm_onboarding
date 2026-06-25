"""API endpoint tests via the Django test client.

(The pipeline is enqueued on commit, which does not fire inside the test
transaction, so these assert the synchronous ingestion + read/admin surface.)
"""

import pytest
from django.test import Client

from onboarding.models import Ticket


@pytest.mark.django_db
def test_inbound_creates_ticket():
    client = Client()
    resp = client.post(
        "/api/email/inbound", {"body": "I am Sara Khan, sara@example.com, 9888877766"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"
    assert Ticket.objects.count() == 1


@pytest.mark.django_db
def test_list_and_detail():
    client = Client()
    client.post("/api/email/inbound", {"body": "I am Sara, sara@example.com"})
    ticket = Ticket.objects.first()

    assert client.get("/api/tickets").status_code == 200
    detail = client.get(f"/api/tickets/{ticket.id}")
    assert detail.status_code == 200
    assert detail.json()["ticket_ref"] == ticket.ticket_ref


@pytest.mark.django_db
def test_duplicate_reply_with_deleted_parent_returns_409_not_500():
    # Reply threads onto an original ticket; if that ticket is later deleted,
    # resending the reply must return a clean 409, not crash with a 500.
    client = Client()
    client.post(
        "/api/email/inbound",
        {"body": "Message-ID: <mx@mail>\nI am Sam, sam@example.com, 9111100000"},
    )
    client.post("/api/email/inbound", {"body": "In-Reply-To: <mx@mail>\nfollowing up"})
    Ticket.objects.all().delete()

    resp = client.post("/api/email/inbound", {"body": "In-Reply-To: <mx@mail>\nfollowing up"})
    assert resp.status_code == 409


@pytest.mark.django_db
def test_status_update_requires_admin_key():
    client = Client()
    client.post("/api/email/inbound", {"body": "hello"})
    ticket = Ticket.objects.first()

    unauth = client.patch(
        f"/api/tickets/{ticket.id}/status",
        data={"status": "approved"},
        content_type="application/json",
    )
    assert unauth.status_code == 401

    ok = client.patch(
        f"/api/tickets/{ticket.id}/status",
        data={"status": "approved"},
        content_type="application/json",
        HTTP_X_ADMIN_KEY="dev-admin-key",
    )
    assert ok.status_code == 200
    ticket.refresh_from_db()
    assert ticket.status == "approved"
