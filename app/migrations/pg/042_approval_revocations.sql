-- "Revoke approval links" for a close: every link issued before revoked_epoch stops working.
CREATE TABLE IF NOT EXISTS approval_revocations (
    workspace_id INTEGER,
    dataset_id INTEGER NOT NULL,
    period TEXT NOT NULL,
    revoked_epoch BIGINT NOT NULL,
    revoked_by TEXT,
    PRIMARY KEY (dataset_id, period)
);

ALTER TABLE approval_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_revocations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ws_isolation ON approval_revocations;
CREATE POLICY ws_isolation ON approval_revocations
    USING (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    )
    WITH CHECK (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    );
