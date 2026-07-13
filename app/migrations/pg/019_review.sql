-- v5.0 collaborative review: reviewer assignment, status, and version history.
ALTER TABLE generated_reports ADD COLUMN IF NOT EXISTS review_status TEXT;
ALTER TABLE generated_reports ADD COLUMN IF NOT EXISTS assigned_to TEXT;
ALTER TABLE generated_reports ADD COLUMN IF NOT EXISTS assigned_email TEXT;

CREATE TABLE IF NOT EXISTS report_versions (
    id           SERIAL PRIMARY KEY,
    report_id    INTEGER NOT NULL,
    version      INTEGER NOT NULL,
    narrative    TEXT,
    editor_id    TEXT,
    editor_email TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_report_versions ON report_versions(report_id, version);
