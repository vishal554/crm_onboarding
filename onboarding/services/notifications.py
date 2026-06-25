"""User notifications: templates + queueing.

A notification is persisted as a queued ``Notification`` row, then a Celery
task on the ``notifications`` queue renders and sends it asynchronously.
"""

# template key -> (subject, body). Body is .format()ed with ticket context.
TEMPLATES = {
    "onboarding_received": (
        "We received your onboarding request",
        "Hi {name},\n\nWe have received your onboarding request "
        "(ref {ref}) and started processing it. We'll be in touch shortly.",
    ),
    "documents_processed": (
        "Your documents have been processed",
        "Hi {name},\n\nThe documents you submitted for {ref} have been "
        "received and processed.",
    ),
    "validation_passed": (
        "Onboarding verification successful",
        "Hi {name},\n\nYour details for {ref} passed verification.",
    ),
    "validation_failed": (
        "Your onboarding needs review",
        "Hi {name},\n\nWe found a discrepancy while verifying {ref}; it is "
        "now under manual review. We'll contact you if we need anything.",
    ),
    "documents_missing": (
        "Documents required for your onboarding",
        "Hi {name},\n\nWe couldn't find the required identity documents for "
        "{ref}. Please reply with a valid document to continue.",
    ),
    "ticket_approved": (
        "Your onboarding is approved",
        "Hi {name},\n\nGood news - your onboarding ({ref}) has been approved.",
    ),
    "ticket_rejected": (
        "Your onboarding could not be completed",
        "Hi {name},\n\nUnfortunately your onboarding request ({ref}) was "
        "rejected. Please reach out if you believe this is an error.",
    ),
}


def render(template: str, ticket) -> tuple[str, str]:
    subject, body = TEMPLATES.get(
        template, ("Onboarding update", "Hi {name},\n\nYour ticket {ref} was updated.")
    )
    context = {
        "name": ticket.applicant_name or "Applicant",
        "ref": ticket.ticket_ref,
        "status": ticket.get_status_display(),
    }
    return subject.format(**context), body.format(**context)


def queue_notification(ticket, template: str, to_addr: str = ""):
    """Persist a queued Notification and dispatch the async send task."""
    from onboarding.models import Notification, NotificationStatus

    recipient = to_addr or ticket.applicant_email or ""
    notification = Notification.objects.create(
        ticket=ticket,
        channel="email",
        template=template,
        to_addr=recipient,
        status=NotificationStatus.QUEUED,
    )

    # Imported here to avoid a circular import at module load.
    from onboarding.tasks.notifications import send_notification

    send_notification.delay(notification.id)
    return notification
