"""Idempotency-Key support for POST /upload and POST /sessions."""

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from core.auth import AuthContext, require_auth
from core.session import Session
from datetime import datetime, timezone
from request_utils import make_test_request
from middleware.rate_limit import limiter
from routes.sessions import router as sessions_router
from routes.upload import router as upload_router
from services.idempotency import (
    ROUTE_SESSION_CREATE,
    ROUTE_UPLOAD,
    _redis_key,
    upload_request_fingerprint,
)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        async with self.lock:
            if nx and key in self.data:
                return None
            self.data[key] = value
            return True

    async def delete(self, key: str) -> None:
        async with self.lock:
            self.data.pop(key, None)


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def upload_client():
    limiter.reset()
    app = FastAPI()
    app.state.limiter = limiter
    app.dependency_overrides[require_auth] = lambda: AuthContext(tenant_id="tenant-a")
    app.include_router(upload_router)
    with TestClient(app) as client:
        yield client
    limiter.reset()


@pytest.fixture
def sessions_client():
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: AuthContext(tenant_id="tenant-a")
    app.include_router(sessions_router)
    with TestClient(app) as client:
        yield client


def _pdf_files(content: bytes = b"%PDF-1.4 same"):
    return {"file": ("doc.pdf", BytesIO(content), "application/pdf")}


def _patched_upload(create_document: AsyncMock):
    return (
        patch("routes.upload.ingestion_pipeline.validate_file", return_value=None),
        patch("routes.upload.db.create_document", new=create_document),
        patch("routes.upload.db.update_document_status", new=AsyncMock()),
        patch("routes.upload.ingestion_queue.enqueue", new=AsyncMock(return_value=1)),
    )


def test_upload_replay_same_key_same_file(upload_client, fake_redis):
    create_document = AsyncMock(return_value="doc-replay")
    headers = {"Idempotency-Key": "upload-key-1"}
    p1, p2, p3, p4 = _patched_upload(create_document)
    with patch("services.idempotency.redis_client", fake_redis), p1, p2, p3, p4:
        first = upload_client.post("/upload", files=_pdf_files(), headers=headers)
        second = upload_client.post("/upload", files=_pdf_files(), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["document_id"] == "doc-replay"
    assert second.json() == first.json()
    create_document.assert_awaited_once()


def test_upload_mismatch_different_bytes(upload_client, fake_redis):
    create_document = AsyncMock(return_value="doc-1")
    headers = {"Idempotency-Key": "upload-key-2"}
    p1, p2, p3, p4 = _patched_upload(create_document)
    with patch("services.idempotency.redis_client", fake_redis), p1, p2, p3, p4:
        upload_client.post("/upload", files=_pdf_files(b"%PDF-1.4 v1"), headers=headers)
        mismatch = upload_client.post(
            "/upload", files=_pdf_files(b"%PDF-1.4 v2"), headers=headers
        )

    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "idempotency_key_mismatch"
    create_document.assert_awaited_once()


def test_upload_mismatch_different_replace_document_id(upload_client, fake_redis):
    replace_a = "11111111-1111-4111-8111-111111111111"
    replace_b = "22222222-2222-4222-8222-222222222222"
    headers = {"Idempotency-Key": "replace-key"}
    begin_replace = AsyncMock(return_value="ok")
    with (
        patch("services.idempotency.redis_client", fake_redis),
        patch("routes.upload.ingestion_pipeline.validate_file", return_value=None),
        patch("routes.upload.db.begin_document_replacement", new=begin_replace),
        patch(
            "routes.upload.db.get_document_status",
            new=AsyncMock(return_value={"status": "completed"}),
        ),
        patch("routes.upload.ingestion_queue.enqueue", new=AsyncMock(return_value=1)),
    ):
        upload_client.post(
            "/upload",
            files=_pdf_files(),
            data={"replace_document_id": replace_a},
            headers=headers,
        )
        mismatch = upload_client.post(
            "/upload",
            files=_pdf_files(),
            data={"replace_document_id": replace_b},
            headers=headers,
        )

    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "idempotency_key_mismatch"
    begin_replace.assert_awaited_once()


def test_upload_fingerprint_canonicalizes_metadata():
    shared = dict(
        file_bytes=b"%PDF-1.4 same",
        filename="doc.pdf",
        content_type="application/pdf",
        replace_document_id=None,
    )
    ordered = upload_request_fingerprint(
        **shared, metadata={"b": 1, "a": "legal"}
    )
    reordered = upload_request_fingerprint(
        **shared, metadata={"a": "legal", "b": 1}
    )
    different = upload_request_fingerprint(**shared, metadata={"a": "hr"})
    omitted = upload_request_fingerprint(**shared, metadata=None)
    empty = upload_request_fingerprint(**shared, metadata={})

    assert ordered == reordered
    assert ordered != different
    assert omitted != empty
    assert omitted != ordered


def test_upload_same_metadata_replays_and_different_metadata_mismatches(
    upload_client, fake_redis
):
    create_document = AsyncMock(return_value="doc-meta")
    headers = {"Idempotency-Key": "metadata-key"}
    p1, p2, p3, p4 = _patched_upload(create_document)
    with patch("services.idempotency.redis_client", fake_redis), p1, p2, p3, p4:
        first = upload_client.post(
            "/upload",
            files=_pdf_files(),
            data={"metadata": json.dumps({"b": 1, "a": "legal"})},
            headers=headers,
        )
        replay = upload_client.post(
            "/upload",
            files=_pdf_files(),
            data={"metadata": json.dumps({"a": "legal", "b": 1})},
            headers=headers,
        )
        mismatch = upload_client.post(
            "/upload",
            files=_pdf_files(),
            data={"metadata": json.dumps({"a": "hr"})},
            headers=headers,
        )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json() == first.json()
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "idempotency_key_mismatch"
    create_document.assert_awaited_once()


def test_idempotency_keys_are_tenant_scoped(fake_redis):
    key = "shared-key"
    fp = upload_request_fingerprint(
        file_bytes=b"x",
        filename="f.pdf",
        content_type="application/pdf",
        replace_document_id=None,
    )
    redis_key_a = _redis_key(tenant_id="tenant-a", route=ROUTE_UPLOAD, idempotency_key=key)
    redis_key_b = _redis_key(tenant_id="tenant-b", route=ROUTE_UPLOAD, idempotency_key=key)
    assert redis_key_a != redis_key_b


def test_upload_without_header_behaves_like_phase3(upload_client, fake_redis):
    create_document = AsyncMock(side_effect=["doc-1", "doc-2"])
    p1, p2, p3, p4 = _patched_upload(create_document)
    with patch("services.idempotency.redis_client", fake_redis), p1, p2, p3, p4:
        first = upload_client.post("/upload", files=_pdf_files())
        second = upload_client.post("/upload", files=_pdf_files())

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["document_id"] == "doc-1"
    assert second.json()["document_id"] == "doc-2"
    assert create_document.await_count == 2
    assert fake_redis.data == {}


@pytest.mark.asyncio
async def test_concurrent_upload_same_idempotency_key(fake_redis):
    from routes.upload import upload

    create_calls = 0
    gate = asyncio.Event()

    async def slow_create_document(*_args, **_kwargs):
        nonlocal create_calls
        create_calls += 1
        gate.set()
        await asyncio.sleep(0.15)
        return f"doc-{create_calls}"

    mock_file = AsyncMock(spec=UploadFile)
    mock_file.filename = "doc.pdf"
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=b"%PDF concurrent")

    request = make_test_request(
        "POST",
        "/upload",
        headers={"Idempotency-Key": "concurrent-upload"},
    )

    with (
        patch("services.idempotency.redis_client", fake_redis),
        patch("routes.upload.ingestion_pipeline.validate_file", return_value=None),
        patch("routes.upload.db.create_document", new=slow_create_document),
        patch("routes.upload.db.update_document_status", new=AsyncMock()),
        patch("routes.upload.ingestion_queue.enqueue", new=AsyncMock(return_value=1)),
    ):

        async def run_upload():
            return await upload(
                request,
                mock_file,
                auth=AuthContext(tenant_id="tenant-a"),
            )

        task_a = asyncio.create_task(run_upload())
        await gate.wait()
        task_b = asyncio.create_task(run_upload())
        result_a = await task_a
        result_b = await task_b

    assert create_calls == 1

    def _document_id(result):
        if hasattr(result, "body"):
            import json

            return json.loads(result.body)["document_id"]
        return result["document_id"]

    assert _document_id(result_a) == _document_id(result_b)


def test_session_create_replay(sessions_client, fake_redis):
    session_obj = Session(
        id="sess-1",
        tenant_id="tenant-a",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_active=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metadata={},
        document_ids=[],
    )

    headers = {"Idempotency-Key": "session-key-1"}
    with (
        patch("services.idempotency.redis_client", fake_redis),
        patch(
            "routes.sessions.session_service.create_session",
            new=AsyncMock(return_value=session_obj),
        ) as create_session,
    ):
        first = sessions_client.post("/sessions", json={}, headers=headers)
        second = sessions_client.post("/sessions", json={}, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"] == "sess-1"
    create_session.assert_awaited_once()


def test_upload_same_key_different_tenants_create_two_documents(fake_redis):
    limiter.reset()
    app_a = FastAPI()
    app_a.state.limiter = limiter
    app_a.dependency_overrides[require_auth] = lambda: AuthContext(tenant_id="tenant-a")
    app_a.include_router(upload_router)
    app_b = FastAPI()
    app_b.state.limiter = limiter
    app_b.dependency_overrides[require_auth] = lambda: AuthContext(tenant_id="tenant-b")
    app_b.include_router(upload_router)

    create_a = AsyncMock(return_value="doc-tenant-a")
    create_b = AsyncMock(return_value="doc-tenant-b")
    headers = {"Idempotency-Key": "shared-tenant-key"}
    p1, p2, p3, p4 = _patched_upload(create_a)
    with patch("services.idempotency.redis_client", fake_redis), p1, p2, p3, p4:
        with TestClient(app_a) as client_a:
            resp_a = client_a.post("/upload", files=_pdf_files(), headers=headers)
    p1, p2, p3, p4 = _patched_upload(create_b)
    with patch("services.idempotency.redis_client", fake_redis), p1, p2, p3, p4:
        with TestClient(app_b) as client_b:
            resp_b = client_b.post("/upload", files=_pdf_files(), headers=headers)

    assert resp_a.json()["document_id"] == "doc-tenant-a"
    assert resp_b.json()["document_id"] == "doc-tenant-b"
    limiter.reset()


def test_session_fingerprint_independent_per_tenant(fake_redis):
    assert _redis_key(
        tenant_id="tenant-a",
        route=ROUTE_SESSION_CREATE,
        idempotency_key="k",
    ) != _redis_key(
        tenant_id="tenant-b",
        route=ROUTE_SESSION_CREATE,
        idempotency_key="k",
    )
