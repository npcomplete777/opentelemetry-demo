CREATE SCHEMA IF NOT EXISTS warehouse;
CREATE TABLE IF NOT EXISTS warehouse.stock (
  sku       TEXT PRIMARY KEY,
  quantity  INTEGER NOT NULL,
  price_usd NUMERIC(10,2) NOT NULL
);
CREATE TABLE IF NOT EXISTS warehouse.audit (
  id         BIGSERIAL PRIMARY KEY,
  sku        TEXT NOT NULL,
  event      TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO warehouse.stock (sku, quantity, price_usd) VALUES
  ('TELESCOPE-1', 100000, 349.99),
  ('BINOCULAR-2', 100000,  89.50),
  ('STARMAP-3',   100000,  12.00)
ON CONFLICT (sku) DO NOTHING;
