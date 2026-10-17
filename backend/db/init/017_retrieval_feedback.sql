-- Structured retrieval / answer feedback linked to sessions, messages, and citations.
--
-- EXISTING INSTALLATIONS
-- Apply manually when upgrading:
--   psql "$DATABASE_URL" -f backend/db/init/017_retrieval_feedback.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS retrieval_feedback;
--   DELETE FROM public.schema_migrations
--    WHERE filename = '017_retrieval_feedback.sql';

BEGIN;

CREATE TABLE IF NOT EXISTS retrieval_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255) NOT NULL,
    feedback_type VARCHAR(50) NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT,
    message_id UUID REFERENCES chat_messages(id) ON DELETE SET NULL,
    document_id UUID,
    chunk_index INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT chk_retrieval_feedback_rating CHECK (rating IN (-1, 1))
);

CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_tenant_id
    ON retrieval_feedback (tenant_id);

CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_session_id
    ON retrieval_feedback (session_id);

CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_tenant_created
    ON retrieval_feedback (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_message_id
    ON retrieval_feedback (message_id)
    WHERE message_id IS NOT NULL;

INSERT INTO public.schema_migrations (filename)
VALUES ('017_retrieval_feedback.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
