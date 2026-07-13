-- Harden the RLS policies from 017: cast via nullif so an empty GUC ('') never
-- reaches ''::int (Postgres may evaluate both OR branches, which would error).
-- nullif('', '') -> NULL, and NULL::int -> NULL (safe). Fail-open preserved.
DROP POLICY IF EXISTS ws_isolation ON datasets;
CREATE POLICY ws_isolation ON datasets
    USING (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    )
    WITH CHECK (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    );

DROP POLICY IF EXISTS ws_isolation ON context_documents;
CREATE POLICY ws_isolation ON context_documents
    USING (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    )
    WITH CHECK (
        nullif(current_setting('app.workspace_id', true), '') IS NULL
        OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::int
    );
