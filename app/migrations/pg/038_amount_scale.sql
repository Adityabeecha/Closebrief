-- Files reported in thousands or millions: amounts are stored as real amounts, and
-- each metric remembers the factor applied so a unit change can undo it.
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS amount_scale INTEGER NOT NULL DEFAULT 1;
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS value_scale DOUBLE PRECISION NOT NULL DEFAULT 1;
-- A dataset whose import stopped before "Finish" (cleaned up after two days).
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS import_pending BOOLEAN NOT NULL DEFAULT false;
-- Words a workspace hides from the AI provider (company, customer, people names).
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS hidden_terms TEXT;
-- What the comparison columns are called ("prior year", "non-GAAP", "guidance").
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS budget_label TEXT;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS forecast_label TEXT;
-- The team's house style for commentary (JSON).
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS house_style TEXT;
