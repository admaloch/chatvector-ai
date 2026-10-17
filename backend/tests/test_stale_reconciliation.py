"""Single-winner stale reconciliation for concurrent API startups."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core.config import Settings, _validate_multi_api_instance
from services.stale_reconciliation import (
    STALE_RECONCILE_LOCK_KEY,
    acquire_stale_reconcile_lock,
    reconcile_stale_documents_on_startup,
)


class _MemoryLockRedis:
    """Minimal Redis stand-in that implements atomic SET NX."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        del ex
        async with self._lock:
            if nx and key in self._store:
                return False
            self._store[key] = value
            return True


@pytest.mark.asyncio
async def test_concurrent_reconcilers_do_not_double_fail_inflight():
    """Two API startups: only the lock winner mutates in-flight document status."""
    statuses = {"doc-stale": "extracting"}
    fail_calls = 0

    async def fail_stale(_statuses: list[str]) -> set[str]:
        nonlocal fail_calls
        fail_calls += 1
        if fail_calls == 1:
            statuses["doc-stale"] = "failed"
            # New work starts after the first reconcile — a second sweep
            # would incorrectly fail this document.
            statuses["doc-new"] = "extracting"
            return {"doc-stale"}
        corrupted = {
            doc_id
            for doc_id, status in statuses.items()
            if status in ("extracting", "queued")
        }
        for doc_id in corrupted:
            statuses[doc_id] = "failed"
        return corrupted

    redis = _MemoryLockRedis()
    first, second = await asyncio.gather(
        reconcile_stale_documents_on_startup(
            fail_stale,
            ["extracting", "queued"],
            redis=redis,
            multi_api_instance=True,
        ),
        reconcile_stale_documents_on_startup(
            fail_stale,
            ["extracting", "queued"],
            redis=redis,
            multi_api_instance=True,
        ),
    )

    winners = [result for result in (first, second) if result is not None]
    assert fail_calls == 1
    assert winners == [{"doc-stale"}]
    assert statuses["doc-stale"] == "failed"
    assert statuses["doc-new"] == "extracting"


@pytest.mark.asyncio
async def test_lock_loser_skips_without_waiting_to_run():
    redis = _MemoryLockRedis()
    assert await acquire_stale_reconcile_lock(redis) is True
    assert await acquire_stale_reconcile_lock(redis) is False

    fail_stale = AsyncMock(return_value={"doc-1"})
    skipped = await reconcile_stale_documents_on_startup(
        fail_stale,
        ["extracting"],
        redis=redis,
        multi_api_instance=True,
        lock_key=STALE_RECONCILE_LOCK_KEY,
    )
    assert skipped is None
    fail_stale.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_instance_reconciles_without_lock():
    fail_stale = AsyncMock(return_value={"doc-1"})
    result = await reconcile_stale_documents_on_startup(
        fail_stale,
        ["extracting"],
        redis=None,
        multi_api_instance=False,
    )
    assert result == {"doc-1"}
    fail_stale.assert_awaited_once_with(["extracting"])


@pytest.mark.asyncio
async def test_multi_instance_requires_redis():
    with pytest.raises(RuntimeError, match="requires Redis"):
        await reconcile_stale_documents_on_startup(
            AsyncMock(),
            ["extracting"],
            redis=None,
            multi_api_instance=True,
        )


def test_multi_api_instance_requires_redis_and_s3():
    settings = Settings()
    settings.MULTI_API_INSTANCE = True
    settings.QUEUE_BACKEND = "memory"
    settings.ARTIFACT_STORAGE = "local"
    with pytest.raises(ValueError, match="QUEUE_BACKEND=redis"):
        _validate_multi_api_instance(settings)

    settings.QUEUE_BACKEND = "redis"
    with pytest.raises(ValueError, match="ARTIFACT_STORAGE=s3"):
        _validate_multi_api_instance(settings)

    settings.ARTIFACT_STORAGE = "s3"
    _validate_multi_api_instance(settings)


@pytest.mark.asyncio
async def test_lifespan_skips_fail_stale_when_lock_lost():
    from fastapi import FastAPI

    from db.migration_ledger import migration_filenames
    from main import lifespan

    app = FastAPI()
    fail_stale = AsyncMock(return_value={"should-not-run"})

    with patch("main.config.APP_ENV", "test"), \
         patch("main.config.MULTI_API_INSTANCE", True), \
         patch("main.config.QUEUE_BACKEND", "redis"), \
         patch("main.config.DATABASE_URL", "postgresql+asyncpg://x"), \
         patch(
             "main._read_migration_ledger_with_retry",
             new_callable=AsyncMock,
             return_value=migration_filenames(),
         ), \
         patch("main.db.fail_stale_documents_global", fail_stale), \
         patch(
             "services.stale_reconciliation.acquire_stale_reconcile_lock",
             new_callable=AsyncMock,
             return_value=False,
         ), \
         patch("core.clients.redis_client.ping", new_callable=AsyncMock), \
         patch(
             "services.api_key_service.bootstrap_development_tenant",
             new_callable=AsyncMock,
         ), \
         patch("main.ingestion_queue.start", new_callable=AsyncMock), \
         patch("main.ingestion_queue.stop", new_callable=AsyncMock), \
         patch("main.ingestion_queue.clear_stale_jobs", return_value=0):
        async with lifespan(app):
            pass

    fail_stale.assert_not_awaited()
