-- A stable code from the source system (GL account code, SKU, ...), captured
-- from the mapping's id_col at ingest. Lets a later budget-only upload join
-- onto an existing metric by code rather than by label alone, which doesn't
-- drift the way a text label can ("Content/Seo" vs "Content/SEO").
SET LOCAL lock_timeout = '10s';
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS external_id TEXT;
