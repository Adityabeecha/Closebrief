-- v4.0 usage-based metering: per-workspace LLM spend + spend limits/tiers.
ALTER TABLE llm_calls  ADD COLUMN IF NOT EXISTS workspace_id INTEGER;
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free';
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS monthly_budget_usd DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_llm_calls_ws ON llm_calls(workspace_id, created_at);
