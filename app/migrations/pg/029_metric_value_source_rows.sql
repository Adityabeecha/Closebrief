-- Preserve how many distinct source rows contributed to each monthly figure.
ALTER TABLE metric_values ADD COLUMN source_rows INTEGER NOT NULL DEFAULT 1;
