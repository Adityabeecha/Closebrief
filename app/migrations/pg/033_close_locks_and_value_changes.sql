
CREATE TABLE value_changes (
    id BIGSERIAL PRIMARY KEY,
    workspace_id INTEGER,
    dataset_id BIGINT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    metric_id BIGINT NOT NULL REFERENCES metrics(id) ON DELETE CASCADE,
    period TEXT NOT NULL,
    field TEXT NOT NULL CHECK (field IN ('value', 'budget')),
    old_value DOUBLE PRECISION,
    new_value DOUBLE PRECISION,
    source TEXT NOT NULL CHECK (source IN ('import', 'budget_join', 'sync', 'custom_kpi')),
    actor_id TEXT,
    actor_email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_value_changes_dataset_period ON value_changes(dataset_id, period);
CREATE TABLE close_locks (
    id BIGSERIAL PRIMARY KEY,
    workspace_id INTEGER,
    dataset_id BIGINT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    period TEXT NOT NULL,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by_id TEXT,
    locked_by_email TEXT,
    UNIQUE(dataset_id, period)
);

ALTER TABLE value_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE value_changes FORCE ROW LEVEL SECURITY;
CREATE POLICY ws_isolation ON value_changes
    USING (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    )
    WITH CHECK (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    );

ALTER TABLE close_locks ENABLE ROW LEVEL SECURITY;
ALTER TABLE close_locks FORCE ROW LEVEL SECURITY;
CREATE POLICY ws_isolation ON close_locks
    USING (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    )
    WITH CHECK (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    );
