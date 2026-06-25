"""SLA monitoring - a Celery Beat task that flags overdue tickets."""

from celery import shared_task


@shared_task(name="onboarding.check_sla", queue="pipeline")
def check_sla():
    """Flag unresolved tickets whose SLA window has elapsed."""
    from django.utils import timezone

    from onboarding.models import (
        EventType,
        TERMINAL_TICKET_STATUSES,
        Ticket,
    )
    from onboarding.services.events import add_event

    now = timezone.now()
    overdue = Ticket.objects.filter(
        sla_due_at__lt=now, sla_breached=False
    ).exclude(status__in=list(TERMINAL_TICKET_STATUSES))

    breached = 0
    for ticket in overdue:
        ticket.sla_breached = True
        ticket.save(update_fields=["sla_breached", "updated_at"])
        add_event(
            ticket,
            EventType.SLA_BREACHED,
            f"SLA breached - not resolved by {ticket.sla_due_at.isoformat()}",
            sla_due_at=ticket.sla_due_at.isoformat(),
        )
        breached += 1

    return {"breached": breached}
