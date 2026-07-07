-- v2.1 notification channel configs (email / slack / webhook).
CREATE TABLE IF NOT EXISTS notification_configs (
    id          SERIAL PRIMARY KEY,
    channel     TEXT NOT NULL,           -- 'email' | 'slack' | 'webhook'
    config      TEXT NOT NULL,           -- JSON string: {recipients, schedule, events, ...}
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
