-- Keep staged uploads across container restarts; legacy paths remain readable.
ALTER TABLE uploads ADD COLUMN IF NOT EXISTS content BYTEA;
ALTER TABLE uploads ALTER COLUMN file_path DROP NOT NULL;
