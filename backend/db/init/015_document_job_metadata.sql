-- Ingestion job observability: documents.job_metadata (JSONB).
--
-- Stores cross-host timing, attempt counts, and failure classification for
-- document ingestion. Legacy rows backfill to '{}'; API omits `job` when empty.
--
-- EXISTING INSTALLATIONS
-- Docker volumes initialised before this file existed do not re-run it.
-- Apply manually:
--   psql "$DATABASE_URL" -f backend/db/init/015_document_job_metadata.sql
--
-- Rollback (destructive — drops persisted job history):
--   ALTER TABLE documents DROP COLUMN IF EXISTS job_metadata;
--   DELETE FROM public.schema_migrations
--    WHERE filename = '015_document_job_metadata.sql';

BEGIN;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS job_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_documents_job_metadata_gin
    ON documents USING GIN (job_metadata jsonb_path_ops);

INSERT INTO public.schema_migrations (filename)
VALUES ('015_document_job_metadata.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
