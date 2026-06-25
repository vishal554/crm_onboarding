"""Celery application for the CRM onboarding system.

Workers and Celery Beat both boot from this module. Task queues:
    ingest        - lightweight hand-off work
    pipeline      - main onboarding pipeline (default)
    ocr           - CPU-heavy document extraction (low concurrency)
    notifications - asynchronous user notifications
    dead_letter   - exhausted tasks parked for inspection / reprocessing
"""

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('crm_onboarding')

# Pull CELERY_* settings from Django settings.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Discover tasks.py / tasks/ modules in installed apps.
app.autodiscover_tasks()

# Periodic SLA-breach scan (Celery Beat).
app.conf.beat_schedule = {
    "check-sla": {
        "task": "onboarding.check_sla",
        "schedule": float(os.environ.get("SLA_CHECK_INTERVAL", "60")),
    },
}


@app.task(name='debug.ping')
def ping():
    """Trivial task used to verify the worker is alive."""
    return 'pong'
