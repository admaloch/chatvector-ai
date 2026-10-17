-- Staged document replacement: documents.status_detail.
--
-- Re-ingesting content for a stable document_id (POST /upload with
-- replace_document_id) must keep the previously completed chunks retrievable
-- until the new content is ready. Retrieval requires documents.status =
-- 'completed', so replacement progress cannot be expressed through `status`.
-- `status_detail` is a second, retrieval-neutral field:
--
--   NULL                    no replacement activity
--   replacement_in_progress a replacement job is queued or running; `status`
--                           (and the chunks it gates) is left untouched
--   replacement_failed      the last replacement failed; the prior content
--                           (if any) is unchanged and `error` describes why
--
-- A successful replacement resets status_detail to NULL in the same
-- transaction that swaps the chunk set. No document_chunks changes are made:
-- this migration deliberately adds no version table and no version FK.
--
-- ────────────────────────────────────────────────────────────────────────────
-- INDEX STRATEGY
-- ────────────────────────────────────────────────────────────────────────────
-- status_detail is filtered on at startup reconciliation
-- (WHERE status_detail = 'replacement_in_progress') and by the atomic
-- replacement-start guard. Most rows are NULL, so a partial index keeps it
-- tiny and only covers rows with replacement activity.
--
-- ────────────────────────────────────────────────────────────────────────────
-- EXISTING INSTALLATIONS
-- ────────────────────────────────────────────────────────────────────────────
-- Docker volumes initialised before this file existed do not re-run it.
-- Apply manually:
--
--   docker compose exec db sh -c \
--     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
--        -f /docker-entrypoint-initdb.d/014_document_replacement.sql'
--
-- ────────────────────────────────────────────────────────────────────────────
-- ROLLBACK
-- ────────────────────────────────────────────────────────────────────────────
--   DROP INDEX IF EXISTS idx_documents_status_detail;
--   ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_status_detail;
--   ALTER TABLE documents DROP COLUMN IF EXISTS status_detail;
--   DELETE FROM public.schema_migrations
--    WHERE filename = '014_document_replacement.sql';

BEGIN;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS status_detail TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'ck_documents_status_detail'
           AND conrelid = 'public.documents'::regclass
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT ck_documents_status_detail
            CHECK (
                status_detail IS NULL
                OR status_detail IN ('replacement_in_progress', 'replacement_failed')
            );
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_documents_status_detail
    ON documents (status_detail)
    WHERE status_detail IS NOT NULL;

INSERT INTO public.schema_migrations (filename)
VALUES ('014_document_replacement.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
