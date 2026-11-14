-- Someone confirmed what the amounts are in (units, thousands, millions). Until then a
-- dataset named '$ thousands' with unscaled amounts can't be shared.
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS units_confirmed BOOLEAN NOT NULL DEFAULT false;
