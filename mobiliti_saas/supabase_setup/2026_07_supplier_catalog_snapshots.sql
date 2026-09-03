-- Latest validated supplier catalog snapshots used by the API.

CREATE TABLE IF NOT EXISTS saas_supplier_catalog_snapshots (
    supplier TEXT PRIMARY KEY CHECK (supplier IN ('tarkett')),
    source_hash TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_supplier_catalog_snapshots_updated_at
    ON saas_supplier_catalog_snapshots(updated_at DESC);

ALTER TABLE saas_supplier_catalog_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE saas_supplier_catalog_snapshots FROM anon, authenticated;
GRANT ALL ON TABLE saas_supplier_catalog_snapshots TO service_role;
