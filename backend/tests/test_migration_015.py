"""Tests for migration 015: documents.job_metadata (ingestion observability)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "db" / "init" / "015_document_job_metadata.sql"
)
MIGRATION_FILENAME = "015_document_job_metadata.sql"


def _executable_sql() -> str:
    return "\n".join(
        line
        for line in MIGRATION_PATH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ).strip()


def test_migration_file_exists():
    assert MIGRATION_PATH.is_file()


def test_migration_is_atomic_and_self_records():
    sql = _executable_sql()
    assert re.match(r"BEGIN\s*;", sql, re.IGNORECASE)
    assert MIGRATION_FILENAME in sql
    assert re.search(
        r"ON\s+CONFLICT\s*\(\s*filename\s*\)\s+DO\s+NOTHING\s*;"
        r"\s*COMMIT\s*;\s*$",
        sql,
        re.IGNORECASE,
    )


def test_orm_document_declares_job_metadata():
    from core.models import Document

    assert "job_metadata" in Document.__table__.columns
    assert Document.__table__.columns["job_metadata"].nullable is False


@pytest.fixture
def postgres_connection():
    psycopg = pytest.importorskip("psycopg")
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
    )
    dsn = re.sub(r"^postgresql\+(?:asyncpg|psycopg)://", "postgresql://", database_url)

    try:
        connection = psycopg.connect(dsn, connect_timeout=2)
    except psycopg.OperationalError as exc:
        if os.getenv("CI", "").strip().lower() in {"1", "true", "yes"}:
            pytest.fail(f"PostgreSQL is required for migration tests in CI: {exc}")
        pytest.skip(f"PostgreSQL is unavailable: {exc}")

    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_015_is_idempotent_and_adds_job_metadata(postgres_connection):
    conn = postgres_connection
    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")

    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations')")
            if cur.fetchone()[0] is None:
                pytest.skip("schema_migrations is not installed")

            cur.execute(migration_sql)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(migration_sql)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM public.schema_migrations WHERE filename = %s",
                (MIGRATION_FILENAME,),
            )
            assert cur.fetchone()[0] == 1

            cur.execute(
                "SELECT is_nullable, data_type, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'documents' "
                "AND column_name = 'job_metadata'"
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "NO"
            assert row[1] == "jsonb"
            assert row[2] is not None and "'{}'::jsonb" in row[2]

            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'documents' AND indexname = 'idx_documents_job_metadata_gin'"
            )
            index_row = cur.fetchone()
            assert index_row is not None
            assert "jsonb_path_ops" in index_row[0]
    finally:
        conn.rollback()
