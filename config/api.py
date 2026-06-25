"""Root Django Ninja API instance.

Routers for email ingestion, tickets, and admin are added in later build
steps. For now this exposes a health check used by Docker and smoke tests.
"""

from django.db import connection
from ninja import NinjaAPI

from onboarding.routers.email import router as email_router
from onboarding.routers.tickets import router as tickets_router

api = NinjaAPI(title='CRM Onboarding API', version='0.1.0')

api.add_router('/email', email_router)
api.add_router('/tickets', tickets_router)


@api.get('/health', tags=['system'])
def health(request):
    """Liveness + DB connectivity check."""
    db_ok = True
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
    except Exception:
        db_ok = False
    return {'status': 'ok' if db_ok else 'degraded', 'database': db_ok}
