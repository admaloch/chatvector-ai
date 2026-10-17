"""Replacement cutover through the real pipeline and the chat retrieval path.

A slow, stubbed embedding stage holds a replacement "in flight" while the
document is queried through the same session-scoped retrieval used by /chat.
Assertions are made on retrieved context text, not on row counts.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pgvector")

import db
from core.config import get_embedding_dim
from services import chat_service
from services.api_key_service import create_tenant, reset_session_factory
from services.chat_service import _resolve_retrieval_doc_ids, retrieve_for_documents
from services.ingestion_pipeline import IngestionPipeline, UploadPipelineError
from services.retrieval_config import resolve_retrieval_config
from services.session_service import create_session, register_session_document
from services.tenant_registry import clear_tenant_registry

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Psycopg async mode not supported with ProactorEventLoop on Windows",
)

OLD_TEXT = "OLD-EDITION penguins migrate across the ice in spring."
NEW_TEXT = "NEW-EDITION albatrosses glide over the open ocean."


def _vector(position: int) -> list[float]:
    vector = [0.0] * get_embedding_dim()
    vector[position] = 1.0
    return vector


def _vector_mix() -> list[float]:
    vector = [0.0] * get_embedding_dim()
    vector[0] = 1.0
    vector[1] = 1.0
    return vector


def _embedding_for(text: str) -> list[float]:
    return _vector(1 if "NEW-EDITION" in text else 0)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    reset_session_factory()
    clear_tenant_registry()
    monkeypatch.setattr(chat_service.config, "QUERY_TRANSFORMATION_ENABLED", False)
    yield
    clear_tenant_registry()
    reset_session_factory()


@pytest.fixture
async def tenant_id() -> str:
    tenant = f"replace-pipe-{uuid.uuid4().hex[:8]}"
    await create_tenant("Replacement pipeline test", tenant_id=tenant)
    return tenant


@pytest.fixture
def embedding_gate():
    """Stubbed embedding stage that can be held open to simulate a slow replacement."""

    class Gate:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.hold = False
            self.fail_with: Exception | None = None

        async def get_embeddings(self, texts, **_kwargs):
            self.entered.set()
            if self.hold:
                await self.release.wait()
            if self.fail_with is not None:
                raise self.fail_with
            return [_embedding_for(text) for text in texts]

    gate = Gate()
    with patch("services.ingestion_pipeline.get_embeddings", new=gate.get_embeddings):
        yield gate


async def _ingest_original(tenant_id: str, gate) -> str:
    """Create a completed document the normal way (non-replacement path)."""
    doc_id = await db.create_document("v1.txt", tenant_id=tenant_id, metadata={"rev": 1})
    with patch(
        "services.ingestion_pipeline.extract_text_with_metadata",
        new=AsyncMock(return_value=(OLD_TEXT, [])),
    ):
        await IngestionPipeline().process_document_background(
            doc_id=doc_id,
            file_name="v1.txt",
            content_type="text/plain",
            file_bytes=b"ignored",
            tenant_id=tenant_id,
        )
    return doc_id


async def _replace(pipeline_text: str, doc_id: str, tenant_id: str, **overrides) -> None:
    kwargs = dict(
        doc_id=doc_id,
        file_name="v2.txt",
        content_type="text/plain",
        file_bytes=b"ignored",
        tenant_id=tenant_id,
        replace=True,
    )
    kwargs.update(overrides)
    with patch(
        "services.ingestion_pipeline.extract_text_with_metadata",
        new=AsyncMock(return_value=(pipeline_text, [])),
    ):
        await IngestionPipeline().process_document_background(**kwargs)


async def _chat_context(doc_id: str, session_id: str, tenant_id: str) -> str:
    """Context text /chat would build for the session-bound document."""
    doc_ids = await _resolve_retrieval_doc_ids(
        scope="session",
        requested_doc_ids=[doc_id],
        session_id=session_id,
        tenant_id=tenant_id,
    )
    assert doc_ids == [doc_id]
    result = await retrieve_for_documents(
        "what does the document say?",
        doc_ids,
        tenant_id,
        resolve_retrieval_config({"match_count": 10, "scope": "session"}),
        session_id=session_id,
        # One query equally close to both editions so any chunk set would surface.
        query_embeddings=[_vector_mix()],
    )
    return result.context


async def _bound_session(doc_id: str, tenant_id: str) -> str:
    session = await create_session(tenant_id=tenant_id)
    await register_session_document(session.id, doc_id, tenant_id)
    return session.id


@pytest.mark.asyncio
async def test_chat_returns_prior_content_while_replacement_is_in_progress(
    tenant_id, embedding_gate
):
    doc_id = await _ingest_original(tenant_id, embedding_gate)
    session_id = await _bound_session(doc_id, tenant_id)
    assert OLD_TEXT in await _chat_context(doc_id, session_id, tenant_id)

    assert await db.begin_document_replacement(doc_id, tenant_id) == "started"
    embedding_gate.hold = True
    replacement = asyncio.create_task(_replace(NEW_TEXT, doc_id, tenant_id))
    await asyncio.wait_for(embedding_gate.entered.wait(), timeout=10)

    # Replacement is mid-flight (stuck in embedding): old content still answers.
    context = await _chat_context(doc_id, session_id, tenant_id)
    assert OLD_TEXT in context
    assert "NEW-EDITION" not in context
    status = await db.get_document_status(doc_id, tenant_id)
    assert status["status"] == "completed"
    assert status["status_detail"] == "replacement_in_progress"

    # A second replace while one is running is rejected (route maps this to 409).
    assert await db.begin_document_replacement(doc_id, tenant_id) == "in_progress"

    embedding_gate.release.set()
    await asyncio.wait_for(replacement, timeout=10)

    # Cutover done: the session-bound document now serves only the new content.
    context = await _chat_context(doc_id, session_id, tenant_id)
    assert NEW_TEXT in context
    assert "OLD-EDITION" not in context
    status = await db.get_document_status(doc_id, tenant_id)
    assert status["status"] == "completed"
    assert status["status_detail"] is None
    assert status["error"] is None


@pytest.mark.asyncio
async def test_replacement_never_exposes_status_other_than_completed_midflight(
    tenant_id, embedding_gate
):
    """`status` must not flicker through extracting/embedding/storing (retrieval gate)."""
    doc_id = await _ingest_original(tenant_id, embedding_gate)
    assert await db.begin_document_replacement(doc_id, tenant_id) == "started"

    observed: list[str] = []
    original = db.get_db_service().update_document_status

    async def recording_update(doc_id, status, tenant_id, **kwargs):
        observed.append(status)
        return await original(doc_id, status, tenant_id, **kwargs)

    with patch.object(
        db.get_db_service(), "update_document_status", new=recording_update
    ):
        await _replace(NEW_TEXT, doc_id, tenant_id)

    assert observed == []
    assert (await db.get_document_status(doc_id, tenant_id))["status"] == "completed"


@pytest.mark.asyncio
async def test_failed_replacement_keeps_prior_content_retrievable(
    tenant_id, embedding_gate
):
    doc_id = await _ingest_original(tenant_id, embedding_gate)
    session_id = await _bound_session(doc_id, tenant_id)
    assert await db.begin_document_replacement(doc_id, tenant_id) == "started"

    embedding_gate.fail_with = RuntimeError("embedding provider exploded")
    # The worker re-raises so the queue can apply retry / DLQ logic.
    with pytest.raises(RuntimeError):
        await _replace(NEW_TEXT, doc_id, tenant_id)

    context = await _chat_context(doc_id, session_id, tenant_id)
    assert OLD_TEXT in context
    assert "NEW-EDITION" not in context

    status = await db.get_document_status(doc_id, tenant_id)
    assert status["status"] == "completed"  # not left failed / non-retrievable
    assert status["status_detail"] == "replacement_failed"
    assert status["error"]["stage"] == "embedding"
    assert status["error"]["code"] == "pipeline_error"

    # The failure is retryable: a new replacement can be started and succeeds.
    assert await db.begin_document_replacement(doc_id, tenant_id) == "started"
    embedding_gate.fail_with = None
    await _replace(NEW_TEXT, doc_id, tenant_id)
    context = await _chat_context(doc_id, session_id, tenant_id)
    assert NEW_TEXT in context and "OLD-EDITION" not in context


@pytest.mark.asyncio
async def test_replacement_with_no_extractable_text_keeps_prior_content(
    tenant_id, embedding_gate
):
    doc_id = await _ingest_original(tenant_id, embedding_gate)
    session_id = await _bound_session(doc_id, tenant_id)
    assert await db.begin_document_replacement(doc_id, tenant_id) == "started"

    with pytest.raises(UploadPipelineError) as exc_info:
        await _replace("   ", doc_id, tenant_id)

    assert exc_info.value.code == "no_text_extracted"
    assert OLD_TEXT in await _chat_context(doc_id, session_id, tenant_id)
    status = await db.get_document_status(doc_id, tenant_id)
    assert status["status"] == "completed"
    assert status["status_detail"] == "replacement_failed"
    assert status["error"]["code"] == "no_text_extracted"


@pytest.mark.asyncio
async def test_cutover_applies_file_name_and_provided_metadata(tenant_id, embedding_gate):
    doc_id = await _ingest_original(tenant_id, embedding_gate)
    assert await db.begin_document_replacement(doc_id, tenant_id) == "started"

    await _replace(NEW_TEXT, doc_id, tenant_id, metadata={"rev": 2})

    assert (await db.get_document(doc_id, tenant_id))["file_name"] == "v2.txt"
    assert (await db.get_document_status(doc_id, tenant_id))["metadata"] == {"rev": 2}


@pytest.mark.asyncio
async def test_cutover_keeps_metadata_when_none_provided(tenant_id, embedding_gate):
    doc_id = await _ingest_original(tenant_id, embedding_gate)
    assert await db.begin_document_replacement(doc_id, tenant_id) == "started"

    await _replace(NEW_TEXT, doc_id, tenant_id, metadata=None)

    assert (await db.get_document_status(doc_id, tenant_id))["metadata"] == {"rev": 1}


@pytest.mark.asyncio
async def test_non_replacement_pipeline_still_walks_status_progression(
    tenant_id, embedding_gate
):
    """Regression: the default (replace=False) path reports stage statuses."""
    doc_id = await db.create_document("fresh.txt", tenant_id=tenant_id)
    observed: list[str] = []
    original = db.get_db_service().update_document_status

    async def recording_update(doc_id, status, tenant_id, **kwargs):
        observed.append(status)
        return await original(doc_id, status, tenant_id, **kwargs)

    with patch.object(db.get_db_service(), "update_document_status", new=recording_update):
        with patch(
            "services.ingestion_pipeline.extract_text_with_metadata",
            new=AsyncMock(return_value=(OLD_TEXT, [])),
        ):
            await IngestionPipeline().process_document_background(
                doc_id=doc_id,
                file_name="fresh.txt",
                content_type="text/plain",
                file_bytes=b"x",
                tenant_id=tenant_id,
            )

    assert observed[0] == "extracting"
    assert observed[-1] == "completed"
    assert "storing" in observed
