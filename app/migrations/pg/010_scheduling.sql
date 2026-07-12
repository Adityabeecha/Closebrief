-- v3.1 scheduling backbone: scheduled jobs (digest / anomaly scan) + digest history.
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id           SERIAL PRIMARY KEY,
    kind         TEXT NOT NULL,           -- 'digest' | 'anomaly_scan'
    cadence      TEXT NOT NULL,           -- 'daily' | 'weekly' | 'monthly'
    dataset_id   INTEGER,                 -- NULL = active real dataset at run time
    config       TEXT NOT NULL DEFAULT '{}',
    enabled      BOOLEAN NOT NULL DEFAULT true,
    last_run_at  TEXT,
    next_run_at  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS digest_runs (
    id           SERIAL PRIMARY KEY,
    dataset_id   INTEGER,
    period       TEXT NOT NULL,
    top_n        INTEGER NOT NULL,
    items        TEXT NOT NULL,           -- JSON snapshot of the digest items
    cost_usd     DOUBLE PRECISION,
    trigger      TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'scheduled'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_digest_runs_dataset ON digest_runs(dataset_id, id DESC);
