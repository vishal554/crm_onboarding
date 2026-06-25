"""Asynchronous notification delivery (the ``notifications`` queue)."""

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone


@shared_task(
    name="onboarding.send_notification",
    queue="notifications",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def send_notification(self, notification_id: int):
    from onboarding.models import EventType, Notification, NotificationStatus
    from onboarding.services.events import add_event
    from onboarding.services.notifications import render

    notification = Notification.objects.select_related("ticket").get(id=notification_id)

    if not notification.to_addr:
        notification.status = NotificationStatus.FAILED
        notification.save(update_fields=["status", "updated_at"])
        add_event(
            notification.ticket,
            EventType.NOTIFICATION_SENT,
            f"Notification '{notification.template}' skipped - no recipient address",
            template=notification.template,
            delivered=False,
        )
        return {"notification": notification_id, "sent": False, "reason": "no recipient"}

    subject, body = render(notification.template, notification.ticket)
    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [notification.to_addr],
            fail_silently=False,
        )
    except Exception as exc:
        notification.status = NotificationStatus.FAILED
        notification.save(update_fields=["status", "updated_at"])
        raise self.retry(exc=exc)

    notification.status = NotificationStatus.SENT
    notification.sent_at = timezone.now()
    notification.save(update_fields=["status", "sent_at", "updated_at"])
    add_event(
        notification.ticket,
        EventType.NOTIFICATION_SENT,
        f"Sent '{notification.template}' to {notification.to_addr}",
        template=notification.template,
        delivered=True,
    )
    return {"notification": notification_id, "sent": True}
