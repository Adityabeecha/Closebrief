-- v4.0 tenancy backstop: Row-Level Security on the two data roots, so a workspace
-- can never read/write another's rows even if an app query forgets its scope
-- (defense in depth over app-level scoping).
--
-- FAIL-OPEN by design: when the app.workspace_id GUC is unset (migrations,
-- background jobs, or rls_enabled=false) the policy allows all rows, so enabling
-- RLS here changes nothing until the app starts setting the GUC (rls_enabled=true).
-- When the GUC is a workspace id, only that workspace's rows are visible/writable.

ALTER TABLE datasets          ENABLE ROW LEVEL SECURITY;
ALTER TABLE datasets          FORCE  ROW LEVEL SECURITY;
ALTER TABLE context_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_documents FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ws_isolation ON datasets;
CREATE POLICY ws_isolation ON datasets
    USING (
        coalesce(current_setting('app.workspace_id', true), '') = ''
        OR workspace_id = current_setting('app.workspace_id', true)::int
    )
    WITH CHECK (
        coalesce(current_setting('app.workspace_id', true), '') = ''
        OR workspace_id = current_setting('app.workspace_id', true)::int
    );

DROP POLICY IF EXISTS ws_isolation ON context_documents;
CREATE POLICY ws_isolation ON context_documents
    USING (
        coalesce(current_setting('app.workspace_id', true), '') = ''
        OR workspace_id = current_setting('app.workspace_id', true)::int
    )
    WITH CHECK (
        coalesce(current_setting('app.workspace_id', true), '') = ''
        OR workspace_id = current_setting('app.workspace_id', true)::int
    );
