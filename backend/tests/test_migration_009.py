"""Tests for migration 009: documents.tenant_id NOT NULL enforcement."""

from pathlib import Path
import re

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "db" / "init" / "009_documents_tenant_id_not_null.sql"
)


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
    assert "009_documents_tenant_id_not_null.sql" in sql
    assert re.search(
        r"ON\s+CONFLICT\s*\(\s*filename\s*\)\s+DO\s+NOTHING\s*;"
        r"\s*COMMIT\s*;\s*$",
        sql,
        re.IGNORECASE,
    )


def test_migration_skips_not_null_when_null_rows_exist():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "tenant_id IS NULL" in sql
    assert "Skipping documents.tenant_id NOT NULL" in sql


def test_migration_replaces_set_null_fk_with_cascade():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ON DELETE CASCADE" in sql
    assert "ON DELETE SET NULL" in sql
