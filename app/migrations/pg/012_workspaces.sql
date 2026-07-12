-- v4.0 multi-tenancy: workspaces (tenants) + membership + invites, and
-- workspace_id on the two data roots. Existing data is backfilled to a single
-- "Default workspace" so nothing is orphaned; app-level scoping + (future) RLS
-- enforce isolation.
CREATE TABLE IF NOT EXISTS workspaces (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
    user_id      TEXT NOT NULL,
    email        TEXT,
    role         TEXT NOT NULL DEFAULT 'analyst',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS workspace_invites (
    token        TEXT PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
    role         TEXT NOT NULL DEFAULT 'analyst',
    email        TEXT,
    accepted     BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE datasets          ADD COLUMN IF NOT EXISTS workspace_id INTEGER;
ALTER TABLE context_documents ADD COLUMN IF NOT EXISTS workspace_id INTEGER;

-- Backfill: one Default workspace owns all pre-existing real data + users.
INSERT INTO workspaces (name)
SELECT 'Default workspace'
WHERE NOT EXISTS (SELECT 1 FROM workspaces);

UPDATE datasets
   SET workspace_id = (SELECT MIN(id) FROM workspaces)
 WHERE workspace_id IS NULL AND COALESCE(is_demo, false) = false;

UPDATE context_documents
   SET workspace_id = (SELECT MIN(id) FROM workspaces)
 WHERE workspace_id IS NULL AND COALESCE(is_demo, false) = false;

-- Every existing user becomes a member of the default workspace, keeping their role.
INSERT INTO workspace_members (workspace_id, user_id, email, role)
SELECT (SELECT MIN(id) FROM workspaces), user_id, email, role
FROM app_roles
ON CONFLICT (workspace_id, user_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_datasets_ws ON datasets(workspace_id);
CREATE INDEX IF NOT EXISTS idx_context_ws ON context_documents(workspace_id);
CREATE INDEX IF NOT EXISTS idx_members_user ON workspace_members(user_id);
