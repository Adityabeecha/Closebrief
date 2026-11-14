-- Files reported in thousands or millions: amounts are stored as real amounts, and
-- each metric remembers the factor applied so a unit change can undo it.
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS amount_scale INTEGER NOT NULL DEFAULT 1;
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS value_scale DOUBLE PRECISION NOT NULL DEFAULT 1;
-- A dataset whose import stopped before "Finish" (cleaned up after two days).
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS import_pending BOOLEAN NOT NULL DEFAULT false;
