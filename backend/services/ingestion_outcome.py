"""Shared retry-versus-terminal decision for ingestion attempts.

The pipeline and both queue backends must use this module so a retryable
attempt is not recorded as a terminal failure before the queue re-enqueues it.
"""

from __future__ import annotations

import logging

from core.config import config
from services.queue_base import is_retryable_ingestion_failure

logger = logging.getLogger(__name__)


def ingestion_failure_is_terminal(exc: Exception, attempt: int) -> bool:
    """Return True when this attempt should be a terminal ingestion failure.

    ``attempt`` is 0-based and matches ``QueueJob.attempt``. Client errors and
    other non-retryable failures are terminal immediately. Retryable failures
    become terminal only after ``QUEUE_JOB_MAX_RETRIES`` is exhausted.
    """
    from services.ingestion_pipeline import UploadPipelineError

    if isinstance(exc, UploadPipelineError) and 400 <= exc.status_code < 500:
        return True
    if not is_retryable_ingestion_failure(exc):
        return True
    return attempt >= config.QUEUE_JOB_MAX_RETRIES


async def record_retrying_ingestion_attempt(
    *,
    doc_id: str,
    tenant_id: str,
    replace: bool,
) -> None:
    """Mark work as still in progress. Does not emit a terminal webhook."""
    import db
    from db.base import REPLACEMENT_IN_PROGRESS

    if replace:
        # Leave ``status`` (and the retrievable chunks) untouched.
        await db.set_document_status_detail(
            doc_id,
            REPLACEMENT_IN_PROGRESS,
            tenant_id=tenant_id,
        )
        return

    await db.update_document_status(
        doc_id=doc_id,
        status="retrying",
        tenant_id=tenant_id,
    )


async def record_terminal_ingestion_failure(
    *,
    doc_id: str,
    tenant_id: str,
    error: dict,
    replace: bool,
) -> None:
    """Persist terminal failure metadata and emit one ``ingestion.failed`` webhook."""
    import db
    from db.base import REPLACEMENT_FAILED
    from services.ingestion_job_metadata import patch_terminal_failure

    job_metadata_patch = patch_terminal_failure(error)
    if replace:
        await db.set_document_status_detail(
            doc_id,
            REPLACEMENT_FAILED,
            tenant_id=tenant_id,
            error=error,
            job_metadata_patch=job_metadata_patch,
        )
    else:
        await db.update_document_status(
            doc_id=doc_id,
            status="failed",
            tenant_id=tenant_id,
            error=error,
            job_metadata_patch=job_metadata_patch,
        )

    if not config.WEBHOOK_ENABLED:
        return
    try:
        from services.ingestion_webhook import notify_ingestion_terminal

        await notify_ingestion_terminal(doc_id, tenant_id, outcome="failed")
    except Exception as webhook_error:
        logger.error(
            "Failed to schedule ingestion webhook for document %s: %s",
            doc_id,
            webhook_error,
        )
