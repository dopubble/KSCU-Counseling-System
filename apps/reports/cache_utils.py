"""캐시 실패 시 DB/직접 계산으로 폴백 — Redis 장애로 페이지 500 방지."""

from __future__ import annotations

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def safe_cache_get(key: str, default=None):
    try:
        return cache.get(key, default)
    except Exception:
        logger.warning("Cache get failed (key=%s)", key, exc_info=True)
        return default


def safe_cache_set(key: str, value, timeout: int) -> None:
    try:
        cache.set(key, value, timeout)
    except Exception:
        logger.warning("Cache set failed (key=%s)", key, exc_info=True)
