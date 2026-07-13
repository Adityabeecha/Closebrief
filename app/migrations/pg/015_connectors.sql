-- v4.0 live data connectors (scheduled syncs).
CREATE TABLE IF NOT EXISTS connectors (
    id           SERIAL PRIMARY KEY,
    workspace_id INTEGER,
    kind         TEXT NOT NULL,          -- 'csv_url' | 'google_sheets'
    name         TEXT NOT NULL,
    config       TEXT NOT NULL DEFAULT '{}',
    dataset_name TEXT,
    enabled      BOOLEAN NOT NULL DEFAULT true,
    last_sync_at TEXT,
    last_status  TEXT,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_connectors_ws ON connectors(workspace_id);
