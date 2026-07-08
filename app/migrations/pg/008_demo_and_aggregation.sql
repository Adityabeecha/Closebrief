-- v3.0: demo dataset isolation + per-KPI aggregation rule for period rollups.
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE kpi_configs ADD COLUMN IF NOT EXISTS aggregation_type TEXT;
