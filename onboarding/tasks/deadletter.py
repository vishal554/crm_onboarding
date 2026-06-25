"""Dead-letter queue task: persist a failed task for inspection / reprocessing."""

from celery import shared_task


@shared_task(name="onboarding.record_dead_letter", queue="dead_letter")
def record_dead_letter(ticket_id: int, task_name: str, error: str):
    from onboarding.models import DeadLetter

    DeadLetter.objects.create(
        ticket_id=ticket_id,
        task_name=task_name,
        error=(error or "")[:4000],
    )
    return {"ticket": ticket_id, "task": task_name}
