-- A trial balance's accounts mapped to P&L lines, remembered per workspace so next
-- month's export maps itself (workspace_id 0 = no workspace, local use).
CREATE TABLE IF NOT EXISTS account_maps (
    workspace_id BIGINT NOT NULL DEFAULT 0,
    account_key TEXT NOT NULL,
    line TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, account_key)
);

ALTER TABLE account_maps ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_maps FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ws_isolation ON account_maps;
CREATE POLICY ws_isolation ON account_maps
    USING (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    )
    WITH CHECK (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    );
