"""Env-configured signed webhooks for terminal ingestion states."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
import uuid
from typing import Any

import httpx

from core.config import config

logger = logging.getLogger(__name__)

WEBHOOK_QUEUE_NAME = "chatvector-webhooks"
SIGNATURE_HEADER = "X-ChatVector-Signature"
EVENT_ID_HEADER = "X-ChatVector-Event-Id"

EVENT_COMPLETED = "ingestion.completed"
EVENT_FAILED = "ingestion.failed"

_TIMESTAMP_KEYS = ("enqueued_at", "started_at", "completed_at")


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Stable JSON encoding used for HMAC signing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_terminal_payload(
    *,
    event_type: str,
    document_id: str,
    tenant_id: str,
    status: str,
    job_metadata: dict[str, Any] | None,
    error: dict[str, Any] | None,
    event_id: str | None = None,
) -> dict[str, Any]:
    event_id = event_id or str(uuid.uuid4())
    timestamps: dict[str, str] = {}
    if job_metadata:
        for key in _TIMESTAMP_KEYS:
            value = job_metadata.get(key)
            if isinstance(value, str) and value.strip():
                timestamps[key] = value

    payload: dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type,
        "document_id": document_id,
        "tenant_id": tenant_id,
        "status": status,
        "timestamps": timestamps,
    }
    if error is not None:
        payload["error_summary"] = _error_summary(error)
    return payload


def _error_summary(error: dict[str, Any]) -> dict[str, str]:
    summary: dict[str, str] = {}
    code = error.get("code")
    message = error.get("message")
    stage = error.get("stage")
    if isinstance(code, str) and code.strip():
        summary["code"] = code.strip()
    if isinstance(stage, str) and stage.strip():
        summary["stage"] = stage.strip()
    if isinstance(message, str) and message.strip():
        summary["message"] = message.strip()
    if not summary:
        summary["message"] = "Ingestion failed."
    return summary


def _webhook_retry_delay_seconds(attempt: int) -> float:
    base = max(0.1, float(config.QUEUE_RETRY_BASE_DELAY))
    cap = base * (2 ** max(1, attempt))
    return random.uniform(0, cap)


def _post_webhook(payload: dict[str, Any]) -> None:
    if not config.WEBHOOK_URL or not config.WEBHOOK_SECRET:
        raise RuntimeError("WEBHOOK_URL and WEBHOOK_SECRET are required when webhooks are enabled")

    body = canonical_json_bytes(payload)
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: compute_signature(config.WEBHOOK_SECRET, body),
        EVENT_ID_HEADER: str(payload["event_id"]),
    }
    timeout = httpx.Timeout(config.WEBHOOK_TIMEOUT_SECONDS)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(config.WEBHOOK_URL, content=body, headers=headers)
        response.raise_for_status()


def deliver_ingestion_webhook_event(payload: dict[str, Any], attempt: int = 1) -> None:
    """
    RQ entry point: POST signed payload with bounded retries (re-enqueue on failure).
    Payload is fully contained in job args — no spill files.
    """
    if not config.WEBHOOK_ENABLED:
        return

    try:
        _post_webhook(payload)
        logger.info(
            "Delivered webhook %s for document %s (attempt %d)",
            payload.get("event_type"),
            payload.get("document_id"),
            attempt,
        )
    except Exception as exc:
        if attempt >= config.WEBHOOK_MAX_ATTEMPTS:
            logger.error(
                "Webhook delivery exhausted %d attempts for event %s document %s: %s",
                config.WEBHOOK_MAX_ATTEMPTS,
                payload.get("event_id"),
                payload.get("document_id"),
                exc,
            )
            return

        delay = _webhook_retry_delay_seconds(attempt)
        logger.warning(
            "Webhook delivery attempt %d failed for event %s — retrying in %.2fs: %s",
            attempt,
            payload.get("event_id"),
            delay,
            exc,
        )
        time.sleep(delay)
        _sync_enqueue_webhook_delivery(payload, attempt=attempt + 1)


def _sync_enqueue_webhook_delivery(payload: dict[str, Any], *, attempt: int = 1) -> None:
    import redis as redis_lib
    from rq import Queue as RQQueue

    from core.config import redis_connection_kwargs

    conn = redis_lib.Redis.from_url(config.REDIS_URL, **redis_connection_kwargs())
    queue = RQQueue(WEBHOOK_QUEUE_NAME, connection=conn)
    event_id = payload.get("event_id", "unknown")
    queue.enqueue(
        deliver_ingestion_webhook_event,
        payload,
        attempt,
        job_id=f"chatvector:webhook:{event_id}:{attempt}",
        job_timeout=max(30, int(config.WEBHOOK_TIMEOUT_SECONDS) + 5),
    )


async def _async_deliver_with_retries(payload: dict[str, Any], attempt: int = 1) -> None:
    try:
        await asyncio.to_thread(_post_webhook, payload)
        logger.info(
            "Delivered webhook %s for document %s (attempt %d, memory backend)",
            payload.get("event_type"),
            payload.get("document_id"),
            attempt,
        )
    except Exception as exc:
        if attempt >= config.WEBHOOK_MAX_ATTEMPTS:
            logger.error(
                "Webhook delivery exhausted %d attempts for event %s document %s: %s",
                config.WEBHOOK_MAX_ATTEMPTS,
                payload.get("event_id"),
                payload.get("document_id"),
                exc,
            )
            return
        delay = _webhook_retry_delay_seconds(attempt)
        logger.warning(
            "Webhook delivery attempt %d failed for event %s — retrying in %.2fs: %s",
            attempt,
            payload.get("event_id"),
            delay,
            exc,
        )
        await asyncio.sleep(delay)
        await _async_deliver_with_retries(payload, attempt=attempt + 1)


async def schedule_ingestion_webhook_delivery(payload: dict[str, Any]) -> None:
    if config.QUEUE_BACKEND == "redis":
        await asyncio.to_thread(_sync_enqueue_webhook_delivery, payload, attempt=1)
    else:
        asyncio.create_task(_async_deliver_with_retries(payload, attempt=1))


async def notify_ingestion_terminal(
    doc_id: str,
    tenant_id: str,
    *,
    outcome: str,
) -> None:
    """Fetch latest document status and enqueue a terminal webhook when enabled."""
    if not config.WEBHOOK_ENABLED:
        return

    import db as db_module

    status_payload = await db_module.get_document_status(doc_id, tenant_id=tenant_id)
    if not status_payload:
        logger.warning("Skipping webhook for missing document %s", doc_id)
        return

    db_status = status_payload.get("status")
    status_detail = status_payload.get("status_detail")
    error = status_payload.get("error")
    job_metadata = status_payload.get("job_metadata")

    if outcome == "completed":
        event_type = EVENT_COMPLETED
        webhook_status = "completed"
    else:
        event_type = EVENT_FAILED
        webhook_status = "failed"

    if outcome == "failed" and db_status == "completed" and status_detail:
        webhook_status = "failed"

    payload = build_terminal_payload(
        event_type=event_type,
        document_id=doc_id,
        tenant_id=tenant_id,
        status=webhook_status,
        job_metadata=job_metadata if isinstance(job_metadata, dict) else None,
        error=error if isinstance(error, dict) else None,
    )
    await schedule_ingestion_webhook_delivery(payload)
