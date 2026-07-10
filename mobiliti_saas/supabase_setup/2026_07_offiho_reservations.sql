-- Mobiliti SaaS Offiho reservations
-- Ejecutar despues de 2026_06_quote_jobs.sql.

CREATE TABLE IF NOT EXISTS saas_offiho_reservations (
    id UUID PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES saas_usuarios(id) ON DELETE CASCADE,
    quote_job_id UUID NOT NULL REFERENCES saas_quote_jobs(id) ON DELETE CASCADE,
    product_code TEXT NOT NULL,
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','released')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_offiho_reservations_product_status
    ON saas_offiho_reservations(product_code, status);
CREATE INDEX IF NOT EXISTS idx_offiho_reservations_usuario
    ON saas_offiho_reservations(usuario_id);
CREATE INDEX IF NOT EXISTS idx_offiho_reservations_quote_job
    ON saas_offiho_reservations(quote_job_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_offiho_reservations_quote_job_product
    ON saas_offiho_reservations(quote_job_id, product_code);

ALTER TABLE saas_offiho_reservations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE saas_offiho_reservations FROM anon, authenticated;
GRANT ALL ON TABLE saas_offiho_reservations TO service_role;
