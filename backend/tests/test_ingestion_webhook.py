"""Tests for ingestion completion webhooks."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services import ingestion_webhook
from services.ingestion_webhook import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    SIGNATURE_HEADER,
    build_terminal_payload,
    canonical_json_bytes,
    compute_signature,
    deliver_ingestion_webhook_event,
    notify_ingestion_terminal,
)

SIGNATURE_VECTOR_BODY = (
    b'{"document_id":"doc-123","event_id":"550e8400-e29b-41d4-a716-446655440000",'
    b'"event_type":"ingestion.completed","status":"completed","tenant_id":"tenant-a",'
    b'"timestamps":{"completed_at":"2026-01-15T12:00:00Z","enqueued_at":"2026-01-15T11:59:50Z",'
    b'"started_at":"2026-01-15T11:59:55Z"}}'
)
SIGNATURE_VECTOR_EXPECTED = (
    "sha256=dd436d2019c2026e493fa1886dd1cb97e46df8e7fe6e343cf8bf59ed14903dcf"
)


def test_signature_vector():
    assert (
        compute_signature("test_webhook_secret", SIGNATURE_VECTOR_BODY)
        == SIGNATURE_VECTOR_EXPECTED
    )


@pytest.fixture
def webhook_env(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setenv("WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("WEBHOOK_TIMEOUT_SECONDS", "5")
    import importlib

    import core.config as core_config

    importlib.reload(core_config)
    importlib.reload(ingestion_webhook)
    yield
    monkeypatch.delenv("WEBHOOK_ENABLED", raising=False)
    importlib.reload(core_config)
    importlib.reload(ingestion_webhook)


def test_webhook_enabled_requires_url_and_secret(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    import importlib

    with pytest.raises(ValueError, match="WEBHOOK_URL"):
        importlib.reload(__import__("core.config", fromlist=["config"]))


@pytest.mark.asyncio
async def test_notify_completed_enqueues_signed_delivery(webhook_env):
    payload = build_terminal_payload(
        event_type=EVENT_COMPLETED,
        document_id="doc-1",
        tenant_id="tenant-1",
        status="completed",
        job_metadata={"completed_at": "2026-01-15T12:00:00Z"},
        error=None,
        event_id="evt-1",
    )
    captured: dict = {}

    def fake_post(url, content, headers):
        captured["url"] = url
        captured["body"] = content
        captured["headers"] = headers
        response = httpx.Response(200, request=httpx.Request("POST", url))
        return response

    with patch(
        "db.get_document_status",
        new=AsyncMock(
            return_value={
                "status": "completed",
                "job_metadata": {"completed_at": "2026-01-15T12:00:00Z"},
            }
        ),
    ), patch(
        "services.ingestion_webhook.schedule_ingestion_webhook_delivery",
        new=AsyncMock(side_effect=lambda p: captured.update({"payload": p})),
    ):
        await notify_ingestion_terminal("doc-1", "tenant-1", outcome="completed")

    assert captured["payload"]["event_type"] == EVENT_COMPLETED


@pytest.mark.asyncio
async def test_notify_failed_uses_ingestion_failed_event(webhook_env):
    captured: dict = {}

    with patch(
        "db.get_document_status",
        new=AsyncMock(
            return_value={
                "status": "failed",
                "error": {"code": "no_text_extracted", "message": "No text", "stage": "extracting"},
                "job_metadata": {"completed_at": "2026-01-15T12:00:00Z"},
            }
        ),
    ), patch(
        "services.ingestion_webhook.schedule_ingestion_webhook_delivery",
        new=AsyncMock(side_effect=lambda p: captured.update({"payload": p})),
    ):
        await notify_ingestion_terminal("doc-1", "tenant-1", outcome="failed")

    assert captured["payload"]["event_type"] == EVENT_FAILED
    assert captured["payload"]["error_summary"]["code"] == "no_text_extracted"


@pytest.mark.asyncio
async def test_webhook_disabled_skips_notify(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ENABLED", "false")
    import importlib

    import core.config as core_config

    importlib.reload(core_config)
    importlib.reload(ingestion_webhook)

    schedule_mock = AsyncMock()
    status_mock = AsyncMock()
    with patch("services.ingestion_webhook.schedule_ingestion_webhook_delivery", schedule_mock), patch(
        "db.get_document_status", status_mock
    ):
        await notify_ingestion_terminal("doc-1", "tenant-1", outcome="completed")

    schedule_mock.assert_not_awaited()
    status_mock.assert_not_awaited()


def test_deliver_posts_signed_body_without_spill(webhook_env, monkeypatch):
    payload = json.loads(SIGNATURE_VECTOR_BODY.decode("utf-8"))
    posted: dict = {}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            posted["url"] = url
            posted["content"] = content
            posted["headers"] = headers
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    deliver_ingestion_webhook_event(payload, attempt=1)

    assert posted["content"] == canonical_json_bytes(payload)
    assert posted["headers"][SIGNATURE_HEADER] == compute_signature(
        "test_webhook_secret", posted["content"]
    )


def test_deliver_retries_via_rq_enqueue(webhook_env, monkeypatch):
    payload = {
        "event_id": "evt-retry",
        "event_type": EVENT_FAILED,
        "document_id": "doc-1",
        "tenant_id": "t1",
        "status": "failed",
        "timestamps": {},
    }
    enqueue_mock = MagicMock()
    monkeypatch.setattr(
        ingestion_webhook, "_sync_enqueue_webhook_delivery", enqueue_mock
    )

    def boom(*args, **kwargs):
        raise httpx.ConnectError("down", request=httpx.Request("POST", "https://x.test"))

    monkeypatch.setattr(ingestion_webhook, "_post_webhook", boom)
    monkeypatch.setattr(ingestion_webhook.time, "sleep", lambda _s: None)

    deliver_ingestion_webhook_event(payload, attempt=1)

    enqueue_mock.assert_called_once_with(payload, attempt=2)


@pytest.mark.asyncio
async def test_pipeline_skips_webhook_when_disabled():
    from services.ingestion_pipeline import IngestionPipeline

    pipeline = IngestionPipeline()
    notify_mock = AsyncMock()
    with patch("db.update_document_status", new=AsyncMock()), patch(
        "core.config.config.WEBHOOK_ENABLED", False
    ), patch("services.ingestion_webhook.notify_ingestion_terminal", notify_mock):
        await pipeline._update_status(
            doc_id="doc-1",
            status="completed",
            tenant_id="tenant-1",
        )
    notify_mock.assert_not_awaited()


def test_deployment_docs_mention_webhook_ops():
    from tests.repo_layout import repo_file

    deployment = repo_file("DEPLOYMENT.md").read_text()
    docs_page = repo_file("documentation", "content", "deployment.md").read_text()
    for text in (deployment, docs_page):
        assert "WEBHOOK_ENABLED" in text
        assert "WEBHOOK_SECRET" in text
        assert "HMAC" in text or "hmac" in text.lower()
        assert "WEBHOOK_MAX_ATTEMPTS" in text
        assert "event_id" in text
        assert "DEVELOPMENT.md" in text
    assert "Ingestion completion webhooks" in deployment or "Ingestion webhooks" in deployment
