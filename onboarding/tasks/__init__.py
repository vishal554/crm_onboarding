"""Celery tasks for the onboarding app.

Re-exported here so Celery's ``autodiscover_tasks`` (which imports
``onboarding.tasks``) registers every stage.
"""

from onboarding.tasks.deadletter import record_dead_letter
from onboarding.tasks.notifications import send_notification
from onboarding.tasks.sla import check_sla
from onboarding.tasks.pipeline import (
    extract_attachments,
    parse_user_info,
    run_document_extraction,
    run_pipeline,
    run_validation_task,
    store_raw_email,
)

__all__ = [
    "run_pipeline",
    "store_raw_email",
    "extract_attachments",
    "parse_user_info",
    "run_document_extraction",
    "run_validation_task",
    "send_notification",
    "record_dead_letter",
    "check_sla",
]
