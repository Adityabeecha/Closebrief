-- v1.2 Phase 3: user attribution on the audit-bearing tables.

ALTER TABLE generated_reports ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE generated_reports ADD COLUMN IF NOT EXISTS user_email TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS user_email TEXT;
ALTER TABLE context_documents ADD COLUMN IF NOT EXISTS created_by UUID;
ALTER TABLE context_documents ADD COLUMN IF NOT EXISTS updated_by UUID;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS uploaded_by UUID;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS uploaded_by_email TEXT;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS user_id UUID;

CREATE INDEX IF NOT EXISTS idx_reports_user ON generated_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id);
