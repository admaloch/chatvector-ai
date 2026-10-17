"""Validation and persistence for structured retrieval feedback."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

import db
from services import session_service

FeedbackType = Literal["answer", "citation", "retrieval"]

DOC_LINKED_TYPES = frozenset({"citation", "retrieval"})


class FeedbackValidationError(ValueError):
    """Raised when feedback payload fails linkage or reference checks."""


async def create_feedback(
    *,
    tenant_id: str,
    session_id: str,
    feedback_type: FeedbackType,
    rating: int,
    comment: Optional[str],
    message_id: Optional[UUID],
    document_id: Optional[UUID],
    chunk_index: Optional[int],
) -> dict:
    session = await session_service.get_session(session_id=session_id, tenant_id=tenant_id)
    if not session:
        raise FeedbackValidationError("session_not_found")

    if feedback_type == "answer":
        if message_id is None:
            raise FeedbackValidationError("message_id_required")
        await _validate_message(message_id, session_id=session_id, tenant_id=tenant_id)
    elif feedback_type in DOC_LINKED_TYPES:
        if message_id is not None:
            await _validate_message(message_id, session_id=session_id, tenant_id=tenant_id)
        elif document_id is None:
            raise FeedbackValidationError("document_link_required")
        else:
            await _validate_document(document_id, tenant_id=tenant_id)
    else:
        raise FeedbackValidationError("invalid_feedback_type")

    if document_id is not None and message_id is None:
        await _validate_document(document_id, tenant_id=tenant_id)

    return await db.create_retrieval_feedback(
        tenant_id=tenant_id,
        session_id=session_id,
        feedback_type=feedback_type,
        rating=rating,
        comment=comment,
        message_id=str(message_id) if message_id else None,
        document_id=str(document_id) if document_id else None,
        chunk_index=chunk_index,
    )


async def list_feedback(
    *,
    tenant_id: str,
    session_id: Optional[str] = None,
    created_after: Optional[datetime] = None,
    created_before: Optional[datetime] = None,
    limit: int = 50,
) -> list[dict]:
    if session_id is not None:
        session = await session_service.get_session(session_id=session_id, tenant_id=tenant_id)
        if not session:
            raise FeedbackValidationError("session_not_found")

    return await db.list_retrieval_feedback(
        tenant_id=tenant_id,
        session_id=session_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
    )


async def _validate_message(message_id: UUID, *, session_id: str, tenant_id: str) -> None:
    message = await db.get_chat_message(str(message_id), tenant_id=tenant_id)
    if not message:
        raise FeedbackValidationError("message_not_found")
    if message["session_id"] != session_id:
        raise FeedbackValidationError("message_session_mismatch")


async def _validate_document(document_id: UUID, *, tenant_id: str) -> None:
    doc = await db.get_document(str(document_id), tenant_id=tenant_id)
    if not doc:
        raise FeedbackValidationError("document_not_found")
