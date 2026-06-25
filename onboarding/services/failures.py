"""Failure handling: mark a ticket failed and park the task in the DLQ."""


def mark_failed(ticket_id, task_name: str, exc) -> None:
    """Called when a pipeline task exhausts its retries.

    Moves the ticket to ``failed``, logs the failure on its timeline, and routes
    a record onto the dead-letter queue for inspection / reprocessing.
    """
    from onboarding.models import EventType, Ticket, TicketStatus
    from onboarding.services.events import add_event

    try:
        ticket = Ticket.objects.get(id=ticket_id)
    except Ticket.DoesNotExist:
        return

    ticket.status = TicketStatus.FAILED
    ticket.save(update_fields=["status", "updated_at"])
    add_event(
        ticket,
        EventType.PROCESSING_FAILED,
        f"{task_name} failed after retries: {exc}",
        task=task_name,
        error=str(exc),
    )

    from onboarding.tasks.deadletter import record_dead_letter

    record_dead_letter.apply_async(
        args=[ticket_id, task_name, str(exc)], queue="dead_letter"
    )
