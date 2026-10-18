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
--
-- F29 (issue 568): document_id and session_id stay unconstrained on purpose.
--
-- Current deletion behavior:
-- - Document delete removes the documents row, its chunks, and session
--   bindings. retrieval_feedback is left in place, still holding document_id.
-- - Session delete removes chat_messages (and session_documents via
--   ON DELETE CASCADE). message_id is ON DELETE SET NULL, so the feedback
--   row survives with that link cleared. session_id is NOT NULL and is kept
--   even after the sessions row is gone.
--
-- message_id is the only explicit contract: feedback outlives the message,
-- and the id is cleared because a foreign key cannot point at a deleted row.
--
-- A foreign key on document_id or session_id would change that contract:
-- - ON DELETE CASCADE would delete feedback when the source disappears.
-- - ON DELETE SET NULL would drop the retained id (and session_id would have
--   to become nullable).
-- - ON DELETE RESTRICT would block document or session deletion while
--   feedback exists.
-- None of those matches "keep the feedback row and its source ids," and
-- there is no separate product rule that picks one. Rows may already store
-- ids for documents or sessions that are gone, so adding a foreign key would
-- also fail until those values were rewritten. Do not add either constraint
-- until a deletion policy is chosen.

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
