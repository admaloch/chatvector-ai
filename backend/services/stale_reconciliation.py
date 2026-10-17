"""Single-winner stale-document reconciliation for multi-instance API startups.

``fail_stale_documents_global`` must not run on every API replica. A second
startup that races after the first has already started workers would mark
newly in-flight documents as failed.

When ``MULTI_API_INSTANCE`` is true, replicas compete for a Redis ``SET NX``
lock. The winner runs reconciliation; losers skip it entirely (they do not
wait and then run). The lock is left in place until TTL expiry so a replica
that starts a few seconds later still skips.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from core.config import config

logger = logging.getLogger(__name__)

STALE_RECONCILE_LOCK_KEY = "chatvector:stale-reconcile-lock"

FailStaleFn = Callable[[list[str]], Awaitable[set[str]]]


async def acquire_stale_reconcile_lock(
    redis: Any,
    *,
    key: str = STALE_RECONCILE_LOCK_KEY,
    ttl_seconds: int | None = None,
) -> bool:
    """Return True when this process won the Redis startup lock."""
    ttl = (
        config.STALE_RECONCILE_LOCK_TTL_SECONDS
        if ttl_seconds is None
        else ttl_seconds
    )
    acquired = await redis.set(key, "1", nx=True, ex=ttl)
    return bool(acquired)


async def reconcile_stale_documents_on_startup(
    fail_stale: FailStaleFn,
    statuses: list[str],
    *,
    redis: Any | None = None,
    multi_api_instance: bool | None = None,
    lock_key: str = STALE_RECONCILE_LOCK_KEY,
    lock_ttl_seconds: int | None = None,
) -> set[str] | None:
    """Run ``fail_stale`` at most once across concurrent API startups.

    Returns the failed document IDs when this instance ran reconciliation,
    or ``None`` when another instance holds the lock and this instance skipped.
    """
    use_lock = (
        config.MULTI_API_INSTANCE
        if multi_api_instance is None
        else multi_api_instance
    )
    if use_lock:
        if redis is None:
            raise RuntimeError(
                "MULTI_API_INSTANCE=true requires Redis for the stale "
                "reconciler lock."
            )
        won = await acquire_stale_reconcile_lock(
            redis, key=lock_key, ttl_seconds=lock_ttl_seconds
        )
        if not won:
            logger.info(
                "Skipping stale-document reconciliation; another API instance "
                "holds the lock"
            )
            return None

    return await fail_stale(statuses)
