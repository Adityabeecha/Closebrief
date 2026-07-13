-- v5.0 custom/derived KPIs (formula over other metrics).
CREATE TABLE IF NOT EXISTS derived_metrics (
    id             SERIAL PRIMARY KEY,
    dataset_id     INTEGER,
    name           TEXT NOT NULL,
    formula        TEXT NOT NULL,
    unit           TEXT NOT NULL DEFAULT 'USD',
    category       TEXT NOT NULL DEFAULT 'Derived',
    direction_good TEXT NOT NULL DEFAULT 'up',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(dataset_id, name)
);
