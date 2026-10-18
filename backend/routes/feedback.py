import logging
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.auth import AuthContext, require_auth, require_current_tenant
from core.config import config
from middleware.rate_limit import limiter
from services.feedback_service import FeedbackValidationError, create_feedback, list_feedback

logger = logging.getLogger(__name__)
router = APIRouter()


class FeedbackCreateRequest(BaseModel):
    type: Literal["answer", "citation", "retrieval"] = Field(
        ...,
        description="Feedback category: answer (message-linked), citation, or retrieval.",
    )
    rating: Literal[-1, 1] = Field(..., description="Thumbs down (-1) or up (1).")
    comment: Optional[str] = Field(None, max_length=4096)
    session_id: str = Field(..., min_length=1, max_length=255)
    message_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    chunk_index: Optional[int] = Field(None, ge=0)


class FeedbackItemResponse(BaseModel):
    id: str
    type: str
    rating: int
    comment: Optional[str] = None
    session_id: str
    message_id: Optional[str] = None
    document_id: Optional[str] = None
    chunk_index: Optional[int] = None
    created_at: str


class FeedbackListResponse(BaseModel):
    feedback: list[FeedbackItemResponse]


def _format_feedback(row: dict) -> FeedbackItemResponse:
    return FeedbackItemResponse(
        id=row["id"],
        type=row["type"],
        rating=row["rating"],
        comment=row.get("comment"),
        session_id=row["session_id"],
        message_id=row.get("message_id"),
        document_id=row.get("document_id"),
        chunk_index=row.get("chunk_index"),
        created_at=row["created_at"],
    )


def _validation_to_http(exc: FeedbackValidationError) -> HTTPException:
    code = str(exc)
    if code in ("session_not_found", "message_not_found", "document_not_found"):
        return HTTPException(status_code=404, detail=code)
    if code == "message_session_mismatch":
        return HTTPException(status_code=404, detail=code)
    if code in (
        "message_id_required",
        "document_link_required",
        "invalid_feedback_type",
    ):
        return HTTPException(status_code=422, detail=code)
    return HTTPException(status_code=422, detail=code)


@router.post("/feedback", status_code=201, response_model=FeedbackItemResponse)
@limiter.limit(config.RATE_LIMIT_FEEDBACK)
async def post_feedback(
    request: Request,
    payload: FeedbackCreateRequest,
    auth: AuthContext = Depends(require_auth),
):
    tenant_id = require_current_tenant(auth)
    try:
        row = await create_feedback(
            tenant_id=tenant_id,
            session_id=payload.session_id,
            feedback_type=payload.type,
            rating=payload.rating,
            comment=payload.comment,
            message_id=payload.message_id,
            document_id=payload.document_id,
            chunk_index=payload.chunk_index,
        )
    except FeedbackValidationError as exc:
        raise _validation_to_http(exc) from exc
    return _format_feedback(row)


@router.get("/feedback", response_model=FeedbackListResponse)
@limiter.limit(config.RATE_LIMIT_FEEDBACK)
async def get_feedback_list(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    session_id: Optional[str] = Query(None, min_length=1, max_length=255),
    created_after: Optional[datetime] = Query(None),
    created_before: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    tenant_id = require_current_tenant(auth)
    try:
        rows = await list_feedback(
            tenant_id=tenant_id,
            session_id=session_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except FeedbackValidationError as exc:
        raise _validation_to_http(exc) from exc
    return FeedbackListResponse(feedback=[_format_feedback(row) for row in rows])
