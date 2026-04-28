"""
Tests for the database factory.
Ensures the right service is returned based on environment.
"""
import importlib

import pytest
from unittest.mock import patch

import core.config
import db as db_module


@pytest.fixture(autouse=True)
def reset_db_singleton():
    import db as db_module

    db_module.db_service = None
    yield
    db_module.db_service = None
    # Tests that reload core.config leave a Settings object tied to monkeypatched APP_ENV.
    # Reload from the process environment after monkeypatch restores os.environ.
    import importlib

    import core.config as core_config_module

    importlib.reload(core_config_module)
    importlib.reload(db_module)


def test_factory_returns_sqlalchemy_in_dev(monkeypatch, reset_db_singleton):
    """Should return SQLAlchemyService when APP_ENV=development."""
    pytest.importorskip("pgvector")
    monkeypatch.setenv("APP_ENV", "development")
    importlib.reload(core.config)
    importlib.reload(db_module)
    service = db_module.get_db_service()
    from db.sqlalchemy_service import SQLAlchemyService

    assert isinstance(service, SQLAlchemyService)


def test_factory_returns_sqlalchemy_in_test_when_database_url(monkeypatch, reset_db_singleton):
    """pytest uses APP_ENV=test; match CI by using SQLAlchemy when DATABASE_URL is set."""
    pytest.importorskip("pgvector")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
    )
    importlib.reload(core.config)
    importlib.reload(db_module)
    service = db_module.get_db_service()
    from db.sqlalchemy_service import SQLAlchemyService

    assert isinstance(service, SQLAlchemyService)


def test_factory_returns_supabase_in_prod(monkeypatch, reset_db_singleton):
    """Should return SupabaseService when APP_ENV=production."""
    monkeypatch.setenv("APP_ENV", "production")
    importlib.reload(core.config)
    importlib.reload(db_module)
    service = db_module.get_db_service()
    from db.supabase_service import SupabaseService

    assert isinstance(service, SupabaseService)


@pytest.mark.asyncio
async def test_factory_caches_service(reset_db_singleton):
    """Should return same instance on subsequent calls."""
    pytest.importorskip("pgvector")
    with patch("core.config.config.APP_ENV", "development"):
        service1 = db_module.get_db_service()
        service2 = db_module.get_db_service()
        assert service1 is service2
