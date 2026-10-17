"""POST /upload replace_document_id and the status/delete routes around it."""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from core.auth import AuthContext
from db.base import REPLACEMENT_FAILED, REPLACEMENT_IN_PROGRESS
from middleware.rate_limit import limiter
from request_utils import make_test_request
from routes.documents import (
    delete_document,
    get_document_status,
    get_document_status_stream,
)
from routes.upload import upload
from services.queue_service import QueueFull

TENANT = "tenant-123"
DOC_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """These tests call the upload route directly many times (20/hour limit)."""
    limiter.reset()
    yield
    limiter.reset()


def _file() -> AsyncMock:
    mock_file = AsyncMock(spec=UploadFile)
    mock_file.filename = "v2.pdf"
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=b"fake-pdf-bytes")
    return mock_file


def _upload_patches(*, begin="started", enqueue=None, status="completed"):
    """Patch the collaborators of POST /upload; returns a dict of mocks."""
    mocks = {
        "create_document": AsyncMock(return_value="new-doc"),
        "update_status": AsyncMock(),
        "begin": AsyncMock(return_value=begin),
        "set_detail": AsyncMock(),
        "get_status": AsyncMock(return_value={"status": status}),
        "enqueue": enqueue or AsyncMock(return_value=3),
    }
    patchers = [
        patch("routes.upload.ingestion_pipeline.validate_file", return_value=None),
        patch("routes.upload.db.create_document", new=mocks["create_document"]),
        patch("routes.upload.db.update_document_status", new=mocks["update_status"]),
        patch("routes.upload.db.begin_document_replacement", new=mocks["begin"]),
        patch("routes.upload.db.set_document_status_detail", new=mocks["set_detail"]),
        patch("routes.upload.db.get_document_status", new=mocks["get_status"]),
        patch("routes.upload.ingestion_queue.enqueue", new=mocks["enqueue"]),
    ]
    return mocks, patchers


class _Patched:
    def __init__(self, **kwargs):
        self.mocks, self._patchers = _upload_patches(**kwargs)

    def __enter__(self):
        for patcher in self._patchers:
            patcher.start()
        return self.mocks

    def __exit__(self, *exc):
        for patcher in reversed(self._patchers):
            patcher.stop()
        return False


async def _upload(request=None, **kwargs):
    return await upload(
        request or make_test_request("POST", "/upload"),
        _file(),
        auth=AuthContext(tenant_id=TENANT),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_upload_without_replace_creates_new_document():
    with _Patched() as mocks:
        result = await _upload()

    assert result["document_id"] == "new-doc"
    assert result["status"] == "queued"
    assert "status_detail" not in result
    mocks["create_document"].assert_awaited_once()
    mocks["begin"].assert_not_awaited()
    job = mocks["enqueue"].call_args[0][0]
    assert job.doc_id == "new-doc"
    assert job.replace is False
    assert job.metadata is None


@pytest.mark.asyncio
async def test_replace_enqueues_job_for_same_document_without_touching_status():
    with _Patched() as mocks:
        result = await _upload(replace_document_id=DOC_ID)

    assert result == {
        "message": "Accepted",
        "document_id": DOC_ID,
        "status": "completed",
        "status_detail": REPLACEMENT_IN_PROGRESS,
        "queue_position": 3,
        "status_endpoint": f"/documents/{DOC_ID}/status",
    }
    mocks["begin"].assert_awaited_once_with(DOC_ID, tenant_id=TENANT)
    mocks["create_document"].assert_not_awaited()
    # `status` must never leave `completed`: no queued/failed writes on this path.
    mocks["update_status"].assert_not_awaited()
    job = mocks["enqueue"].call_args[0][0]
    assert job.doc_id == DOC_ID
    assert job.replace is True
    assert job.file_name == "v2.pdf"
    assert job.metadata is None  # keep existing metadata


@pytest.mark.asyncio
async def test_replace_of_failed_document_reports_failed_status():
    with _Patched(status="failed"):
        result = await _upload(replace_document_id=DOC_ID)

    assert result["status"] == "failed"
    assert result["status_detail"] == REPLACEMENT_IN_PROGRESS


@pytest.mark.asyncio
async def test_replace_passes_provided_metadata_to_job():
    with _Patched() as mocks:
        await _upload(replace_document_id=DOC_ID, metadata='{"department": "legal"}')

    assert mocks["enqueue"].call_args[0][0].metadata == {"department": "legal"}


@pytest.mark.asyncio
async def test_replace_with_explicit_empty_metadata_clears_metadata():
    with _Patched() as mocks:
        await _upload(replace_document_id=DOC_ID, metadata="{}")

    assert mocks["enqueue"].call_args[0][0].metadata == {}


@pytest.mark.asyncio
async def test_replace_binds_session_when_header_present():
    request = make_test_request("POST", "/upload", headers={"X-Session-Id": "sess-1"})
    session = AsyncMock()
    session.id = "sess-1"

    with (
        _Patched(),
        patch("routes.upload.get_or_create_session", new=AsyncMock(return_value=session)),
        patch("routes.upload.register_session_document", new=AsyncMock()) as register,
    ):
        await _upload(request, replace_document_id=DOC_ID)

    register.assert_awaited_once_with("sess-1", DOC_ID, TENANT)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["not-a-uuid", "", "   ", "123", "doc-1"])
async def test_replace_rejects_invalid_uuid_before_touching_the_database(bad_id):
    with _Patched() as mocks:
        with pytest.raises(HTTPException) as exc_info:
            await _upload(replace_document_id=bad_id)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "invalid_replace_document_id"
    mocks["begin"].assert_not_awaited()
    mocks["create_document"].assert_not_awaited()
    mocks["enqueue"].assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_unknown_or_foreign_document_returns_404():
    with _Patched(begin="not_found") as mocks:
        with pytest.raises(HTTPException) as exc_info:
            await _upload(replace_document_id=DOC_ID)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "document_not_found"
    assert exc_info.value.detail["document_id"] == DOC_ID
    mocks["enqueue"].assert_not_awaited()
    mocks["set_detail"].assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_during_in_flight_replacement_returns_409():
    with _Patched(begin="in_progress") as mocks:
        with pytest.raises(HTTPException) as exc_info:
            await _upload(replace_document_id=DOC_ID)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "replacement_in_progress"
    mocks["enqueue"].assert_not_awaited()
    # The running replacement's state must not be disturbed by the rejected call.
    mocks["set_detail"].assert_not_awaited()
    mocks["update_status"].assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_of_document_still_ingesting_returns_409():
    with _Patched(begin="not_eligible") as mocks:
        with pytest.raises(HTTPException) as exc_info:
            await _upload(replace_document_id=DOC_ID)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "document_processing"
    mocks["enqueue"].assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_with_invalid_metadata_does_not_claim_document():
    with _Patched() as mocks:
        with pytest.raises(HTTPException) as exc_info:
            await _upload(replace_document_id=DOC_ID, metadata="[1, 2]")

    assert exc_info.value.status_code == 422
    mocks["begin"].assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_queue_full_records_failure_without_failing_document():
    enqueue = AsyncMock(side_effect=QueueFull("full"))
    with _Patched(enqueue=enqueue) as mocks:
        with pytest.raises(HTTPException) as exc_info:
            await _upload(replace_document_id=DOC_ID)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "queue_full"
    mocks["update_status"].assert_not_awaited()  # never marked `failed`
    mocks["set_detail"].assert_awaited_once()
    args, kwargs = mocks["set_detail"].await_args
    assert args == (DOC_ID, REPLACEMENT_FAILED)
    assert kwargs["error"]["stage"] == "queued"


@pytest.mark.asyncio
async def test_replace_unexpected_error_never_marks_document_failed():
    enqueue = AsyncMock(side_effect=RuntimeError("redis down"))
    with _Patched(enqueue=enqueue) as mocks:
        with pytest.raises(HTTPException) as exc_info:
            await _upload(replace_document_id=DOC_ID)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["document_id"] == DOC_ID
    mocks["update_status"].assert_not_awaited()
    mocks["set_detail"].assert_awaited_once()
    assert mocks["set_detail"].await_args.args == (DOC_ID, REPLACEMENT_FAILED)
    assert mocks["set_detail"].await_args.kwargs["error"]["code"] == "upload_failed"


# ── status / stream / delete ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_exposes_status_detail_while_replacement_runs():
    payload = {
        "document_id": DOC_ID,
        "status": "completed",
        "status_detail": REPLACEMENT_IN_PROGRESS,
        "chunks": {"total": 2, "processed": 2},
        "error": None,
    }
    with (
        patch("routes.documents.db.get_document_status", new=AsyncMock(return_value=payload)),
        patch("routes.documents.ingestion_queue.queue_position", return_value=2),
    ):
        result = await get_document_status(
            make_test_request("GET", f"/documents/{DOC_ID}/status"),
            uuid.UUID(DOC_ID),
            auth=AuthContext(tenant_id=TENANT),
        )

    assert result["status"] == "completed"
    assert result["status_detail"] == REPLACEMENT_IN_PROGRESS
    assert result["queue_position"] == 2
    assert "error" not in result


@pytest.mark.asyncio
async def test_status_reports_failed_replacement_without_failing_document():
    error = {"stage": "embedding", "code": "pipeline_error", "message": "boom"}
    payload = {
        "document_id": DOC_ID,
        "status": "completed",
        "status_detail": REPLACEMENT_FAILED,
        "chunks": {"total": 2, "processed": 2},
        "error": error,
    }
    with patch("routes.documents.db.get_document_status", new=AsyncMock(return_value=payload)):
        result = await get_document_status(
            make_test_request("GET", f"/documents/{DOC_ID}/status"),
            uuid.UUID(DOC_ID),
            auth=AuthContext(tenant_id=TENANT),
        )

    assert result["status"] == "completed"
    assert result["status_detail"] == REPLACEMENT_FAILED
    assert result["error"] == error


@pytest.mark.asyncio
async def test_status_omits_status_detail_when_idle():
    payload = {"document_id": DOC_ID, "status": "completed", "status_detail": None, "chunks": {}}
    with patch("routes.documents.db.get_document_status", new=AsyncMock(return_value=payload)):
        result = await get_document_status(
            make_test_request("GET", f"/documents/{DOC_ID}/status"),
            uuid.UUID(DOC_ID),
            auth=AuthContext(tenant_id=TENANT),
        )

    assert "status_detail" not in result


@pytest.mark.asyncio
async def test_status_stream_stays_open_until_replacement_resolves():
    payloads = [
        {"document_id": DOC_ID, "status": "completed", "status_detail": REPLACEMENT_IN_PROGRESS},
        {"document_id": DOC_ID, "status": "completed", "status_detail": REPLACEMENT_IN_PROGRESS},
        {"document_id": DOC_ID, "status": "completed", "status_detail": None},
    ]

    async def next_payload(doc_id, **kwargs):
        return payloads.pop(0) if payloads else None

    request = make_test_request("GET", f"/documents/{DOC_ID}/status/stream")
    request.is_disconnected = AsyncMock(return_value=False)

    with (
        patch("routes.documents.config") as mock_config,
        patch("routes.documents.db.get_document_status", new=AsyncMock(side_effect=next_payload)),
        patch("routes.documents.ingestion_queue.queue_position", return_value=None),
        patch("routes.documents.asyncio.sleep", new=AsyncMock()),
    ):
        mock_config.ENABLE_STREAMING = True
        response = await get_document_status_stream(
            request, uuid.UUID(DOC_ID), auth=AuthContext(tenant_id=TENANT)
        )
        events = [
            json.loads(chunk.replace("event: status\ndata: ", "").strip())
            async for chunk in response.body_iterator
        ]

    assert [e.get("status_detail") for e in events] == [
        REPLACEMENT_IN_PROGRESS,
        REPLACEMENT_IN_PROGRESS,
        None,
    ]


@pytest.mark.asyncio
async def test_delete_blocked_while_replacement_in_progress():
    payload = {
        "document_id": DOC_ID,
        "status": "completed",
        "status_detail": REPLACEMENT_IN_PROGRESS,
    }
    with (
        patch("routes.documents.db.get_document_status", new=AsyncMock(return_value=payload)),
        patch("routes.documents.db.delete_document", new=AsyncMock()) as mock_delete,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await delete_document(
                make_test_request("DELETE", f"/documents/{DOC_ID}"),
                uuid.UUID(DOC_ID),
                auth=AuthContext(tenant_id=TENANT),
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "document_replacing"
    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_allowed_after_replacement_failed():
    payload = {
        "document_id": DOC_ID,
        "status": "completed",
        "status_detail": REPLACEMENT_FAILED,
    }
    with (
        patch("routes.documents.db.get_document_status", new=AsyncMock(return_value=payload)),
        patch("routes.documents.ingestion_queue.queue_position", return_value=None),
        patch("routes.documents.db.delete_document", new=AsyncMock()) as mock_delete,
    ):
        result = await delete_document(
            make_test_request("DELETE", f"/documents/{DOC_ID}"),
            uuid.UUID(DOC_ID),
            auth=AuthContext(tenant_id=TENANT),
        )

    assert result.status_code == 204
    mock_delete.assert_awaited_once()
