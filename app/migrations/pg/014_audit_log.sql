-- v4.0 immutable audit trail (append-only, hash-chained per workspace).
CREATE TABLE IF NOT EXISTS audit_log (
    id           SERIAL PRIMARY KEY,
    workspace_id INTEGER,
    actor_id     TEXT,
    actor_email  TEXT,
    action       TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT,
    summary      TEXT,
    row_hash     TEXT NOT NULL,
    prev_hash    TEXT,
    created_at   TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS')
);

CREATE INDEX IF NOT EXISTS idx_audit_ws ON audit_log(workspace_id, id);
