-- Add client-provided scalar metadata to documents.
--
-- documents.metadata is a JSONB object (never an array or scalar) holding
-- flat key/value pairs supplied at upload. v1 values are string, number, or
-- boolean only; the API layer enforces that (and size bounds) before insert.
-- Existing rows backfill to '{}'.
--
-- ────────────────────────────────────────────────────────────────────────────
-- INDEX STRATEGY
-- ────────────────────────────────────────────────────────────────────────────
-- Planned query path (follow-up issue, not implemented here): equality filters
-- on one or more metadata keys, always scoped to a tenant, e.g.
--
--   WHERE tenant_id = :tenant_id
--     AND metadata @> '{"department": "legal", "year": 2026}'::jsonb
--
-- * tenant_id already has its own index (see ORM / 006); it narrows the row
--   set first, so a composite (tenant_id, metadata) index is not needed.
-- * A GIN index with jsonb_path_ops supports the @> containment operator,
--   which is how multi-key equality filters are expressed. It is smaller and
--   faster than the default jsonb_ops class and covers every v1 value type
--   (string, number, boolean).
-- * Range, key-existence (?), contains-text, and full-text metadata queries
--   are out of scope for v1 and are NOT served by this index. Add targeted
--   expression indexes (e.g. ((metadata ->> 'key'))) if a hot key needs them.
--
-- ────────────────────────────────────────────────────────────────────────────
-- EXISTING INSTALLATIONS
-- ────────────────────────────────────────────────────────────────────────────
-- Docker volumes initialised before this file existed do not re-run it.
-- Apply manually:
--
--   docker compose exec db sh -c \
--     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
--        -f /docker-entrypoint-initdb.d/013_document_metadata.sql'
--
-- ────────────────────────────────────────────────────────────────────────────
-- ROLLBACK
-- ────────────────────────────────────────────────────────────────────────────
--   DROP INDEX IF EXISTS idx_documents_metadata_gin;
--   ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_metadata_object;
--   ALTER TABLE documents DROP COLUMN IF EXISTS metadata;
--   DELETE FROM public.schema_migrations
--    WHERE filename = '013_document_metadata.sql';

BEGIN;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'ck_documents_metadata_object'
           AND conrelid = 'public.documents'::regclass
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT ck_documents_metadata_object
            CHECK (jsonb_typeof(metadata) = 'object');
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_documents_metadata_gin
    ON documents USING GIN (metadata jsonb_path_ops);

INSERT INTO public.schema_migrations (filename)
VALUES ('013_document_metadata.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
