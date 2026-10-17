"""Persistence for documents.artifact_uri (migration 016)."""

from __future__ import annotations

import sys
import uuid

import pytest

pytest.importorskip("pgvector")

from db.sqlalchemy_service import SQLAlchemyService
from services.api_key_service import create_tenant, reset_session_factory

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Psycopg async mode not supported with ProactorEventLoop on Windows",
)


@pytest.fixture(autouse=True)
def _reset_api_key_session_factory():
    reset_session_factory()
    yield
    reset_session_factory()


@pytest.fixture
async def tenant_id() -> str:
    tenant = f"artifact-{uuid.uuid4().hex[:8]}"
    await create_tenant("Artifact URI test", tenant_id=tenant)
    return tenant


@pytest.fixture
def svc() -> SQLAlchemyService:
    return SQLAlchemyService()


@pytest.mark.asyncio
async def test_set_and_clear_document_artifact_uri(svc, tenant_id):
    doc_id = await svc.create_document("payload.txt", tenant_id=tenant_id)
    uri = f"s3://chatvector-artifacts/chatvector/artifacts/{doc_id}"

    await svc.set_document_artifact_uri(doc_id, tenant_id, uri)
    document = await svc.get_document(doc_id, tenant_id)
    assert document["artifact_uri"] == uri

    await svc.set_document_artifact_uri(doc_id, tenant_id, None)
    document = await svc.get_document(doc_id, tenant_id)
    assert document["artifact_uri"] is None
