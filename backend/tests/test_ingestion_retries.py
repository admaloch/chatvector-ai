"""Retryable ingestion stays non-terminal until retries are exhausted."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import config
from db.base import REPLACEMENT_IN_PROGRESS
from services.ingestion_pipeline import IngestionPipeline, UploadPipelineError
from services.providers.base import ProviderAuthError, ProviderTimeoutError
from services.queue_asyncio import AsyncioIngestionQueue
from services.queue_base import QueueJob


def _job(**overrides) -> QueueJob:
    kwargs = dict(
        doc_id="doc-retry",
        file_name="f.pdf",
        content_type="application/pdf",
        file_bytes=b"x",
        tenant_id="tenant-1",
    )
    kwargs.update(overrides)
    return QueueJob(**kwargs)


@pytest.fixture
def webhook_on(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ENABLED", True)
    monkeypatch.setattr(config, "QUEUE_JOB_MAX_RETRIES", 2)


@pytest.mark.asyncio
async def test_retryable_first_attempt_does_not_emit_terminal_failure(webhook_on):
    pipeline = IngestionPipeline()
    update_status = AsyncMock()
    notify = AsyncMock()
    with (
        patch("db.update_document_status", update_status),
        patch("db.delete_document_chunks", new=AsyncMock()),
        patch("services.ingestion_webhook.notify_ingestion_terminal", notify),
    ):
        await pipeline._handle_error(
            doc_id="doc-1",
            stage="embedding",
            exc=ProviderTimeoutError("timed out"),
            tenant_id="tenant-1",
            attempt=0,
        )

    notify.assert_not_awaited()
    update_status.assert_awaited_once()
    assert update_status.await_args.kwargs["status"] == "retrying"
    assert "job_metadata_patch" not in update_status.await_args.kwargs


@pytest.mark.asyncio
async def test_exhaustion_emits_exactly_one_terminal_failed_event(webhook_on):
    pipeline = IngestionPipeline()
    update_status = AsyncMock()
    notify = AsyncMock()
    with (
        patch("db.update_document_status", update_status),
        patch("db.delete_document_chunks", new=AsyncMock()),
        patch("services.ingestion_webhook.notify_ingestion_terminal", notify),
    ):
        await pipeline._handle_error(
            doc_id="doc-1",
            stage="embedding",
            exc=ProviderTimeoutError("timed out"),
            tenant_id="tenant-1",
            attempt=config.QUEUE_JOB_MAX_RETRIES,
        )

    update_status.assert_awaited_once()
    assert update_status.await_args.kwargs["status"] == "failed"
    assert update_status.await_args.kwargs["job_metadata_patch"]["failure_class"] == (
        "provider_timeout"
    )
    notify.assert_awaited_once_with("doc-1", "tenant-1", outcome="failed")


@pytest.mark.asyncio
async def test_non_retryable_error_is_terminal_on_first_attempt(webhook_on):
    pipeline = IngestionPipeline()
    notify = AsyncMock()
    with (
        patch("db.update_document_status", new=AsyncMock()),
        patch("db.delete_document_chunks", new=AsyncMock()),
        patch("services.ingestion_webhook.notify_ingestion_terminal", notify),
    ):
        await pipeline._handle_error(
            doc_id="doc-1",
            stage="embedding",
            exc=ProviderAuthError("bad key"),
            tenant_id="tenant-1",
            attempt=0,
        )

    notify.assert_awaited_once_with("doc-1", "tenant-1", outcome="failed")


@pytest.mark.asyncio
async def test_successful_completion_emits_one_completed_event(webhook_on):
    pipeline = IngestionPipeline()
    notify = AsyncMock()
    with (
        patch("db.update_document_status", new=AsyncMock()),
        patch("services.ingestion_webhook.notify_ingestion_terminal", notify),
    ):
        await pipeline._handle_error(
            doc_id="doc-1",
            stage="embedding",
            exc=ProviderTimeoutError("timed out"),
            tenant_id="tenant-1",
            attempt=0,
        )
        await pipeline._update_status(
            doc_id="doc-1",
            status="completed",
            tenant_id="tenant-1",
        )

    notify.assert_awaited_once_with("doc-1", "tenant-1", outcome="completed")


@pytest.mark.asyncio
async def test_validation_error_is_terminal_even_with_retries_left(webhook_on):
    pipeline = IngestionPipeline()
    notify = AsyncMock()
    with (
        patch("db.update_document_status", new=AsyncMock()),
        patch("db.delete_document_chunks", new=AsyncMock()),
        patch("services.ingestion_webhook.notify_ingestion_terminal", notify),
    ):
        await pipeline._handle_error(
            doc_id="doc-1",
            stage="extracting",
            exc=UploadPipelineError(
                status_code=422,
                code="no_text_extracted",
                stage="extracting",
                message="No extractable text was found in the uploaded document.",
            ),
            tenant_id="tenant-1",
            attempt=0,
        )

    notify.assert_awaited_once_with("doc-1", "tenant-1", outcome="failed")


@pytest.mark.asyncio
async def test_retryable_replacement_stays_in_progress(webhook_on):
    pipeline = IngestionPipeline()
    set_detail = AsyncMock()
    notify = AsyncMock()
    with (
        patch("db.set_document_status_detail", set_detail),
        patch("services.ingestion_webhook.notify_ingestion_terminal", notify),
    ):
        await pipeline._handle_error(
            doc_id="doc-1",
            stage="embedding",
            exc=ProviderTimeoutError("timed out"),
            tenant_id="tenant-1",
            replace=True,
            attempt=0,
        )

    notify.assert_not_awaited()
    set_detail.assert_awaited_once()
    assert set_detail.await_args.args[1] == REPLACEMENT_IN_PROGRESS


@pytest.mark.asyncio
async def test_asyncio_queue_retries_without_terminal_webhook(webhook_on):
    service = AsyncioIngestionQueue()
    service._rate_limiter.acquire = AsyncMock()
    pipeline = IngestionPipeline()
    notify = AsyncMock()
    update_status = AsyncMock()

    async def fail_then_succeed(**kwargs):
        if pipeline_calls["n"] == 0:
            pipeline_calls["n"] += 1
            await pipeline._handle_error(
                doc_id=kwargs["doc_id"],
                stage="embedding",
                exc=ProviderTimeoutError("timed out"),
                tenant_id=kwargs["tenant_id"],
                attempt=kwargs["attempt"],
            )
            raise ProviderTimeoutError("timed out")
        await pipeline._update_status(
            doc_id=kwargs["doc_id"],
            status="completed",
            tenant_id=kwargs["tenant_id"],
        )

    pipeline_calls = {"n": 0}
    mock_pipeline_cls = MagicMock()
    mock_pipeline_cls.return_value.process_document_background = AsyncMock(
        side_effect=fail_then_succeed
    )

    with (
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch("services.queue_asyncio.db.update_document_status", new=update_status),
        patch("db.update_document_status", new=update_status),
        patch("services.queue_asyncio.asyncio.sleep", new=AsyncMock()),
        patch("services.ingestion_webhook.notify_ingestion_terminal", notify),
    ):
        await service.start()
        try:
            await service.enqueue(_job())
            await asyncio.wait_for(service._queue.join(), timeout=3.0)
        finally:
            await service.stop()

    assert pipeline_calls["n"] == 1
    retrying = [
        call for call in update_status.await_args_list
        if call.kwargs.get("status") == "retrying"
    ]
    failed = [
        call for call in update_status.await_args_list
        if call.kwargs.get("status") == "failed"
    ]
    assert retrying
    assert failed == []
    notify.assert_awaited_once_with("doc-retry", "tenant-1", outcome="completed")


@pytest.mark.asyncio
async def test_redis_queue_retries_without_terminal_webhook(webhook_on):
    import services.queue_redis as queue_redis

    @asynccontextmanager
    async def fake_worker_db_context():
        yield

    pipeline = IngestionPipeline()
    notify = AsyncMock()
    update_status = AsyncMock()
    storage = MagicMock()
    storage.get.return_value = b"bytes"
    rq_queue = MagicMock()
    rq_queue.__len__.return_value = 0

    async def fail_once(**kwargs):
        await pipeline._handle_error(
            doc_id=kwargs["doc_id"],
            stage="embedding",
            exc=ProviderTimeoutError("timed out"),
            tenant_id=kwargs["tenant_id"],
            attempt=kwargs["attempt"],
        )
        raise ProviderTimeoutError("timed out")

    mock_pipeline_cls = MagicMock()
    mock_pipeline_cls.return_value.process_document_background = AsyncMock(
        side_effect=fail_once
    )

    with (
        patch("db.worker_db_context", fake_worker_db_context),
        patch("db.update_document_status", update_status),
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch.object(queue_redis, "get_artifact_storage", return_value=storage),
        patch.object(queue_redis, "_push_dlq_entry"),
        patch.object(queue_redis.redis_lib.Redis, "from_url", return_value=MagicMock()),
        patch.object(queue_redis, "RQQueue", return_value=rq_queue),
        patch.object(queue_redis.asyncio, "sleep", new=AsyncMock()),
        patch("services.ingestion_webhook.notify_ingestion_terminal", notify),
    ):
        await queue_redis._async_execute_job(
            "doc-redis",
            "f.pdf",
            "application/pdf",
            "artifact://doc-redis",
            0,
            "tenant-1",
        )

    notify.assert_not_awaited()
    statuses = [call.kwargs.get("status") for call in update_status.await_args_list]
    assert "failed" not in statuses
    assert "retrying" in statuses
    rq_queue.enqueue.assert_called_once()
