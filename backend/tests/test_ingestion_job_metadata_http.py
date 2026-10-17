"""HTTP/SSE integration tests for ingestion job metadata on document status."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

pytest.importorskip("pgvector")

import db
from core.auth import AuthContext, require_auth
from core.config import get_embedding_dim
from db.base import ChunkRecord
from langchain_core.documents import Document as LangChainDocument
from middleware.rate_limit import limiter
from routes.documents import router as documents_router
from services.api_key_service import create_tenant, reset_session_factory
from services.ingestion_job_metadata import patch_enqueued, patch_terminal_success
from services.ingestion_pipeline import IngestionPipeline
from services.providers.base import ProviderAuthError

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Psycopg async mode not supported with ProactorEventLoop on Windows",
)


@pytest.fixture(autouse=True)
def _isolated_state():
    reset_session_factory()
    limiter.reset()
    yield
    limiter.reset()
    reset_session_factory()


@pytest.fixture
async def tenant_id() -> str:
    tenant = f"job-meta-{uuid.uuid4().hex[:8]}"
    await create_tenant("Job metadata HTTP test", tenant_id=tenant)
    return tenant


def _client(tenant_id: str) -> httpx.AsyncClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.dependency_overrides[require_auth] = lambda: AuthContext(tenant_id=tenant_id)
    app.include_router(documents_router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.mark.asyncio
async def test_completed_status_includes_enqueued_at_and_total_duration(tenant_id: str):
    enqueued = datetime.now(timezone.utc) - timedelta(seconds=2)
    doc_id = await db.create_document("done.txt", tenant_id=tenant_id)
    await db.update_document_status(
        doc_id,
        "queued",
        tenant_id=tenant_id,
        job_metadata_patch=patch_enqueued(enqueued_at=enqueued, attempt=1),
    )

    dim = get_embedding_dim()
    pipeline = IngestionPipeline()
    with patch(
        "services.ingestion_pipeline.extract_text_with_metadata",
        new=AsyncMock(return_value=("hello world", [])),
    ), patch.object(
        pipeline,
        "_chunk_document_text",
        return_value=[LangChainDocument(page_content="hello world", metadata={})],
    ), patch(
        "services.ingestion_pipeline.get_embeddings",
        new=AsyncMock(return_value=[[0.1] * dim]),
    ), patch(
        "services.ingestion_pipeline.db.store_chunks_with_embeddings",
        new=AsyncMock(return_value=["chunk-1"]),
    ):
        await pipeline.process_document_background(
            doc_id=doc_id,
            file_name="done.txt",
            content_type="text/plain",
            file_bytes=b"hello world",
            tenant_id=tenant_id,
            attempt=0,
        )

    async with _client(tenant_id) as client:
        response = await client.get(f"/documents/{doc_id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert "job" in body
    assert body["job"]["enqueued_at"] is not None
    assert body["job"]["durations_ms"]["total"] >= 0


@pytest.mark.asyncio
async def test_failed_status_includes_failure_class(tenant_id: str):
    doc_id = await db.create_document("fail.txt", tenant_id=tenant_id)
    await db.merge_document_job_metadata(
        doc_id,
        tenant_id,
        patch_enqueued(attempt=1),
    )

    pipeline = IngestionPipeline()
    with patch(
        "services.ingestion_pipeline.extract_text_with_metadata",
        new=AsyncMock(return_value=("hello", [])),
    ), patch.object(
        pipeline,
        "_chunk_document_text",
        return_value=[LangChainDocument(page_content="hello", metadata={})],
    ), patch(
        "services.ingestion_pipeline.get_embeddings",
        new=AsyncMock(side_effect=ProviderAuthError("401 invalid")),
    ):
        with pytest.raises(Exception):
            await pipeline.process_document_background(
                doc_id=doc_id,
                file_name="fail.txt",
                content_type="text/plain",
                file_bytes=b"hello",
                tenant_id=tenant_id,
            )

    async with _client(tenant_id) as client:
        response = await client.get(f"/documents/{doc_id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["job"]["failure_class"] == "embedding_invalid_api_key"


@pytest.mark.asyncio
async def test_sse_terminal_event_matches_http_job_fields(tenant_id: str):
    doc_id = await db.create_document("stream.txt", tenant_id=tenant_id)
    enqueued = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.update_document_status(
        doc_id,
        "completed",
        tenant_id=tenant_id,
        chunks={"total": 1, "processed": 1},
        job_metadata_patch={
            **patch_enqueued(enqueued_at=enqueued, attempt=1),
            **patch_terminal_success(),
        },
    )
    await db.store_chunks_with_embeddings(
        doc_id,
        [
            ChunkRecord(
                chunk_text="stream",
                embedding=[0.1] * get_embedding_dim(),
                chunk_index=0,
                character_offset_start=0,
                character_offset_end=6,
            )
        ],
        tenant_id=tenant_id,
    )

    async with _client(tenant_id) as client:
        with patch("routes.documents.config") as mock_config:
            mock_config.ENABLE_STREAMING = True
            async with client.stream("GET", f"/documents/{doc_id}/status/stream") as response:
                assert response.status_code == 200
                chunks = []
                async for chunk in response.aiter_text():
                    chunks.append(chunk)
        http_body = (await client.get(f"/documents/{doc_id}/status")).json()

    assert len(chunks) == 1
    data_line = chunks[0].split("data: ", 1)[1].strip()
    sse_body = json.loads(data_line)
    assert sse_body["job"] == http_body["job"]


@pytest.mark.asyncio
async def test_legacy_document_without_job_metadata_omits_job(tenant_id: str):
    doc_id = await db.create_document("legacy.txt", tenant_id=tenant_id)
    await db.update_document_status(
        doc_id,
        "completed",
        tenant_id=tenant_id,
        chunks={"total": 0, "processed": 0},
    )

    async with _client(tenant_id) as client:
        response = await client.get(f"/documents/{doc_id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert "job" not in body
