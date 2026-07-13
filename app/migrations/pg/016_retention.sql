-- v4.0 configurable data retention (per-workspace).
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS retention_days INTEGER;
