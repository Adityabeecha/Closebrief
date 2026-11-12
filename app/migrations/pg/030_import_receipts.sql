CREATE TABLE import_receipts (
    id BIGSERIAL PRIMARY KEY,
    dataset_id BIGINT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    workspace_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind TEXT NOT NULL CHECK (kind IN ('import', 'budget_join', 'connector_sync')),
    filename TEXT NOT NULL,
    mapping TEXT NOT NULL,
    report TEXT NOT NULL,
    kpi_totals TEXT NOT NULL
);

ALTER TABLE import_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE import_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY ws_isolation ON import_receipts
    USING (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    )
    WITH CHECK (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    );
