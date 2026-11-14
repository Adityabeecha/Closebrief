-- A note can cover several periods: through a month ('YYYY-MM') or until turned off ('ongoing').
ALTER TABLE context_documents ADD COLUMN IF NOT EXISTS effective_until TEXT;
