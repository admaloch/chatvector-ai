"""Tests for migration 016: documents.artifact_uri (durable ingest artifacts)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "db" / "init" / "016_document_artifact_uri.sql"
)
MIGRATION_FILENAME = "016_document_artifact_uri.sql"


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


def test_migration_does_not_touch_document_chunks():
    sql = _executable_sql().lower()
    assert "document_chunks" not in sql


def test_orm_document_declares_artifact_uri():
    from core.models import Document

    assert "artifact_uri" in Document.__table__.columns
    assert Document.__table__.columns["artifact_uri"].nullable is True


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


def test_016_is_idempotent_and_adds_artifact_uri(postgres_connection):
    conn = postgres_connection
    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")
    tenant_id = f"test-016-{uuid4().hex[:8]}"
    doc_id = str(uuid4())

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
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'documents' "
                "AND column_name = 'artifact_uri'"
            )
            assert cur.fetchone() == ("YES", "text")

            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'documents' AND indexname = 'idx_documents_artifact_uri'"
            )
            indexdef = cur.fetchone()[0]
            assert "artifact_uri IS NOT NULL" in indexdef

            cur.execute(
                "INSERT INTO tenants (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (tenant_id, tenant_id),
            )
            cur.execute(
                "INSERT INTO documents (id, file_name, tenant_id, status) "
                "VALUES (%s, 'artifact-016.txt', %s, 'queued') RETURNING artifact_uri",
                (doc_id, tenant_id),
            )
            assert cur.fetchone()[0] is None

            cur.execute(
                "UPDATE documents SET artifact_uri = %s WHERE id = %s",
                ("s3://bucket/chatvector/artifacts/" + doc_id, doc_id),
            )
            cur.execute(
                "SELECT artifact_uri FROM documents WHERE id = %s",
                (doc_id,),
            )
            assert cur.fetchone()[0].startswith("s3://")
        conn.rollback()
    finally:
        conn.rollback()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
                cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
            conn.commit()
        except Exception:
            conn.rollback()
