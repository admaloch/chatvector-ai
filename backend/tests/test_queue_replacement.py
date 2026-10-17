"""Queue behavior for replacement jobs: `status` must never leave `completed`."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.base import REPLACEMENT_FAILED, REPLACEMENT_IN_PROGRESS
from services.queue_asyncio import AsyncioIngestionQueue
from services.queue_base import QueueJob


@pytest.fixture(autouse=True)
def _force_memory_backend(monkeypatch):
    from services.queue_service import _reset_queue_singleton

    _reset_queue_singleton()
    monkeypatch.setenv("QUEUE_BACKEND", "memory")
    yield
    _reset_queue_singleton()


def _replace_job(doc_id: str = "doc-replace", **overrides) -> QueueJob:
    kwargs = dict(
        doc_id=doc_id,
        file_name="v2.pdf",
        content_type="application/pdf",
        file_bytes=b"new-bytes",
        tenant_id="tenant-1",
        replace=True,
        metadata={"rev": 2},
    )
    kwargs.update(overrides)
    return QueueJob(**kwargs)


def test_queue_job_defaults_are_not_replacements():
    job = QueueJob(doc_id="d", file_name="f.pdf", content_type="application/pdf", file_bytes=b"x")
    assert job.replace is False
    assert job.metadata is None


@pytest.mark.asyncio
async def test_worker_forwards_replace_and_metadata_to_pipeline(monkeypatch):
    service = AsyncioIngestionQueue()
    service._rate_limiter.acquire = AsyncMock()

    mock_pipeline_cls = MagicMock()
    pipeline = mock_pipeline_cls.return_value
    pipeline.process_document_background = AsyncMock()

    with patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls):
        await service.start()
        try:
            await service.enqueue(_replace_job())
            await asyncio.wait_for(service._queue.join(), timeout=3.0)
        finally:
            await service.stop()

    kwargs = pipeline.process_document_background.await_args.kwargs
    assert kwargs["replace"] is True
    assert kwargs["metadata"] == {"rev": 2}


@pytest.mark.asyncio
async def test_retry_of_replacement_does_not_set_retrying_status(monkeypatch):
    monkeypatch.setattr("services.queue_asyncio.config.QUEUE_JOB_MAX_RETRIES", 1)

    service = AsyncioIngestionQueue()
    service._rate_limiter.acquire = AsyncMock()

    mock_pipeline_cls = MagicMock()
    pipeline = mock_pipeline_cls.return_value
    pipeline.process_document_background = AsyncMock(
        side_effect=[RuntimeError("embedding API unavailable"), None]
    )
    update_status = AsyncMock()
    set_detail = AsyncMock()

    with (
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch("services.queue_asyncio.db.update_document_status", new=update_status),
        patch("services.queue_asyncio.db.set_document_status_detail", new=set_detail),
        patch("services.queue_asyncio.asyncio.sleep", new=AsyncMock()),
    ):
        await service.start()
        try:
            await service.enqueue(_replace_job("doc-retry"))
            await asyncio.wait_for(service._queue.join(), timeout=3.0)
        finally:
            await service.stop()

    assert pipeline.process_document_background.await_count == 2
    update_status.assert_not_awaited()
    set_detail.assert_awaited_once_with(
        "doc-retry",
        REPLACEMENT_IN_PROGRESS,
        tenant_id="tenant-1",
        job_metadata_patch={"attempt": 2},
    )


@pytest.mark.asyncio
async def test_retry_of_regular_job_still_sets_retrying_status(monkeypatch):
    monkeypatch.setattr("services.queue_asyncio.config.QUEUE_JOB_MAX_RETRIES", 1)

    service = AsyncioIngestionQueue()
    service._rate_limiter.acquire = AsyncMock()

    mock_pipeline_cls = MagicMock()
    pipeline = mock_pipeline_cls.return_value
    pipeline.process_document_background = AsyncMock(
        side_effect=[RuntimeError("embedding API unavailable"), None]
    )
    update_status = AsyncMock()
    set_detail = AsyncMock()

    with (
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch("services.queue_asyncio.db.update_document_status", new=update_status),
        patch("services.queue_asyncio.db.set_document_status_detail", new=set_detail),
        patch("services.queue_asyncio.asyncio.sleep", new=AsyncMock()),
    ):
        await service.start()
        try:
            await service.enqueue(
                QueueJob(
                    doc_id="doc-regular",
                    file_name="f.pdf",
                    content_type="application/pdf",
                    file_bytes=b"x",
                    tenant_id="tenant-1",
                )
            )
            await asyncio.wait_for(service._queue.join(), timeout=3.0)
        finally:
            await service.stop()

    update_status.assert_awaited_once()
    assert update_status.await_args.kwargs["status"] == "retrying"
    set_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_replacement_requeue_on_full_queue_records_replacement_failed(monkeypatch):
    monkeypatch.setattr("services.queue_asyncio.config.QUEUE_MAX_SIZE", 1)
    monkeypatch.setattr("services.queue_asyncio.config.QUEUE_WORKER_COUNT", 1)
    monkeypatch.setattr("services.queue_asyncio.config.QUEUE_JOB_MAX_RETRIES", 2)

    service = AsyncioIngestionQueue()
    service._rate_limiter.acquire = AsyncMock()

    call_count = 0

    async def fail_and_fill_queue(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            service._queue.put_nowait(
                QueueJob(
                    doc_id="blocker",
                    file_name="b.pdf",
                    content_type="application/pdf",
                    file_bytes=b"b",
                )
            )
            raise RuntimeError("transient unavailable")
        return None

    mock_pipeline_cls = MagicMock()
    pipeline = mock_pipeline_cls.return_value
    pipeline.process_document_background = AsyncMock(side_effect=fail_and_fill_queue)
    update_status = AsyncMock()
    set_detail = AsyncMock()

    with (
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch("services.queue_asyncio.db.update_document_status", new=update_status),
        patch("services.queue_asyncio.db.set_document_status_detail", new=set_detail),
        patch("services.queue_asyncio.asyncio.sleep", new=AsyncMock()),
    ):
        await service.start()
        try:
            await service.enqueue(_replace_job("doc-full"))
            await asyncio.wait_for(service._queue.join(), timeout=3.0)
        finally:
            await service.stop()

    update_status.assert_not_awaited()
    failure_calls = [c for c in set_detail.await_args_list if c.args[1] == REPLACEMENT_FAILED]
    assert len(failure_calls) == 1
    assert failure_calls[0].kwargs["error"]["stage"] == "queued"
    assert any(entry.doc_id == "doc-full" for entry in service.dlq_jobs())


# ---------------------------------------------------------------------------
# Redis worker bridge (no Redis server needed: db + pipeline are mocked and the
# payload failures return before the retry path touches RQ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_job_missing_payload_marks_replacement_failed_not_document_failed(
    monkeypatch,
):
    from contextlib import asynccontextmanager

    import services.queue_redis as queue_redis

    @asynccontextmanager
    async def fake_worker_db_context():
        yield

    update_status = AsyncMock()
    set_detail = AsyncMock()

    with (
        patch("db.worker_db_context", fake_worker_db_context),
        patch("db.update_document_status", update_status),
        patch("db.set_document_status_detail", set_detail),
        patch.object(queue_redis, "_push_dlq_entry"),
        patch.object(queue_redis.redis_lib.Redis, "from_url", return_value=MagicMock()),
    ):
        await queue_redis._async_execute_job(
            "doc-redis",
            "v2.pdf",
            "application/pdf",
            str(Path("/nonexistent/spill/doc-redis")),
            0,
            "tenant-1",
            True,
            {"rev": 2},
        )

    update_status.assert_not_awaited()
    set_detail.assert_awaited_once()
    assert set_detail.await_args.args[:2] == ("doc-redis", REPLACEMENT_FAILED)
    assert set_detail.await_args.kwargs["error"]["code"] == "job_payload_missing"


@pytest.mark.asyncio
async def test_redis_job_forwards_replace_and_metadata_to_pipeline(tmp_path):
    from contextlib import asynccontextmanager

    import services.queue_redis as queue_redis

    @asynccontextmanager
    async def fake_worker_db_context():
        yield

    payload = tmp_path / "doc-redis-ok"
    payload.write_bytes(b"bytes")

    mock_pipeline_cls = MagicMock()
    pipeline = mock_pipeline_cls.return_value
    pipeline.process_document_background = AsyncMock()

    with (
        patch("db.worker_db_context", fake_worker_db_context),
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch.object(queue_redis.redis_lib.Redis, "from_url", return_value=MagicMock()),
    ):
        await queue_redis._async_execute_job(
            "doc-redis-ok",
            "v2.pdf",
            "application/pdf",
            str(payload),
            0,
            "tenant-1",
            True,
            {"rev": 2},
        )

    kwargs = pipeline.process_document_background.await_args.kwargs
    assert kwargs["replace"] is True
    assert kwargs["metadata"] == {"rev": 2}
    assert not payload.exists()
