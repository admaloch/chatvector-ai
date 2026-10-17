import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from core.auth import AuthContext, require_auth, require_current_tenant
from core.config import config
from middleware.rate_limit import limiter

import db
from core.config import STALE_INGESTION_STATUSES
from db.base import REPLACEMENT_IN_PROGRESS
from services.ingestion_job_metadata import build_document_status_response
from services.queue_service import ingestion_queue
from services.tenant_registry import invalidate_tenant_cache

logger = logging.getLogger(__name__)
router = APIRouter()


def _status_response_from_payload(
    document_id: UUID | str,
    status_payload: dict,
) -> dict:
    queue_pos = None
    if (
        status_payload.get("status") == "queued"
        or status_payload.get("status_detail") == REPLACEMENT_IN_PROGRESS
    ):
        queue_pos = ingestion_queue.queue_position(str(document_id))

    return build_document_status_response(
        str(document_id),
        status_payload,
        queue_position=queue_pos,
    )


@router.get("/documents")
@limiter.limit(config.RATE_LIMIT_DOCUMENT_STATUS)
async def list_documents(request: Request, auth: AuthContext = Depends(require_auth)):
    """List documents registered to the authenticated tenant."""
    tenant_id = require_current_tenant(auth)
    documents = await db.list_tenant_document_summaries(tenant_id)
    return {"tenant_id": tenant_id, "documents": documents}


@router.get("/documents/{document_id}/status")
@limiter.limit(config.RATE_LIMIT_DOCUMENT_STATUS)
async def get_document_status(request: Request, document_id: UUID, auth: AuthContext = Depends(require_auth)):
    status_payload = await db.get_document_status(str(document_id), tenant_id=require_current_tenant(auth))
    if not status_payload:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "document_not_found",
                "message": "Document not found.",
                "document_id": document_id,
            },
        )

    return _status_response_from_payload(document_id, status_payload)


@router.get("/documents/{document_id}/status/stream")
@limiter.limit(config.RATE_LIMIT_DOCUMENT_STATUS)
async def get_document_status_stream(request: Request, document_id: UUID, auth: AuthContext = Depends(require_auth)):
    if not config.ENABLE_STREAMING:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "streaming_disabled",
                "message": "Streaming responses are currently disabled.",
            },
        )

    tenant_id = require_current_tenant(auth)

    async def event_generator():
        MAX_POLL_SECONDS = 300
        elapsed = 0
        while elapsed < MAX_POLL_SECONDS:
            if await request.is_disconnected():
                break

            status_payload = await db.get_document_status(str(document_id), tenant_id=tenant_id)
            if not status_payload:
                yield f"event: error\ndata: {json.dumps({'message': 'Document not found.'})}\n\n"
                break

            response = _status_response_from_payload(document_id, status_payload)

            yield f"event: status\ndata: {json.dumps(response)}\n\n"

            status = status_payload.get("status")
            # A replacement keeps `status` completed/failed while it runs, so the
            # stream stays open until the replacement resolves.
            replacing = status_payload.get("status_detail") == REPLACEMENT_IN_PROGRESS
            if status in ("completed", "failed") and not replacing:
                break

            await asyncio.sleep(1)
            elapsed += 1
        
        if elapsed >= MAX_POLL_SECONDS:
            yield f"event: error\ndata: {json.dumps({'message': 'Timed out waiting for status.'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/documents/{document_id}", status_code=204)
@limiter.limit(config.RATE_LIMIT_DOCUMENT_DELETE)
async def delete_document(request: Request, document_id: UUID, auth: AuthContext = Depends(require_auth)):
    status_payload = await db.get_document_status(str(document_id), tenant_id=require_current_tenant(auth))
    if not status_payload:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "document_not_found",
                "message": "Document not found.",
                "document_id": document_id,
            },
        )
    
    status = status_payload.get("status")
    
    # Jobs already picked up by a worker are tracked via status rather than the queue
    if status in STALE_INGESTION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "document_processing",
                "message": f"Document cannot be deleted while in '{status}' state.",
                "document_id": str(document_id),
            },
        )

    if status_payload.get("status_detail") == REPLACEMENT_IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "document_replacing",
                "message": "Document cannot be deleted while a replacement is in progress.",
                "document_id": str(document_id),
            },
        )

    if ingestion_queue.queue_position(str(document_id)) is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "document_queued",
                "message": "Document cannot be deleted while in the queue.",
                "document_id": str(document_id),
            },
        )

    tenant_id = require_current_tenant(auth)
    await db.delete_document(str(document_id), tenant_id=tenant_id)
    invalidate_tenant_cache(tenant_id)
    return Response(status_code=204)
