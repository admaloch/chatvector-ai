"""Ingestion workers fetch upload bytes from ArtifactStorage."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.artifact_storage import (
    ArtifactMissingError,
    S3ArtifactStorage,
    reset_artifact_storage,
)
from services.ingestion_pipeline import UploadPipelineError
from services.queue_asyncio import AsyncioIngestionQueue
from services.queue_base import QueueJob


def _s3_storage(bucket: str = "cv-artifacts"):
    boto3 = pytest.importorskip("boto3")
    pytest.importorskip("moto")
    from moto import mock_aws

    mock = mock_aws()
    mock.start()
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=bucket)
    storage = S3ArtifactStorage(
        bucket=bucket,
        prefix="chatvector/artifacts/",
        ttl_seconds=3600,
        client=client,
    )
    storage._moto = mock  # type: ignore[attr-defined]
    return storage


@pytest.fixture
def s3_storage():
    storage = _s3_storage()
    try:
        yield storage
    finally:
        storage._moto.stop()  # type: ignore[attr-defined]
        reset_artifact_storage()


@asynccontextmanager
async def _fake_worker_db_context():
    yield


@pytest.mark.asyncio
async def test_worker_completes_from_s3_without_local_disk(s3_storage, tmp_path, monkeypatch):
    """Worker fetches the artifact URI; API host disk is empty / unused."""
    from services.queue_redis import _async_execute_job

    api_disk = tmp_path / "api-spill"
    api_disk.mkdir()
    monkeypatch.setattr("services.queue_redis.config.QUEUE_SPILL_DIR", str(api_disk))

    uri = s3_storage.put("doc-remote", b"hello-from-s3")
    assert list(api_disk.iterdir()) == []

    mock_pipeline_cls = MagicMock()
    pipeline = mock_pipeline_cls.return_value
    pipeline.process_document_background = AsyncMock()

    with (
        patch("db.worker_db_context", _fake_worker_db_context),
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch("services.queue_redis.get_artifact_storage", return_value=s3_storage),
        patch("services.queue_redis.redis_lib.Redis.from_url", return_value=MagicMock()),
    ):
        await _async_execute_job(
            "doc-remote",
            "remote.txt",
            "text/plain",
            uri,
            0,
        )

    assert pipeline.process_document_background.await_count == 1
    assert (
        pipeline.process_document_background.await_args.kwargs["file_bytes"]
        == b"hello-from-s3"
    )
    assert list(api_disk.iterdir()) == []
    with pytest.raises(ArtifactMissingError):
        s3_storage.get(uri)


@pytest.mark.asyncio
async def test_missing_s3_artifact_is_structured_failure(s3_storage):
    from services.queue_redis import _async_execute_job

    update_status = AsyncMock()
    with (
        patch("db.worker_db_context", _fake_worker_db_context),
        patch("db.update_document_status", update_status),
        patch("services.queue_redis.get_artifact_storage", return_value=s3_storage),
        patch("services.queue_redis._push_dlq_entry"),
        patch("services.queue_redis.redis_lib.Redis.from_url", return_value=MagicMock()),
    ):
        await _async_execute_job(
            "doc-missing",
            "gone.txt",
            "text/plain",
            "s3://cv-artifacts/chatvector/artifacts/doc-missing",
            0,
            tenant_id="tenant-s3",
        )

    update_status.assert_awaited_once()
    error = update_status.await_args.kwargs["error"]
    assert error["code"] == "job_payload_missing"
    assert error["stage"] == "queued"
    assert "re-upload" in error["message"].lower()


@pytest.mark.asyncio
async def test_terminal_failure_deletes_s3_artifact(s3_storage):
    from services.queue_redis import _async_execute_job

    uri = s3_storage.put("doc-fail", b"will-fail")
    assert s3_storage.ttl_marked(uri) is True

    mock_pipeline_cls = MagicMock()
    pipeline = mock_pipeline_cls.return_value
    pipeline.process_document_background = AsyncMock(
        side_effect=UploadPipelineError(
            status_code=422,
            code="unprocessable_entity",
            stage="extract",
            message="No extractable text",
        )
    )

    with (
        patch("db.worker_db_context", _fake_worker_db_context),
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch("services.queue_redis.get_artifact_storage", return_value=s3_storage),
        patch("services.queue_redis._push_dlq_entry"),
        patch("services.queue_redis.redis_lib.Redis.from_url", return_value=MagicMock()),
    ):
        await _async_execute_job(
            "doc-fail",
            "fail.txt",
            "text/plain",
            uri,
            0,
        )

    with pytest.raises(ArtifactMissingError):
        s3_storage.get(uri)


@pytest.mark.asyncio
async def test_memory_queue_does_not_require_artifact_storage(monkeypatch):
    """Single-host memory queue keeps file bytes in-process."""
    monkeypatch.setattr("services.queue_asyncio.config.QUEUE_JOB_MAX_RETRIES", 0)
    service = AsyncioIngestionQueue()
    service._rate_limiter.acquire = AsyncMock()

    mock_pipeline_cls = MagicMock()
    pipeline = mock_pipeline_cls.return_value
    pipeline.process_document_background = AsyncMock()
    put = MagicMock(side_effect=AssertionError("memory queue must not spill"))

    with (
        patch("services.ingestion_pipeline.IngestionPipeline", mock_pipeline_cls),
        patch("services.artifact_storage.ArtifactStorage.put", put),
        patch("services.queue_asyncio.db.update_document_status", new=AsyncMock()),
    ):
        await service.start()
        try:
            await service.enqueue(
                QueueJob(
                    doc_id="doc-memory",
                    file_name="local.txt",
                    content_type="text/plain",
                    file_bytes=b"in-memory-bytes",
                )
            )
            await service._queue.join()
        finally:
            await service.stop()

    put.assert_not_called()
    assert pipeline.process_document_background.await_args.kwargs["file_bytes"] == (
        b"in-memory-bytes"
    )


@pytest.mark.asyncio
async def test_redis_enqueue_records_artifact_uri(s3_storage):
    from services.queue_redis import RedisIngestionQueue

    persist = AsyncMock()
    queue = RedisIngestionQueue.__new__(RedisIngestionQueue)

    def _put_only(job):
        uri = s3_storage.put(job.doc_id, job.file_bytes)
        return 1, uri

    with patch("db.set_document_artifact_uri", persist):
        queue._sync_enqueue = _put_only  # type: ignore[method-assign]
        position = await RedisIngestionQueue.enqueue(
            queue,
            QueueJob(
                doc_id="doc-persist",
                file_name="p.txt",
                content_type="text/plain",
                file_bytes=b"persist-me",
                tenant_id="tenant-s3",
            ),
        )

    assert position == 1
    persist.assert_awaited_once()
    assert persist.await_args.args[0] == "doc-persist"
    assert persist.await_args.args[1] == "tenant-s3"
    assert persist.await_args.args[2].startswith("s3://cv-artifacts/")
    assert s3_storage.ttl_marked(persist.await_args.args[2]) is True
