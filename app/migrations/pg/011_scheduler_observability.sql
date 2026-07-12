-- v3.0 scheduler observability: per-run log + last-status/failure tracking.
ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS last_status TEXT;
ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS fail_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS scheduler_runs (
    id          SERIAL PRIMARY KEY,
    job_id      INTEGER,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,       -- 'ok' | 'skipped' | 'error'
    detail      TEXT,
    latency_ms  DOUBLE PRECISION,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduler_runs_job ON scheduler_runs(job_id, id DESC);
