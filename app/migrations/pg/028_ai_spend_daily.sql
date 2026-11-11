-- S13: preserve the GLOBAL daily cap after llm_calls gets FORCE RLS.
-- ai_spend_daily holds only one aggregate per UTC day: no tenant/user data,
-- no workspace_id and intentionally no RLS. The call logger increments it
-- atomically alongside the scoped llm_calls insert. Global cap checks read
-- this counter; per-user checks continue to read scoped llm_calls.
-- A SECURITY DEFINER function would still be filtered by FORCE RLS unless it
-- depended on BYPASSRLS privileges or cleared app.workspace_id. Neither is
-- acceptable: request connections must retain their verified workspace scope.
-- Migrations run unscoped, so today's existing calls can be backfilled here.
CREATE TABLE IF NOT EXISTS ai_spend_daily (
    day DATE PRIMARY KEY,
    cost_usd NUMERIC NOT NULL DEFAULT 0
);

INSERT INTO ai_spend_daily (day, cost_usd)
SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date, COALESCE(SUM(cost_usd), 0)
FROM llm_calls
WHERE created_at >= ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date::timestamp AT TIME ZONE 'UTC')
ON CONFLICT (day) DO NOTHING;
