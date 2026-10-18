"""Tests for POST/GET /feedback and tenant isolation."""

from __future__ import annotations

import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from core.auth import AuthContext
from request_utils import make_test_request
from routes.feedback import (
    FeedbackCreateRequest,
    FeedbackListResponse,
    get_feedback_list,
    post_feedback,
)


@pytest.mark.asyncio
async def test_post_feedback_success_with_message():
    row = {
        "id": "fb-1",
        "type": "answer",
        "rating": 1,
        "comment": "helpful",
        "session_id": "sess-1",
        "message_id": "msg-1",
        "document_id": None,
        "chunk_index": None,
        "created_at": "2026-01-01T00:00:00",
    }
    with patch(
        "routes.feedback.create_feedback",
        new=AsyncMock(return_value=row),
    ) as mock_create:
        result = await post_feedback(
            make_test_request("POST", "/feedback"),
            FeedbackCreateRequest(
                type="answer",
                rating=1,
                comment="helpful",
                session_id="sess-1",
                message_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            ),
            auth=AuthContext(tenant_id="tenant-1"),
        )
        assert result.id == "fb-1"
        assert result.type == "answer"
        mock_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_feedback_not_found_maps_to_404():
    from services.feedback_service import FeedbackValidationError

    with patch(
        "routes.feedback.create_feedback",
        new=AsyncMock(side_effect=FeedbackValidationError("message_not_found")),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await post_feedback(
                make_test_request("POST", "/feedback"),
                FeedbackCreateRequest(
                    type="answer",
                    rating=-1,
                    session_id="sess-1",
                    message_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                ),
                auth=AuthContext(tenant_id="tenant-1"),
            )
        assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_list_feedback_success():
    rows = [
        {
            "id": "fb-1",
            "type": "citation",
            "rating": -1,
            "comment": None,
            "session_id": "sess-1",
            "message_id": None,
            "document_id": "00000000-0000-0000-0000-000000000002",
            "chunk_index": 0,
            "created_at": "2026-01-01T00:00:00",
        }
    ]
    with patch(
        "routes.feedback.list_feedback",
        new=AsyncMock(return_value=rows),
    ) as mock_list:
        result = await get_feedback_list(
            make_test_request("GET", "/feedback"),
            auth=AuthContext(tenant_id="tenant-1"),
        )
        assert isinstance(result, FeedbackListResponse)
        assert len(result.feedback) == 1
        assert result.feedback[0].type == "citation"
        mock_list.assert_awaited_once()


async def _fresh_service():
    pytest.importorskip("pgvector")
    if sys.platform == "win32":
        pytest.skip("Psycopg async mode not supported with ProactorEventLoop on Windows")
    from db.sqlalchemy_service import SQLAlchemyService

    return SQLAlchemyService()


async def _feedback_table_exists(svc) -> bool:
    from sqlalchemy import text

    async with svc.engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'retrieval_feedback'"
            )
        )
        return result.fetchone() is not None


@pytest.mark.asyncio
async def test_feedback_persists_and_lists_under_tenant():
    svc = await _fresh_service()
    if not await _feedback_table_exists(svc):
        pytest.skip("retrieval_feedback table not migrated")

    tenant_id = f"tenant-fb-{uuid.uuid4()}"
    session_id = f"session-fb-{uuid.uuid4()}"

    await svc.create_session_record(session_id, tenant_id)
    user_id, assistant_id = await svc.store_chat_turn(
        session_id, "question?", "answer.", tenant_id=tenant_id
    )

    from services.feedback_service import create_feedback, list_feedback

    created = await create_feedback(
        tenant_id=tenant_id,
        session_id=session_id,
        feedback_type="answer",
        rating=1,
        comment="good",
        message_id=uuid.UUID(assistant_id),
        document_id=None,
        chunk_index=None,
    )
    assert created["type"] == "answer"
    assert created["message_id"] == assistant_id

    listed = await list_feedback(tenant_id=tenant_id, session_id=session_id)
    assert any(item["id"] == created["id"] for item in listed)

    await svc.engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_message_rejected():
    svc = await _fresh_service()
    if not await _feedback_table_exists(svc):
        pytest.skip("retrieval_feedback table not migrated")

    from services.api_key_service import create_tenant, reset_session_factory

    tenant_a = f"tenant-a-{uuid.uuid4()}"
    tenant_b = f"tenant-b-{uuid.uuid4()}"
    session_a = f"session-a-{uuid.uuid4()}"
    session_b = f"session-b-{uuid.uuid4()}"

    await create_tenant("Feedback tenant A", tenant_id=tenant_a)
    await create_tenant("Feedback tenant B", tenant_id=tenant_b)
    await svc.create_session_record(session_a, tenant_a)
    await svc.create_session_record(session_b, tenant_b)
    _, assistant_id = await svc.store_chat_turn(
        session_a, "q", "a", tenant_id=tenant_a
    )

    from services.feedback_service import FeedbackValidationError, create_feedback

    with pytest.raises(FeedbackValidationError, match="message_not_found"):
        await create_feedback(
            tenant_id=tenant_b,
            session_id=session_b,
            feedback_type="answer",
            rating=-1,
            comment=None,
            message_id=uuid.UUID(assistant_id),
            document_id=None,
            chunk_index=None,
        )

    reset_session_factory()
    await svc.engine.dispose()


@pytest.mark.asyncio
async def test_citation_without_message_id_requires_document():
    svc = await _fresh_service()
    if not await _feedback_table_exists(svc):
        pytest.skip("retrieval_feedback table not migrated")

    from services.api_key_service import create_tenant, reset_session_factory

    tenant_id = f"tenant-fb-{uuid.uuid4()}"
    session_id = f"session-fb-{uuid.uuid4()}"
    await create_tenant("Feedback citation test", tenant_id=tenant_id)
    doc_id = await svc.create_document("feedback.pdf", tenant_id=tenant_id)

    await svc.create_session_record(session_id, tenant_id)

    from services.feedback_service import FeedbackValidationError, create_feedback

    with pytest.raises(FeedbackValidationError, match="document_link_required"):
        await create_feedback(
            tenant_id=tenant_id,
            session_id=session_id,
            feedback_type="citation",
            rating=1,
            comment=None,
            message_id=None,
            document_id=None,
            chunk_index=None,
        )

    created = await create_feedback(
        tenant_id=tenant_id,
        session_id=session_id,
        feedback_type="citation",
        rating=-1,
        comment=None,
        message_id=None,
        document_id=uuid.UUID(doc_id),
        chunk_index=2,
    )
    assert created["document_id"] == doc_id
    assert created["chunk_index"] == 2

    reset_session_factory()
    await svc.engine.dispose()


@pytest.mark.asyncio
async def test_both_ids_validate_document_before_store():
    """A valid message_id must not skip document_id checks or persistence."""
    from services.feedback_service import FeedbackValidationError, create_feedback

    message_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    document_id = uuid.UUID("00000000-0000-0000-0000-000000000022")
    session = {"id": "sess-1"}
    message = {"session_id": "sess-1"}
    stored = {
        "id": "fb-both",
        "type": "citation",
        "rating": 1,
        "comment": None,
        "session_id": "sess-1",
        "message_id": str(message_id),
        "document_id": str(document_id),
        "chunk_index": 0,
        "created_at": "2026-01-01T00:00:00",
    }

    with (
        patch(
            "services.feedback_service.session_service.get_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "services.feedback_service.db.get_chat_message",
            new=AsyncMock(return_value=message),
        ) as mock_message,
        patch(
            "services.feedback_service.db.get_document",
            new=AsyncMock(return_value={"id": str(document_id)}),
        ) as mock_document,
        patch(
            "services.feedback_service.db.create_retrieval_feedback",
            new=AsyncMock(return_value=stored),
        ) as mock_create,
    ):
        created = await create_feedback(
            tenant_id="tenant-1",
            session_id="sess-1",
            feedback_type="citation",
            rating=1,
            comment=None,
            message_id=message_id,
            document_id=document_id,
            chunk_index=0,
        )

    assert created["document_id"] == str(document_id)
    mock_message.assert_awaited_once()
    mock_document.assert_awaited_once_with(str(document_id), tenant_id="tenant-1")
    mock_create.assert_awaited_once()

    with (
        patch(
            "services.feedback_service.session_service.get_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "services.feedback_service.db.get_chat_message",
            new=AsyncMock(return_value=message),
        ),
        patch(
            "services.feedback_service.db.get_document",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.feedback_service.db.create_retrieval_feedback",
            new=AsyncMock(),
        ) as mock_create_rejected,
    ):
        with pytest.raises(FeedbackValidationError, match="document_not_found"):
            await create_feedback(
                tenant_id="tenant-1",
                session_id="sess-1",
                feedback_type="retrieval",
                rating=-1,
                comment=None,
                message_id=message_id,
                document_id=document_id,
                chunk_index=None,
            )
    mock_create_rejected.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_tenant_message_and_document_feedback_persists():
    svc = await _fresh_service()
    if not await _feedback_table_exists(svc):
        pytest.skip("retrieval_feedback table not migrated")

    from services.api_key_service import create_tenant, reset_session_factory
    from services.feedback_service import create_feedback, list_feedback

    tenant_id = f"tenant-fb-{uuid.uuid4()}"
    session_id = f"session-fb-{uuid.uuid4()}"
    await create_tenant("Feedback both ids", tenant_id=tenant_id)
    await svc.create_session_record(session_id, tenant_id)
    _, assistant_id = await svc.store_chat_turn(
        session_id, "question?", "answer.", tenant_id=tenant_id
    )
    doc_id = await svc.create_document("feedback.pdf", tenant_id=tenant_id)

    created = await create_feedback(
        tenant_id=tenant_id,
        session_id=session_id,
        feedback_type="citation",
        rating=1,
        comment="both ids",
        message_id=uuid.UUID(assistant_id),
        document_id=uuid.UUID(doc_id),
        chunk_index=1,
    )
    assert created["message_id"] == assistant_id
    assert created["document_id"] == doc_id

    listed = await list_feedback(tenant_id=tenant_id, session_id=session_id)
    assert any(item["id"] == created["id"] and item["document_id"] == doc_id for item in listed)

    reset_session_factory()
    await svc.engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_document_not_stored_when_message_is_valid():
    """Cross-tenant and missing document_id share one non-oracular 404 code."""
    svc = await _fresh_service()
    if not await _feedback_table_exists(svc):
        pytest.skip("retrieval_feedback table not migrated")

    from services.api_key_service import create_tenant, reset_session_factory
    from services.feedback_service import FeedbackValidationError, create_feedback, list_feedback

    tenant_a = f"tenant-a-{uuid.uuid4()}"
    tenant_b = f"tenant-b-{uuid.uuid4()}"
    session_id = f"session-fb-{uuid.uuid4()}"
    await create_tenant("Feedback owner", tenant_id=tenant_a)
    await create_tenant("Feedback other", tenant_id=tenant_b)
    await svc.create_session_record(session_id, tenant_a)
    _, assistant_id = await svc.store_chat_turn(
        session_id, "question?", "answer.", tenant_id=tenant_a
    )
    foreign_doc = await svc.create_document("other.pdf", tenant_id=tenant_b)
    missing_doc = uuid.uuid4()

    with pytest.raises(FeedbackValidationError, match="document_not_found") as cross_tenant:
        await create_feedback(
            tenant_id=tenant_a,
            session_id=session_id,
            feedback_type="citation",
            rating=-1,
            comment=None,
            message_id=uuid.UUID(assistant_id),
            document_id=uuid.UUID(foreign_doc),
            chunk_index=0,
        )
    with pytest.raises(FeedbackValidationError, match="document_not_found") as missing:
        await create_feedback(
            tenant_id=tenant_a,
            session_id=session_id,
            feedback_type="citation",
            rating=-1,
            comment=None,
            message_id=uuid.UUID(assistant_id),
            document_id=missing_doc,
            chunk_index=0,
        )

    assert str(cross_tenant.value) == str(missing.value) == "document_not_found"
    listed = await list_feedback(tenant_id=tenant_a, session_id=session_id)
    assert listed == []

    reset_session_factory()
    await svc.engine.dispose()
