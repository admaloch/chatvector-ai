"""Tests for document metadata: upload validation, storage, and list/status read paths."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from io import BytesIO
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import AuthContext, require_auth
from core.config import config
from middleware.rate_limit import limiter
from routes.documents import router as documents_router
from routes.upload import router as upload_router
from services.document_metadata import parse_document_metadata
from services.ingestion_pipeline import UploadPipelineError

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)


# ── parse_document_metadata ──────────────────────────────────────────────────


def test_parse_none_returns_empty_dict():
    assert parse_document_metadata(None) == {}


def test_parse_empty_object_returns_empty_dict():
    assert parse_document_metadata("{}") == {}


def test_parse_accepts_string_number_and_boolean_values():
    raw = json.dumps({"dept": "legal", "year": 2026, "score": 1.5, "final": True, "draft": False})
    assert parse_document_metadata(raw) == {
        "dept": "legal",
        "year": 2026,
        "score": 1.5,
        "final": True,
        "draft": False,
    }


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "",
        "{",
        '{"a": 1,}',
        "NaN",
        '{"a": NaN}',
        '{"a": Infinity}',
        '{"a": -Infinity}',
        '{"a": 1e999}',
    ],
)
def test_parse_rejects_malformed_json(raw):
    with pytest.raises(UploadPipelineError) as exc:
        parse_document_metadata(raw)
    assert exc.value.status_code == 422
    assert exc.value.code == "invalid_metadata"


@pytest.mark.parametrize("raw", ["[]", '[{"a": 1}]', '["a"]', '"text"', "42", "true", "null"])
def test_parse_rejects_non_object_metadata(raw):
    with pytest.raises(UploadPipelineError) as exc:
        parse_document_metadata(raw)
    assert exc.value.status_code == 422
    assert exc.value.code == "invalid_metadata"


@pytest.mark.parametrize(
    "raw",
    [
        '{"nested": {"a": 1}}',
        '{"tags": ["a", "b"]}',
        '{"empty_list": []}',
        '{"empty_obj": {}}',
        '{"nothing": null}',
    ],
)
def test_parse_rejects_non_scalar_values(raw):
    with pytest.raises(UploadPipelineError) as exc:
        parse_document_metadata(raw)
    assert exc.value.status_code == 422
    assert exc.value.code == "invalid_metadata"


def test_parse_rejects_empty_key():
    with pytest.raises(UploadPipelineError) as exc:
        parse_document_metadata('{"": "x"}')
    assert exc.value.status_code == 422


def test_parse_rejects_nul_characters_postgres_cannot_store():
    for raw in ('{"a\\u0000b": "x"}', '{"a": "x\\u0000y"}'):
        with pytest.raises(UploadPipelineError) as exc:
            parse_document_metadata(raw)
        assert exc.value.status_code == 422


def test_parse_rejects_deeply_nested_input_without_crashing(monkeypatch):
    monkeypatch.setattr(config, "DOCUMENT_METADATA_MAX_BYTES", 100_000)
    with pytest.raises(UploadPipelineError) as exc:
        parse_document_metadata("[" * 50_000)
    assert exc.value.status_code == 422


def test_parse_enforces_max_keys(monkeypatch):
    monkeypatch.setattr(config, "DOCUMENT_METADATA_MAX_KEYS", 2)
    assert parse_document_metadata('{"a": 1, "b": 2}') == {"a": 1, "b": 2}
    with pytest.raises(UploadPipelineError) as exc:
        parse_document_metadata('{"a": 1, "b": 2, "c": 3}')
    assert exc.value.status_code == 422
    assert exc.value.code == "metadata_too_many_keys"


def test_parse_enforces_max_bytes_on_multibyte_input(monkeypatch):
    monkeypatch.setattr(config, "DOCUMENT_METADATA_MAX_BYTES", 20)
    # 10 characters but 20+ UTF-8 bytes once the JSON wrapper is included.
    with pytest.raises(UploadPipelineError) as exc:
        parse_document_metadata(json.dumps({"k": "é" * 10}, ensure_ascii=False))
    assert exc.value.status_code == 422
    assert exc.value.code == "metadata_too_large"


# ── POST /upload (HTTP, mocked persistence) ──────────────────────────────────


def _pdf_files():
    return {"file": ("t.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")}


@pytest.fixture
def upload_client():
    limiter.reset()
    app = FastAPI()
    app.state.limiter = limiter
    app.dependency_overrides[require_auth] = lambda: AuthContext(tenant_id="tenant-123")
    app.include_router(upload_router)
    with TestClient(app) as client:
        yield client
    limiter.reset()


def _patched_upload(create_document: AsyncMock):
    return (
        patch("routes.upload.ingestion_pipeline.validate_file", return_value=None),
        patch("routes.upload.db.create_document", new=create_document),
        patch("routes.upload.db.update_document_status", new=AsyncMock()),
        patch("routes.upload.ingestion_queue.enqueue", new=AsyncMock(return_value=1)),
    )


def test_upload_stores_validated_metadata(upload_client):
    create_document = AsyncMock(return_value="doc-1")
    metadata = {"dept": "legal", "year": 2026, "final": True}
    p1, p2, p3, p4 = _patched_upload(create_document)
    with p1, p2, p3, p4:
        response = upload_client.post(
            "/upload", files=_pdf_files(), data={"metadata": json.dumps(metadata)}
        )

    assert response.status_code == 202
    create_document.assert_awaited_once()
    assert create_document.await_args.kwargs["metadata"] == metadata
    assert create_document.await_args.kwargs["tenant_id"] == "tenant-123"


def test_upload_without_metadata_stores_empty_object(upload_client):
    create_document = AsyncMock(return_value="doc-1")
    p1, p2, p3, p4 = _patched_upload(create_document)
    with p1, p2, p3, p4:
        response = upload_client.post("/upload", files=_pdf_files())

    assert response.status_code == 202
    assert create_document.await_args.kwargs["metadata"] == {}


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[]",
        '[{"a": 1}]',
        '{"nested": {"a": 1}}',
        '{"tags": ["a"]}',
        '{"nothing": null}',
    ],
)
def test_upload_rejects_invalid_metadata_with_422(upload_client, raw):
    create_document = AsyncMock(return_value="doc-1")
    p1, p2, p3, p4 = _patched_upload(create_document)
    with p1, p2, p3, p4:
        response = upload_client.post("/upload", files=_pdf_files(), data={"metadata": raw})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_metadata"
    assert detail["stage"] == "validation"
    create_document.assert_not_awaited()


# ── GET /documents and /documents/{id}/status (HTTP, mocked db) ──────────────


@pytest.fixture
def documents_client():
    limiter.reset()
    app = FastAPI()
    app.state.limiter = limiter
    app.dependency_overrides[require_auth] = lambda: AuthContext(tenant_id="dev")
    app.include_router(documents_router)
    with TestClient(app) as client:
        yield client
    limiter.reset()


def test_list_documents_includes_metadata(documents_client):
    summaries = [
        {
            "document_id": "doc-1",
            "file_name": "a.txt",
            "status": "completed",
            "metadata": {"dept": "legal"},
            "created_at": None,
            "updated_at": None,
        }
    ]
    with patch(
        "routes.documents.db.list_tenant_document_summaries",
        new=AsyncMock(return_value=summaries),
    ):
        response = documents_client.get("/documents")

    assert response.status_code == 200
    assert response.json()["documents"][0]["metadata"] == {"dept": "legal"}


def test_status_includes_metadata_round_trip(documents_client):
    doc_id = str(uuid4())
    payload = {
        "document_id": doc_id,
        "status": "completed",
        "chunks": {"total": 1, "processed": 1},
        "error": None,
        "metadata": {"dept": "legal", "year": 2026, "final": True},
    }
    with patch("routes.documents.db.get_document_status", new=AsyncMock(return_value=payload)):
        response = documents_client.get(f"/documents/{doc_id}/status")

    assert response.status_code == 200
    assert response.json()["metadata"] == {"dept": "legal", "year": 2026, "final": True}


def test_status_defaults_metadata_to_empty_object_when_absent(documents_client):
    doc_id = str(uuid4())
    payload = {"document_id": doc_id, "status": "queued", "chunks": None, "error": None}
    with (
        patch("routes.documents.db.get_document_status", new=AsyncMock(return_value=payload)),
        patch("routes.documents.ingestion_queue.queue_position", return_value=None),
    ):
        response = documents_client.get(f"/documents/{doc_id}/status")

    assert response.status_code == 200
    assert response.json()["metadata"] == {}


# ── SQLAlchemyService (real DB) ──────────────────────────────────────────────


async def _metadata_column_exists() -> bool:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(DB_URL, echo=False)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='documents' "
                    "AND column_name='metadata'"
                )
            )
            return result.scalar_one() == 1
    except Exception:
        return False
    finally:
        await engine.dispose()


_requires_metadata_column = pytest.mark.skipif(
    not asyncio.get_event_loop().run_until_complete(_metadata_column_exists())
    if sys.platform != "win32"
    else False,
    reason="documents.metadata column not available (apply 013_document_metadata.sql)",
)


@_requires_metadata_column
@pytest.mark.asyncio
async def test_metadata_round_trips_on_list_and_status_and_defaults_to_empty():
    from db.sqlalchemy_service import SQLAlchemyService
    from services.api_key_service import ensure_tenant_exists

    pytest.importorskip("pgvector")
    svc = SQLAlchemyService()
    tenant_id = f"meta-{uuid4().hex[:8]}"
    await ensure_tenant_exists(tenant_id, "Metadata")
    metadata = {"dept": "legal", "year": 2026, "score": 1.5, "final": True}

    doc_ids: list[str] = []
    try:
        with_meta = await svc.create_document("with.pdf", tenant_id=tenant_id, metadata=metadata)
        without_meta = await svc.create_document("without.pdf", tenant_id=tenant_id)
        doc_ids.extend([with_meta, without_meta])

        status_with = await svc.get_document_status(with_meta, tenant_id=tenant_id)
        status_without = await svc.get_document_status(without_meta, tenant_id=tenant_id)
        assert status_with["metadata"] == metadata
        assert status_without["metadata"] == {}

        by_id = {
            doc["document_id"]: doc
            for doc in await svc.list_tenant_document_summaries(tenant_id)
        }
        assert by_id[with_meta]["metadata"] == metadata
        assert by_id[without_meta]["metadata"] == {}
    finally:
        for doc_id in doc_ids:
            try:
                await svc.delete_document(doc_id, tenant_id=tenant_id)
            except Exception:
                pass
        await svc.engine.dispose()


@_requires_metadata_column
@pytest.mark.asyncio
async def test_metadata_not_visible_across_tenants():
    from db.sqlalchemy_service import SQLAlchemyService
    from services.api_key_service import ensure_tenant_exists

    pytest.importorskip("pgvector")
    svc = SQLAlchemyService()
    tenant_a = f"meta-a-{uuid4().hex[:8]}"
    tenant_b = f"meta-b-{uuid4().hex[:8]}"
    await ensure_tenant_exists(tenant_a, "Meta A")
    await ensure_tenant_exists(tenant_b, "Meta B")

    doc_a: str | None = None
    try:
        doc_a = await svc.create_document(
            "secret.pdf", tenant_id=tenant_a, metadata={"classification": "secret"}
        )

        assert await svc.get_document_status(doc_a, tenant_id=tenant_b) is None
        assert await svc.list_tenant_document_summaries(tenant_b) == []
        listed_a = await svc.list_tenant_document_summaries(tenant_a)
        assert [d["metadata"] for d in listed_a] == [{"classification": "secret"}]
    finally:
        if doc_a:
            try:
                await svc.delete_document(doc_a, tenant_id=tenant_a)
            except Exception:
                pass
        await svc.engine.dispose()
