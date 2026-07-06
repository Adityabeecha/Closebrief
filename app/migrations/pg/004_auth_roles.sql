-- v1.2 Phase 2: application roles. Maps a Supabase auth.users UUID to an app
-- role. We don't FK to auth.users (different schema, managed by Supabase) — the
-- id is the JWT `sub` claim.

CREATE TABLE IF NOT EXISTS app_roles (
    user_id UUID PRIMARY KEY,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'analyst'
        CHECK (role IN ('analyst', 'executive', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_roles_email ON app_roles(email);
