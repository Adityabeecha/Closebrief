-- A context note belongs to one dataset; NULL keeps it workspace-wide (older notes).
ALTER TABLE context_documents ADD COLUMN IF NOT EXISTS dataset_id BIGINT;
CREATE INDEX IF NOT EXISTS context_documents_dataset_idx ON context_documents (dataset_id);
