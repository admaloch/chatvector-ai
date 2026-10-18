"""Redis-backed idempotency for mutating API routes."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping

import orjson
from fastapi import HTTPException, Request
from redis.exceptions import RedisError
from starlette.responses import JSONResponse

from core.clients import redis_client
from core.config import config

logger = logging.getLogger(__name__)

ROUTE_UPLOAD = "post:upload"
ROUTE_SESSION_CREATE = "post:sessions"

_STATUS_IN_PROGRESS = "in_progress"
_STATUS_COMPLETED = "completed"


def _canonical_metadata_bytes(metadata: Mapping[str, Any] | None) -> bytes:
    """Stable metadata bytes so key order does not change the fingerprint.

    ``None`` means the client omitted metadata. An explicit object, including
    an empty object, is canonical JSON.
    """
    if metadata is None:
        return b""
    return orjson.dumps(dict(metadata), option=orjson.OPT_SORT_KEYS)


def upload_request_fingerprint(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    replace_document_id: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """Logical upload fingerprint (not raw multipart transport bytes)."""
    parts: list[bytes] = [
        hashlib.sha256(file_bytes).digest(),
        filename.encode("utf-8"),
    ]
    if content_type:
        parts.append(content_type.encode("utf-8"))
    if replace_document_id:
        parts.append(replace_document_id.encode("utf-8"))
    parts.append(_canonical_metadata_bytes(metadata))
    joined = b"\x00".join(parts)
    return hashlib.sha256(joined).hexdigest()


def session_create_fingerprint(*, session_id: str | None) -> str:
    """Fingerprint for POST /sessions (tenant is scoped via the Redis key)."""
    normalized = (session_id or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_idempotency_key(request: Request) -> str | None:
    raw = request.headers.get("Idempotency-Key")
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_idempotency_key",
                "message": "Idempotency-Key must not be empty.",
            },
        )
    max_len = config.IDEMPOTENCY_KEY_MAX_LENGTH
    if len(key) > max_len:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_idempotency_key",
                "message": f"Idempotency-Key must be at most {max_len} characters.",
            },
        )
    return key


def _redis_key(*, tenant_id: str, route: str, idempotency_key: str) -> str:
    return (
        f"{config.IDEMPOTENCY_KEY_PREFIX}:{tenant_id}:{route}:{idempotency_key}"
    )


def _mismatch_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "idempotency_key_mismatch",
            "message": (
                "This Idempotency-Key was already used with a different request body."
            ),
        },
    )


def _in_progress_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "idempotency_in_progress",
            "message": (
                "A request with this Idempotency-Key is still in progress. Retry shortly."
            ),
        },
    )


def _store_unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "idempotency_store_unavailable",
            "message": "Idempotency store is unavailable. Retry later.",
        },
    )


@dataclass
class IdempotencyLease:
    """Handle for an idempotency claim; no-op when no key was supplied."""

    _redis_key: str | None
    _fingerprint: str
    _finished: bool = False

    @property
    def active(self) -> bool:
        return self._redis_key is not None

    async def complete(self, status_code: int, body: Any) -> None:
        if not self.active or self._finished:
            return
        record = {
            "status": _STATUS_COMPLETED,
            "fingerprint": self._fingerprint,
            "status_code": status_code,
            "body": body,
        }
        await _write_record(self._redis_key, record)
        self._finished = True

    async def abort(self) -> None:
        if not self.active or self._finished:
            return
        try:
            raw = await redis_client.get(self._redis_key)
            if raw:
                record = orjson.loads(raw)
                if record.get("status") == _STATUS_IN_PROGRESS:
                    await redis_client.delete(self._redis_key)
        except RedisError as exc:
            logger.warning("Idempotency abort failed: %s", exc)
        self._finished = True


async def _write_record(redis_key: str, record: dict[str, Any]) -> None:
    payload = orjson.dumps(record).decode("utf-8")
    ttl = config.IDEMPOTENCY_TTL_SECONDS
    await redis_client.set(redis_key, payload, ex=ttl)


async def _read_record(redis_key: str) -> dict[str, Any] | None:
    raw = await redis_client.get(redis_key)
    if raw is None:
        return None
    return orjson.loads(raw)


async def _try_claim_in_progress(redis_key: str, fingerprint: str) -> bool:
    record = {
        "status": _STATUS_IN_PROGRESS,
        "fingerprint": fingerprint,
    }
    payload = orjson.dumps(record).decode("utf-8")
    ttl = config.IDEMPOTENCY_TTL_SECONDS
    result = await redis_client.set(redis_key, payload, nx=True, ex=ttl)
    return bool(result)


def _replay_response(record: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=int(record["status_code"]),
        content=record["body"],
    )


async def begin_idempotency(
    *,
    tenant_id: str,
    route: str,
    idempotency_key: str | None,
    fingerprint: str,
) -> tuple[IdempotencyLease, JSONResponse | None]:
    """Claim or replay an idempotent request. Returns (lease, replay_response)."""
    if idempotency_key is None:
        return IdempotencyLease(None, fingerprint), None

    redis_key = _redis_key(
        tenant_id=tenant_id, route=route, idempotency_key=idempotency_key
    )
    lease = IdempotencyLease(redis_key, fingerprint)

    try:
        existing = await _read_record(redis_key)
        if existing is not None:
            replay = _evaluate_existing(existing, fingerprint)
            if isinstance(replay, JSONResponse):
                return lease, replay
            if replay is False:
                raise _mismatch_error()

        if await _try_claim_in_progress(redis_key, fingerprint):
            return lease, None

        deadline = time.monotonic() + config.IDEMPOTENCY_WAIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(config.IDEMPOTENCY_POLL_INTERVAL_SECONDS)
            existing = await _read_record(redis_key)
            if existing is None:
                if await _try_claim_in_progress(redis_key, fingerprint):
                    return lease, None
                continue
            replay = _evaluate_existing(existing, fingerprint)
            if isinstance(replay, JSONResponse):
                return lease, replay
            if replay is False:
                raise _mismatch_error()

        raise _in_progress_error()
    except RedisError as exc:
        logger.warning("Idempotency Redis error: %s", exc)
        raise _store_unavailable_error() from exc


def _evaluate_existing(
    record: dict[str, Any], fingerprint: str
) -> JSONResponse | bool | None:
    """Return replay response, False on mismatch, or None to keep waiting."""
    stored_fp = record.get("fingerprint")
    if stored_fp != fingerprint:
        return False
    if record.get("status") == _STATUS_COMPLETED:
        return _replay_response(record)
    return None


async def complete_idempotent_http_exception(
    lease: IdempotencyLease, exc: HTTPException
) -> None:
    if isinstance(exc.detail, dict):
        body: Any = {"detail": exc.detail}
    else:
        body = {"detail": exc.detail}
    await lease.complete(exc.status_code, body)
