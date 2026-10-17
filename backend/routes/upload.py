import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from core.auth import AuthContext, require_auth, require_current_tenant
from core.config import config
from middleware.rate_limit import limiter

import db
from db.base import REPLACEMENT_FAILED
from services.document_metadata import parse_document_metadata
from services.ingestion_pipeline import IngestionPipeline, UploadPipelineError, _sanitize_filename
from services.queue_service import QueueFull, QueueJob, ingestion_queue
from services.session_service import get_or_create_session, register_session_document
from services.idempotency import (
    ROUTE_UPLOAD,
    begin_idempotency,
    complete_idempotent_http_exception,
    parse_idempotency_key,
    upload_request_fingerprint,
)
from services.tenant_registry import register_tenant_document

logger = logging.getLogger(__name__)
router = APIRouter()
ingestion_pipeline = IngestionPipeline()


def _http_error(
    status_code: int,
    code: str,
    stage: str,
    message: str,
    document_id: str | None = None,
    headers: dict | None = None,
) -> HTTPException:
    detail = {
        "code": code,
        "stage": stage,
        "message": message,
    }
    if document_id:
        detail["document_id"] = document_id
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def _parse_replace_document_id(raw: str | None) -> str | None:
    """Validate the optional ``replace_document_id`` form field (canonical UUID)."""
    if raw is None:
        return None
    try:
        return str(uuid.UUID(raw.strip()))
    except ValueError:
        raise UploadPipelineError(
            status_code=422,
            code="invalid_replace_document_id",
            stage="validation",
            message="replace_document_id must be a valid UUID.",
        ) from None


@router.post("/upload", status_code=202)
@limiter.limit(config.RATE_LIMIT_UPLOAD)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    metadata: Annotated[str | None, Form()] = None,
    replace_document_id: Annotated[str | None, Form()] = None,
    auth: AuthContext = Depends(require_auth),
):
    """
    Accept a file upload, validate it, and enqueue it for background processing.

    The optional ``metadata`` form field is a JSON object of string, number, or
    boolean values stored on the document; invalid metadata is rejected with 422.

    The optional ``replace_document_id`` form field (a UUID) re-ingests content for
    an existing tenant-owned document that is ``completed`` or ``failed``. The
    document keeps its ID and its prior completed content stays retrievable
    until the new content is fully processed and swapped in; a failed replacement
    leaves the prior content untouched. A replacement already running returns 409.

    Returns immediately (< 500 ms) with the document ID and queue position so
    the client can poll /documents/{document_id}/status for progress.
    """
    doc_id: str | None = None
    replacing = False
    tenant_id = require_current_tenant(auth)
    idempotency_lease = None

    try:
        safe_filename = _sanitize_filename(file.filename)
        file_bytes = await file.read()

        # Validate synchronously before touching the DB
        ingestion_pipeline.validate_file(file, file_bytes)
        document_metadata = parse_document_metadata(metadata)
        replace_id = _parse_replace_document_id(replace_document_id)

        fingerprint = upload_request_fingerprint(
            file_bytes=file_bytes,
            filename=safe_filename,
            content_type=file.content_type,
            replace_document_id=replace_id,
        )
        idempotency_key = parse_idempotency_key(request)
        idempotency_lease, replay = await begin_idempotency(
            tenant_id=tenant_id,
            route=ROUTE_UPLOAD,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay

        if replace_id is None:
            # Persist the document record so the status endpoint works immediately
            doc_id = await db.create_document(
                safe_filename, tenant_id=tenant_id, metadata=document_metadata
            )
            register_tenant_document(tenant_id, doc_id)
            await db.update_document_status(doc_id=doc_id, status="queued", tenant_id=tenant_id)
        else:
            # Claim the document atomically. `status` and the active chunks are
            # not touched: prior completed content stays retrievable.
            outcome = await db.begin_document_replacement(replace_id, tenant_id=tenant_id)
            if outcome == "not_found":
                raise _http_error(
                    status_code=404,
                    code="document_not_found",
                    stage="validation",
                    message="Document not found.",
                    document_id=replace_id,
                )
            if outcome == "in_progress":
                raise _http_error(
                    status_code=409,
                    code="replacement_in_progress",
                    stage="validation",
                    message="A replacement for this document is already in progress.",
                    document_id=replace_id,
                )
            if outcome == "not_eligible":
                raise _http_error(
                    status_code=409,
                    code="document_processing",
                    stage="validation",
                    message=(
                        "Document can only be replaced when it is in 'completed' "
                        "or 'failed' state."
                    ),
                    document_id=replace_id,
                )
            doc_id = replace_id
            replacing = True

        job = QueueJob(
            doc_id=doc_id,
            file_name=safe_filename,
            content_type=file.content_type,
            file_bytes=file_bytes,
            tenant_id=tenant_id,
            replace=replacing,
            # Replacement keeps the existing metadata unless the client sent some.
            metadata=document_metadata if replacing and metadata is not None else None,
        )

        try:
            queue_position = await ingestion_queue.enqueue(job)
        except QueueFull:
            queue_full_error = {
                "stage": "queued",
                "message": "Queue is at capacity. Please retry later.",
            }
            if replacing:
                # The prior content is still active; only record the failed attempt.
                await db.set_document_status_detail(
                    doc_id,
                    REPLACEMENT_FAILED,
                    tenant_id=tenant_id,
                    error=queue_full_error,
                )
            else:
                # Roll back the document record so the DB stays clean
                await db.update_document_status(
                    doc_id=doc_id,
                    status="failed",
                    error=queue_full_error,
                    tenant_id=tenant_id,
                )
            raise _http_error(
                    status_code=503,
                    code="queue_full",
                    stage="queued",
                    message="The processing queue is currently full. Please try again later.",
                    document_id=doc_id,
                    headers={"Retry-After": "30"},
                )

        session_id = request.headers.get("x-session-id")
        if session_id:
            session = await get_or_create_session(
                session_id=session_id.strip(), tenant_id=tenant_id
            )
            await register_session_document(session.id, doc_id, tenant_id)

        logger.info(
            f"Accepted upload {safe_filename!r} → document {doc_id} "
            f"at queue position {queue_position}"
        )

        if replacing:
            # `status` stays whatever it was (completed keeps serving retrieval);
            # `status_detail` tells the client a replacement is underway.
            current = await db.get_document_status(doc_id, tenant_id=tenant_id) or {}
            response_body = {
                "message": "Accepted",
                "document_id": doc_id,
                "status": current.get("status", "completed"),
                "status_detail": "replacement_in_progress",
                "queue_position": queue_position,
                "status_endpoint": f"/documents/{doc_id}/status",
            }
            await idempotency_lease.complete(202, response_body)
            return response_body

        response_body = {
            "message": "Accepted",
            "document_id": doc_id,
            "status": "queued",
            "queue_position": queue_position,
            "status_endpoint": f"/documents/{doc_id}/status",
        }
        await idempotency_lease.complete(202, response_body)
        return response_body

    except HTTPException as exc:
        if idempotency_lease is not None and idempotency_lease.active:
            await complete_idempotent_http_exception(idempotency_lease, exc)
        raise

    except UploadPipelineError as e:
        if doc_id and not e.document_id:
            e.document_id = doc_id
        logger.warning(
            f"Upload validation failed at stage={e.stage}: {e.message}"
        )
        http_exc = _http_error(
            status_code=e.status_code,
            code=e.code,
            stage=e.stage,
            message=e.message,
            document_id=getattr(e, "document_id", None),
        )
        if idempotency_lease is not None and idempotency_lease.active:
            await complete_idempotent_http_exception(idempotency_lease, http_exc)
        raise http_exc

    except Exception as e:
        if idempotency_lease is not None and idempotency_lease.active:
            await idempotency_lease.abort()
        if doc_id and replacing:
            # Never mark a replaced document `failed`: its prior content must
            # stay retrievable. Record the failed attempt in status_detail.
            await db.set_document_status_detail(
                doc_id,
                REPLACEMENT_FAILED,
                tenant_id=tenant_id,
                error={
                    "stage": "queued",
                    "code": "upload_failed",
                    "message": "An unexpected error occurred during upload.",
                },
            )
        elif doc_id:
            await db.update_document_status(
                doc_id=doc_id,
                status="failed",
                error={
                    "stage": "queued", 
                    "code": "upload_failed", 
                    "message": "An unexpected error occurred during upload."
                },
                tenant_id=tenant_id,
            )
        log_msg = (
            f"Unexpected error during upload of {safe_filename!r} "
            f"(tenant_id={tenant_id!r}): {e}"
        )
        err_name = type(e).__name__
        if "IntegrityError" in err_name or "ForeignKeyViolation" in str(e):
            log_msg += (
                ". This may indicate the development tenant was not bootstrapped "
                "(check startup logs for DEV_TENANT_ID initialization)."
            )
        logger.error(log_msg, exc_info=True)
        raise _http_error(
            status_code=500,
            code="upload_failed",
            stage="queued",
            message="Upload failed. Please try again.",
            document_id=doc_id,
        )
