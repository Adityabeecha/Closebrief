ALTER TABLE metric_values ADD COLUMN forecast DOUBLE PRECISION;
ALTER TABLE computed_facts ADD COLUMN forecast_var_abs DOUBLE PRECISION;
ALTER TABLE computed_facts ADD COLUMN forecast_var_pct DOUBLE PRECISION;
ALTER TABLE import_receipts DROP CONSTRAINT import_receipts_kind_check;
ALTER TABLE import_receipts ADD CONSTRAINT import_receipts_kind_check
    CHECK (kind IN ('import', 'budget_join', 'forecast_join', 'connector_sync'));
ALTER TABLE value_changes DROP CONSTRAINT value_changes_field_check;
ALTER TABLE value_changes ADD CONSTRAINT value_changes_field_check CHECK (field IN ('value', 'budget', 'forecast'));
ALTER TABLE value_changes DROP CONSTRAINT value_changes_source_check;
ALTER TABLE value_changes ADD CONSTRAINT value_changes_source_check
    CHECK (source IN ('import', 'budget_join', 'forecast_join', 'sync', 'custom_kpi'));


CREATE TABLE metric_breakdowns (
    id BIGSERIAL PRIMARY KEY,
    workspace_id INTEGER,
    metric_id BIGINT NOT NULL REFERENCES metrics(id) ON DELETE CASCADE,
    period TEXT NOT NULL,
    dimension TEXT NOT NULL,
    member TEXT NOT NULL,
    value DOUBLE PRECISION,
    budget DOUBLE PRECISION,
    forecast DOUBLE PRECISION,
    source_rows INTEGER NOT NULL DEFAULT 1,
    UNIQUE(metric_id, period, member)
);

ALTER TABLE metric_breakdowns ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric_breakdowns FORCE ROW LEVEL SECURITY;
CREATE POLICY ws_isolation ON metric_breakdowns
    USING (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    )
    WITH CHECK (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    );

