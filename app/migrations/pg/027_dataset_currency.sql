-- P6: dataset currency is formatting metadata; existing amounts stay unchanged.
-- Staged uploads retain the optional import currency until mapping creates a dataset.
ALTER TABLE datasets ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'CAD', 'AUD', 'SGD', 'CHF'));
ALTER TABLE uploads ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'CAD', 'AUD', 'SGD', 'CHF'));
