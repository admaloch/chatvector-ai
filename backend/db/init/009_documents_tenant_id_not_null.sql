-- Enforce documents.tenant_id NOT NULL and align tenant FK delete behavior.
--
-- ────────────────────────────────────────────────────────────────────────────
-- EXISTING INSTALLATIONS
-- ────────────────────────────────────────────────────────────────────────────
-- If any documents still have tenant_id IS NULL, this migration skips the
-- NOT NULL constraint and leaves the existing FK unchanged. Backfill first:
--
--   -- Option A: assign orphaned documents to a known tenant
--   UPDATE documents
--      SET tenant_id = '<your-tenant-id>'
--    WHERE tenant_id IS NULL;
--
--   -- Option B: delete orphaned documents (irreversible)
--   DELETE FROM documents WHERE tenant_id IS NULL;
--
-- Then re-run:
--
--   docker compose exec db psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--       -f /docker-entrypoint-initdb.d/009_documents_tenant_id_not_null.sql
--
-- Once NOT NULL is enforced, tenant deletion cascades to owned documents.
-- The previous ON DELETE SET NULL behavior from 006 is incompatible with
-- NOT NULL and is replaced by ON DELETE CASCADE.
--
-- ────────────────────────────────────────────────────────────────────────────
-- ROLLBACK
-- ────────────────────────────────────────────────────────────────────────────
--   ALTER TABLE documents DROP CONSTRAINT IF EXISTS fk_documents_tenant_id;
--   ALTER TABLE documents ALTER COLUMN tenant_id DROP NOT NULL;
--   ALTER TABLE documents
--       ADD CONSTRAINT fk_documents_tenant_id
--       FOREIGN KEY (tenant_id) REFERENCES tenants(id)
--       ON DELETE SET NULL;

BEGIN;

DO $$
DECLARE
    null_count BIGINT;
BEGIN
    SELECT COUNT(*) INTO null_count FROM documents WHERE tenant_id IS NULL;

    IF null_count > 0 THEN
        RAISE NOTICE
            'Skipping documents.tenant_id NOT NULL: % row(s) still have NULL tenant_id. Backfill and re-run 009.',
            null_count;
        RETURN;
    END IF;

    ALTER TABLE documents ALTER COLUMN tenant_id SET NOT NULL;

    IF EXISTS (
        SELECT 1
          FROM information_schema.table_constraints
         WHERE constraint_name = 'fk_documents_tenant_id'
           AND table_name = 'documents'
    ) THEN
        ALTER TABLE documents DROP CONSTRAINT fk_documents_tenant_id;
    END IF;

    ALTER TABLE documents
        ADD CONSTRAINT fk_documents_tenant_id
        FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        ON DELETE CASCADE;
END;
$$;

INSERT INTO public.schema_migrations (filename)
VALUES ('009_documents_tenant_id_not_null.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
