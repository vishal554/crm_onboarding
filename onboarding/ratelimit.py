"""Redis fixed-window rate limiter for the ingestion endpoint.

Protects ``POST /email/inbound`` against abuse / bursts. Synchronous: the
ingestion handler runs in a threadpool under uvicorn, and a sync Redis client
avoids per-request event-loop churn.
"""

import time

import redis
from django.conf import settings

_redis = None


def _client():
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def check_rate_limit(identifier: str):
    """Return ``(allowed, retry_after_seconds)`` for the given client identifier."""
    limit = settings.INGEST_RATE_LIMIT
    window = settings.INGEST_RATE_WINDOW
    bucket = int(time.time()) // window
    key = f"ratelimit:ingest:{identifier}:{bucket}"

    r = _client()
    count = r.incr(key)
    if count == 1:
        r.expire(key, window)
    if count > limit:
        ttl = r.ttl(key)
        return False, max(ttl, 1)
    return True, 0
