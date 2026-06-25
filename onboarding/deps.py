"""Shared API dependencies (auth)."""

from django.conf import settings
from ninja.security import APIKeyHeader


class AdminAuth(APIKeyHeader):
    """Header API-key auth for admin/mutating endpoints (X-Admin-Key)."""

    param_name = "X-Admin-Key"

    def authenticate(self, request, key):
        if key and key == settings.ADMIN_API_KEY:
            return key
        return None


admin_auth = AdminAuth()
