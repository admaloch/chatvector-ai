"""Tests for migration 017: retrieval_feedback table."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "db" / "init" / "017_retrieval_feedback.sql"
)
MIGRATION_FILENAME = "017_retrieval_feedback.sql"


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


def test_orm_retrieval_feedback_model():
    from core.models import RetrievalFeedback

    table = RetrievalFeedback.__table__
    assert table.name == "retrieval_feedback"
    assert "feedback_type" in table.columns
    assert "rating" in table.columns
    assert list(table.c.document_id.foreign_keys) == []
    assert list(table.c.session_id.foreign_keys) == []
    assert table.c.message_id.foreign_keys


def test_feedback_source_ids_stay_unconstrained_until_deletion_policy():
    """F29 deferred: document_id and session_id are not foreign keys.

    Feedback currently survives document and session deletion and keeps those
    ids. message_id already uses ON DELETE SET NULL. CASCADE, SET NULL, and
    RESTRICT would each change that behavior, and dangling ids would make a
    new foreign key unsafe to apply. The tradeoff is recorded in migration 017.
    """
    raw = MIGRATION_PATH.read_text(encoding="utf-8")
    sql = _executable_sql()
    assert "F29" in raw
    assert "document_id and session_id stay unconstrained" in raw
    assert re.search(
        r"message_id\s+UUID\s+REFERENCES\s+chat_messages\(id\)\s+ON\s+DELETE\s+SET\s+NULL",
        sql,
        re.IGNORECASE,
    )
    assert re.search(r"session_id\s+VARCHAR\(255\)\s+NOT\s+NULL\s*,", sql, re.IGNORECASE)
    assert re.search(r"document_id\s+UUID\s*,", sql, re.IGNORECASE)


@pytest.fixture
def postgres_connection():
    psycopg = pytest.importorskip("psycopg")
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
    )
    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = psycopg.connect(sync_url)
    yield conn
    conn.close()


def test_migration_applies_idempotently(postgres_connection):
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    with postgres_connection.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'retrieval_feedback'"
        )
        assert cur.fetchone() is not None
        cur.execute(sql)
        cur.execute(
            "SELECT COUNT(*) FROM public.schema_migrations WHERE filename = %s",
            (MIGRATION_FILENAME,),
        )
        assert cur.fetchone()[0] == 1

    postgres_connection.rollback()

    with postgres_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO retrieval_feedback (
                id, tenant_id, session_id, feedback_type, rating
            ) VALUES (%s, %s, %s, 'answer', 1)
            """,
            (str(uuid4()), f"tenant-{uuid4()}", f"session-{uuid4()}"),
        )
    postgres_connection.rollback()
