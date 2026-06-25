"""Ticket APIs: list/detail/timeline/documents (read) + status & reprocess.

Read endpoints are open; mutating admin actions (status change, reprocess)
require the X-Admin-Key header.
"""

from typing import List, Optional

from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination, paginate

from onboarding.deps import admin_auth
from onboarding.models import EventType, Ticket, TicketStatus
from onboarding.schemas import (
    DocumentOut,
    StatusUpdateIn,
    TicketActionResponse,
    TicketDetailOut,
    TicketOut,
    TimelineEventOut,
)
from onboarding.services.events import add_event

router = Router(tags=["tickets"])


def _get_ticket(ticket_id: int) -> Ticket:
    ticket = Ticket.objects.filter(id=ticket_id).first()
    if ticket is None:
        raise HttpError(404, "Ticket not found")
    return ticket


@router.get("", response=List[TicketOut])
@paginate(PageNumberPagination)
def list_tickets(request, status: Optional[str] = None):
    """List tickets (paginated), optionally filtered by status."""
    qs = Ticket.objects.all().order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    return qs


@router.get("/{ticket_id}", response=TicketDetailOut)
def get_ticket(request, ticket_id: int):
    """Full ticket detail including documents and OCR-extracted fields."""
    return _get_ticket(ticket_id)


@router.get("/{ticket_id}/timeline", response=List[TimelineEventOut])
def ticket_timeline(request, ticket_id: int):
    """Ordered activity timeline / processing log for a ticket."""
    return _get_ticket(ticket_id).events.all()


@router.get("/{ticket_id}/documents", response=List[DocumentOut])
def ticket_documents(request, ticket_id: int):
    """Documents attached to a ticket, with metadata and extracted fields."""
    return _get_ticket(ticket_id).documents.all()


@router.patch("/{ticket_id}/status", response=TicketActionResponse, auth=admin_auth)
def update_status(request, ticket_id: int, payload: StatusUpdateIn):
    """Admin override of a ticket's status (audited on the timeline)."""
    ticket = _get_ticket(ticket_id)
    valid = {s.value for s in TicketStatus}
    if payload.status not in valid:
        raise HttpError(422, f"Invalid status. Valid values: {sorted(valid)}")

    old = ticket.status
    ticket.status = payload.status
    ticket.save(update_fields=["status", "updated_at"])
    message = f"Status changed {old} -> {payload.status} (admin)"
    if payload.note:
        message += f": {payload.note}"
    add_event(
        ticket,
        EventType.STATUS_UPDATED,
        message,
        old=old,
        new=payload.status,
        note=payload.note,
    )
    return TicketActionResponse(
        ticket_id=ticket.ticket_ref, status=ticket.status, message="Status updated."
    )


@router.post("/{ticket_id}/reprocess", response=TicketActionResponse, auth=admin_auth)
def reprocess_ticket(request, ticket_id: int):
    """Re-run the processing pipeline for a ticket (e.g. after a failure)."""
    ticket = _get_ticket(ticket_id)

    add_event(ticket, EventType.REPROCESS_REQUESTED, "Manual reprocess requested")
    ticket.status = TicketStatus.PROCESSING
    ticket.save(update_fields=["status", "updated_at"])
    ticket.dead_letters.filter(reprocessed=False).update(reprocessed=True)

    from onboarding.tasks import run_pipeline

    run_pipeline.delay(ticket.id)

    return TicketActionResponse(
        ticket_id=ticket.ticket_ref,
        status=ticket.status,
        message="Reprocessing started.",
    )
