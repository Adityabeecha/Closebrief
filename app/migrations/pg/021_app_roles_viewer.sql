-- 'viewer' became a real role after 004 was written (VALID_ROLES in app/auth.py),
-- but the CHECK constraint there still only allowed analyst/executive/admin.
-- It went unnoticed because viewer was used only for demo sessions, which never
-- persist an app_roles row. Google sign-in defaults new users to 'viewer' and
-- DOES persist one, so without this every new Google signup fails on Postgres.
ALTER TABLE app_roles DROP CONSTRAINT IF EXISTS app_roles_role_check;
ALTER TABLE app_roles ADD CONSTRAINT app_roles_role_check
    CHECK (role IN ('viewer', 'analyst', 'executive', 'admin'));
