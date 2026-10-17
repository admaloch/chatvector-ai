"""Tenant registry cache vs DB-authoritative bypass for multi-instance."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import pytest

from services.tenant_registry import (
    clear_tenant_registry,
    get_tenant_document_ids,
    register_tenant_document,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_tenant_registry()
    yield
    clear_tenant_registry()


@pytest.mark.asyncio
async def test_registry_bypass_returns_all_tenant_docs(monkeypatch):
    """Incomplete process-local cache must not hide DB documents when disabled."""
    import services.tenant_registry as registry

    register_tenant_document("tenant-a", "only-cached")
    registry._TENANT_REGISTRY_LOADED.add("tenant-a")
    monkeypatch.setattr("core.config.config.TENANT_REGISTRY_DISABLED", True)

    db_ids = ["doc-1", "doc-2", "doc-3"]
    with patch("db.list_tenant_documents", new=AsyncMock(return_value=db_ids)):
        ids = await get_tenant_document_ids("tenant-a")

    assert ids == sorted(db_ids)
    assert "only-cached" not in ids


@pytest.mark.asyncio
async def test_registry_cache_used_when_enabled_and_loaded(monkeypatch):
    import services.tenant_registry as registry

    register_tenant_document("tenant-a", "cached-only")
    registry._TENANT_REGISTRY_LOADED.add("tenant-a")
    monkeypatch.setattr("core.config.config.TENANT_REGISTRY_DISABLED", False)

    with patch("db.list_tenant_documents", new=AsyncMock(return_value=["doc-db"])) as list_db:
        ids = await get_tenant_document_ids("tenant-a")

    assert ids == ["cached-only"]
    list_db.assert_not_awaited()


def _reload_multi_instance_config(monkeypatch, *, registry_env: str | None):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MULTI_API_INSTANCE", "true")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("ARTIFACT_STORAGE", "s3")
    monkeypatch.setenv("ARTIFACT_S3_BUCKET", "chatvector-artifacts")
    if registry_env is None:
        monkeypatch.delenv("TENANT_REGISTRY_DISABLED", raising=False)
    else:
        monkeypatch.setenv("TENANT_REGISTRY_DISABLED", registry_env)
    with patch("dotenv.load_dotenv"):
        import core.config

        importlib.reload(core.config)
        return core.config


def _restore_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("MULTI_API_INSTANCE", raising=False)
    monkeypatch.delenv("QUEUE_BACKEND", raising=False)
    monkeypatch.delenv("ARTIFACT_STORAGE", raising=False)
    monkeypatch.delenv("ARTIFACT_S3_BUCKET", raising=False)
    monkeypatch.delenv("TENANT_REGISTRY_DISABLED", raising=False)
    import core.config

    importlib.reload(core.config)


def test_tenant_registry_disabled_defaults_on_for_multi_instance(monkeypatch):
    try:
        core_config = _reload_multi_instance_config(monkeypatch, registry_env=None)
        assert core_config.config.MULTI_API_INSTANCE is True
        assert core_config.config.TENANT_REGISTRY_DISABLED is True
    finally:
        _restore_config(monkeypatch)


def test_tenant_registry_disabled_empty_env_keeps_multi_instance_default(monkeypatch):
    try:
        core_config = _reload_multi_instance_config(monkeypatch, registry_env="")
        assert core_config.config.TENANT_REGISTRY_DISABLED is True
    finally:
        _restore_config(monkeypatch)
