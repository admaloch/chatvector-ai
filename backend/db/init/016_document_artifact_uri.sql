-- Durable ingestion artifacts: documents.artifact_uri.
--
-- Optional URI (file:// or s3://) so workers can fetch upload bytes without
-- sharing the API host's local disk. Cleared after terminal ingest cleanup.
--
-- EXISTING INSTALLATIONS
-- Docker volumes initialised before this file existed do not re-run it.
-- Apply manually:
--   psql "$DATABASE_URL" -f backend/db/init/016_document_artifact_uri.sql
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_documents_artifact_uri;
--   ALTER TABLE documents DROP COLUMN IF EXISTS artifact_uri;
--   DELETE FROM public.schema_migrations
--    WHERE filename = '016_document_artifact_uri.sql';

BEGIN;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS artifact_uri TEXT;

CREATE INDEX IF NOT EXISTS idx_documents_artifact_uri
    ON documents (artifact_uri)
    WHERE artifact_uri IS NOT NULL;

INSERT INTO public.schema_migrations (filename)
VALUES ('016_document_artifact_uri.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
