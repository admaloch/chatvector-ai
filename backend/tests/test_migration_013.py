"""Tests for migration 013: documents.metadata JSONB column."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "db" / "init" / "013_document_metadata.sql"
)
MIGRATION_FILENAME = "013_document_metadata.sql"


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


def test_migration_documents_index_strategy():
    text = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "INDEX STRATEGY" in text
    assert "jsonb_path_ops" in text


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


def test_013_is_idempotent_and_defaults_metadata_to_empty_object(postgres_connection):
    psycopg = pytest.importorskip("psycopg")
    conn = postgres_connection
    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")
    tenant_id = f"test-013-{uuid4().hex[:8]}"
    doc_id = str(uuid4())

    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations')")
            if cur.fetchone()[0] is None:
                pytest.skip("schema_migrations is not installed")

            # Re-running must not fail or duplicate the ledger row.
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
                "AND column_name = 'metadata'"
            )
            assert cur.fetchone() == ("NO", "jsonb")

            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'documents' AND indexname = 'idx_documents_metadata_gin'"
            )
            indexdef = cur.fetchone()[0]
            assert "gin" in indexdef.lower()
            assert "jsonb_path_ops" in indexdef

            cur.execute(
                "INSERT INTO tenants (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (tenant_id, tenant_id),
            )
            cur.execute(
                "INSERT INTO documents (id, file_name, tenant_id, status) "
                "VALUES (%s, 'meta-013.txt', %s, 'completed') RETURNING metadata",
                (doc_id, tenant_id),
            )
            assert cur.fetchone()[0] == {}

            # Array (and other non-object) metadata is rejected by the DB.
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "UPDATE documents SET metadata = '[]'::jsonb WHERE id = %s",
                    (doc_id,),
                )
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
        conn.autocommit = True
