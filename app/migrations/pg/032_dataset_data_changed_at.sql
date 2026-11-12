ALTER TABLE datasets ADD COLUMN data_changed_at TIMESTAMPTZ;
UPDATE datasets SET data_changed_at = created_at WHERE data_changed_at IS NULL;
