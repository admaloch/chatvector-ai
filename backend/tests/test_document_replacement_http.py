"""POST /upload replace_document_id over HTTP against a real database.

The ingestion queue is stubbed, so a replacement stays "in flight" until the
test resolves it; retrieval is asserted through ``db.find_similar_chunks``.
"""

from __future__ import annotations

import sys
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

pytest.importorskip("pgvector")

import db
from core.auth import AuthContext, require_auth
from core.config import get_embedding_dim
from db.base import ChunkRecord
from middleware.rate_limit import limiter
from routes.documents import router as documents_router
from routes.upload import router as upload_router
from services.api_key_service import create_tenant, reset_session_factory
from services.queue_service import QueueFull

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Psycopg async mode not supported with ProactorEventLoop on Windows",
)

OLD_PHRASE = "OLD-EDITION penguins migrate in spring"


def _vector() -> list[float]:
    vector = [0.0] * get_embedding_dim()
    vector[0] = 1.0
    return vector


@pytest.fixture(autouse=True)
def _isolated_state():
    reset_session_factory()
    limiter.reset()
    yield
    limiter.reset()
    reset_session_factory()


@pytest.fixture
async def tenant_id() -> str:
    tenant = f"replace-http-{uuid.uuid4().hex[:8]}"
    await create_tenant("Replacement HTTP test", tenant_id=tenant)
    return tenant


def _client(tenant_id: str) -> httpx.AsyncClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.dependency_overrides[require_auth] = lambda: AuthContext(tenant_id=tenant_id)
    app.include_router(upload_router)
    app.include_router(documents_router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def _txt(name: str = "v2.txt", body: bytes = b"replacement body"):
    return {"file": (name, body, "text/plain")}


async def _completed_document(tenant_id: str) -> str:
    doc_id = await db.create_document("v1.txt", tenant_id=tenant_id)
    await db.store_chunks_with_embeddings(
        doc_id,
        [
            ChunkRecord(
                chunk_text=OLD_PHRASE,
                embedding=_vector(),
                chunk_index=0,
                character_offset_start=0,
                character_offset_end=len(OLD_PHRASE),
            )
        ],
        tenant_id=tenant_id,
    )
    await db.update_document_status(
        doc_id, "completed", tenant_id=tenant_id, chunks={"total": 1, "processed": 1}
    )
    return doc_id


async def _old_content_still_retrievable(doc_id: str, tenant_id: str) -> bool:
    matches = await db.find_similar_chunks(
        doc_id, _vector(), 5, tenant_id=tenant_id
    )
    return [m.chunk_text for m in matches] == [OLD_PHRASE]


@pytest.mark.asyncio
async def test_replace_accepts_then_rejects_second_replace_with_409(tenant_id):
    doc_id = await _completed_document(tenant_id)

    with patch(
        "routes.upload.ingestion_queue.enqueue", new=AsyncMock(return_value=1)
    ) as enqueue:
        async with _client(tenant_id) as client:
            first = await client.post(
                "/upload", files=_txt(), data={"replace_document_id": doc_id}
            )
            second = await client.post(
                "/upload", files=_txt(), data={"replace_document_id": doc_id}
            )
            status = await client.get(f"/documents/{doc_id}/status")

    assert first.status_code == 202
    body = first.json()
    assert body["document_id"] == doc_id
    assert body["status"] == "completed"
    assert body["status_detail"] == "replacement_in_progress"
    assert body["status_endpoint"] == f"/documents/{doc_id}/status"

    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "replacement_in_progress"
    assert enqueue.await_count == 1  # the rejected call never enqueued
    job = enqueue.await_args.args[0]
    assert job.replace is True and job.doc_id == doc_id

    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["status_detail"] == "replacement_in_progress"
    # Prior content keeps serving retrieval while the replacement is queued.
    assert await _old_content_still_retrievable(doc_id, tenant_id)


@pytest.mark.asyncio
async def test_replace_queue_full_keeps_document_completed_and_retrievable(tenant_id):
    doc_id = await _completed_document(tenant_id)

    with patch(
        "routes.upload.ingestion_queue.enqueue",
        new=AsyncMock(side_effect=QueueFull("full")),
    ):
        async with _client(tenant_id) as client:
            response = await client.post(
                "/upload", files=_txt(), data={"replace_document_id": doc_id}
            )
            status = await client.get(f"/documents/{doc_id}/status")

    assert response.status_code == 503
    payload = status.json()
    assert payload["status"] == "completed"
    assert payload["status_detail"] == "replacement_failed"
    assert payload["error"]["stage"] == "queued"
    assert await _old_content_still_retrievable(doc_id, tenant_id)


@pytest.mark.asyncio
async def test_replace_is_rejected_for_unknown_foreign_and_malformed_ids(tenant_id):
    doc_id = await _completed_document(tenant_id)
    other_tenant = f"replace-http-{uuid.uuid4().hex[:8]}"
    await create_tenant("Other tenant", tenant_id=other_tenant)

    with patch("routes.upload.ingestion_queue.enqueue", new=AsyncMock()) as enqueue:
        async with _client(tenant_id) as client:
            unknown = await client.post(
                "/upload", files=_txt(), data={"replace_document_id": str(uuid.uuid4())}
            )
            malformed = await client.post(
                "/upload", files=_txt(), data={"replace_document_id": "nope"}
            )
        async with _client(other_tenant) as other_client:
            foreign = await other_client.post(
                "/upload", files=_txt(), data={"replace_document_id": doc_id}
            )

    assert unknown.status_code == 404
    assert foreign.status_code == 404
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "invalid_replace_document_id"
    enqueue.assert_not_awaited()
    # Rejected attempts leave the document untouched and replaceable.
    status = await db.get_document_status(doc_id, tenant_id)
    assert status["status"] == "completed"
    assert status["status_detail"] is None


@pytest.mark.asyncio
async def test_replace_of_document_still_ingesting_returns_409_without_changes(tenant_id):
    doc_id = await db.create_document("busy.txt", tenant_id=tenant_id)
    await db.update_document_status(doc_id, "embedding", tenant_id=tenant_id)

    with patch("routes.upload.ingestion_queue.enqueue", new=AsyncMock()) as enqueue:
        async with _client(tenant_id) as client:
            response = await client.post(
                "/upload", files=_txt(), data={"replace_document_id": doc_id}
            )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "document_processing"
    enqueue.assert_not_awaited()
    assert (await db.get_document_status(doc_id, tenant_id))["status"] == "embedding"


@pytest.mark.asyncio
async def test_upload_without_replace_creates_a_new_document_id(tenant_id):
    doc_id = await _completed_document(tenant_id)

    with patch(
        "routes.upload.ingestion_queue.enqueue", new=AsyncMock(return_value=1)
    ) as enqueue:
        async with _client(tenant_id) as client:
            response = await client.post("/upload", files=_txt())

    assert response.status_code == 202
    body = response.json()
    assert body["document_id"] != doc_id
    assert body["status"] == "queued"
    assert "status_detail" not in body
    assert enqueue.await_args.args[0].replace is False
    # The existing document is untouched.
    assert (await db.get_document_status(doc_id, tenant_id))["status_detail"] is None
    assert await _old_content_still_retrievable(doc_id, tenant_id)


@pytest.mark.asyncio
async def test_delete_is_blocked_while_replacement_in_progress_over_http(tenant_id):
    doc_id = await _completed_document(tenant_id)
    assert await db.begin_document_replacement(doc_id, tenant_id) == "started"

    async with _client(tenant_id) as client:
        blocked = await client.delete(f"/documents/{doc_id}")

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "document_replacing"
    assert await _old_content_still_retrievable(doc_id, tenant_id)
