ALTER TABLE computed_facts ADD COLUMN anomaly_basis TEXT
    CHECK (anomaly_basis IN ('seasonal', 'month_on_month'));
ALTER TABLE computed_facts ADD COLUMN anomaly_z DOUBLE PRECISION;
ALTER TABLE datasets ADD COLUMN reason_threshold_pct DOUBLE PRECISION NOT NULL DEFAULT 10
    CHECK (reason_threshold_pct >= 1 AND reason_threshold_pct <= 100);
