-- v2.1 multi-domain: each dataset belongs to an analytics domain (fpa default).
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'fpa';
