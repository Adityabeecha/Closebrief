-- v3.1: demo isolation for context documents (library, retrieval, conflicts).
-- BOOLEAN to match datasets.is_demo so the shared demo-scope predicate applies.
ALTER TABLE context_documents ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT false;
