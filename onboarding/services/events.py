"""Helper for appending to a ticket's activity timeline / processing log."""

from onboarding.models import TicketEvent


def add_event(ticket, event_type, message="", **payload):
    """Create a TicketEvent row. Used by every pipeline stage and API action."""
    return TicketEvent.objects.create(
        ticket=ticket,
        event_type=event_type,
        message=message,
        payload=payload or {},
    )
