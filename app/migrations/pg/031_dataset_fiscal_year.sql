ALTER TABLE datasets ADD COLUMN fiscal_year_start INTEGER NOT NULL DEFAULT 1
    CHECK (fiscal_year_start BETWEEN 1 AND 12);
