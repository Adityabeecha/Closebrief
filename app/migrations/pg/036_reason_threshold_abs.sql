ALTER TABLE datasets ADD COLUMN reason_threshold_abs DOUBLE PRECISION
    CHECK (reason_threshold_abs IS NULL OR reason_threshold_abs >= 0);
