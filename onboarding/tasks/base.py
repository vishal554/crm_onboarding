"""Base Celery task for pipeline stages: auto-retry then dead-letter."""

from celery import Task


class PipelineTask(Task):
    """Retries transient failures with exponential backoff + jitter; on final
    failure routes the ticket to ``failed`` and the dead-letter queue."""

    autoretry_for = (Exception,)
    max_retries = 3
    retry_backoff = True          # 1s, 2s, 4s ...
    retry_backoff_max = 60
    retry_jitter = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        ticket_id = args[0] if args else kwargs.get("ticket_id")
        if ticket_id is not None:
            from onboarding.services.failures import mark_failed

            mark_failed(ticket_id, self.name, exc)
