-- Registro neutral de assets: el productor sólo lo invoca tras verificar PUT/conflicto.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS saas_catalog_asset_cutover_batches (
    batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_digest TEXT NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    keyset_digest TEXT NOT NULL CHECK (keyset_digest ~ '^[0-9a-f]{64}$'),
    expected_count INTEGER NOT NULL CHECK (expected_count > 0),
    verified_count INTEGER NOT NULL DEFAULT 0 CHECK (verified_count >= 0),
    missing_count INTEGER NOT NULL DEFAULT 0 CHECK (missing_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','verified','failed')),
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (status = 'verified' AND verified_count = expected_count
         AND missing_count = 0 AND failed_count = 0 AND verified_at IS NOT NULL)
        OR (status <> 'verified' AND verified_at IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS saas_catalog_assets (
    object_name TEXT PRIMARY KEY,
    storage_provider TEXT NOT NULL CHECK (storage_provider IN ('supabase','r2')),
    physical_bucket TEXT NOT NULL CHECK (physical_bucket = 'catalog-assets'),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    byte_size BIGINT NOT NULL CHECK (byte_size > 0),
    mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png','image/jpeg','image/webp')),
    cutover_batch_id UUID REFERENCES saas_catalog_asset_cutover_batches(batch_id) ON DELETE RESTRICT,
    verified_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (object_name ~ '^[0-9a-f]{64}[.](png|jpg|jpeg|webp)$'),
    CHECK (split_part(object_name, '.', 1) = sha256),
    CHECK (
        (object_name ~ '[.]png$' AND mime_type = 'image/png')
        OR (object_name ~ '[.](jpg|jpeg)$' AND mime_type = 'image/jpeg')
        OR (object_name ~ '[.]webp$' AND mime_type = 'image/webp')
    )
);

ALTER TABLE saas_catalog_asset_cutover_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_catalog_assets ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE saas_catalog_asset_cutover_batches FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_assets FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_asset_cutover_batches FROM service_role;
REVOKE ALL ON TABLE saas_catalog_assets FROM service_role;
GRANT SELECT ON TABLE saas_catalog_asset_cutover_batches TO service_role;
GRANT SELECT ON TABLE saas_catalog_assets TO service_role;

CREATE OR REPLACE FUNCTION saas_register_catalog_asset(
    p_object_name TEXT,
    p_storage_provider TEXT,
    p_physical_bucket TEXT,
    p_byte_size BIGINT,
    p_mime_type TEXT
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_existing saas_catalog_assets%ROWTYPE;
    v_sha256 TEXT;
BEGIN
    IF p_object_name !~ '^[0-9a-f]{64}[.](png|jpg|jpeg|webp)$'
       OR p_storage_provider NOT IN ('supabase','r2')
       OR p_physical_bucket <> 'catalog-assets'
       OR p_byte_size IS NULL OR p_byte_size <= 0
       OR p_mime_type NOT IN ('image/png','image/jpeg','image/webp')
       OR (p_object_name ~ '[.]png$' AND p_mime_type <> 'image/png')
       OR (p_object_name ~ '[.](jpg|jpeg)$' AND p_mime_type <> 'image/jpeg')
       OR (p_object_name ~ '[.]webp$' AND p_mime_type <> 'image/webp') THEN
        RAISE EXCEPTION 'invalid catalog asset registry input';
    END IF;

    v_sha256 := split_part(p_object_name, '.', 1);
    SELECT * INTO v_existing
    FROM saas_catalog_assets
    WHERE object_name = p_object_name
    FOR UPDATE;

    IF FOUND THEN
        IF v_existing.storage_provider IS DISTINCT FROM p_storage_provider
           OR v_existing.physical_bucket IS DISTINCT FROM p_physical_bucket
           OR v_existing.sha256 IS DISTINCT FROM v_sha256
           OR v_existing.byte_size IS DISTINCT FROM p_byte_size
           OR v_existing.mime_type IS DISTINCT FROM p_mime_type
           OR v_existing.verified_at IS NULL THEN
            RAISE EXCEPTION 'Catalog asset registry conflict';
        END IF;
        RETURN p_object_name;
    END IF;

    INSERT INTO saas_catalog_assets (
        object_name, storage_provider, physical_bucket, sha256, byte_size, mime_type, verified_at
    ) VALUES (
        p_object_name, p_storage_provider, p_physical_bucket, v_sha256,
        p_byte_size, p_mime_type, NOW()
    );
    RETURN p_object_name;
END;
$$;

REVOKE ALL ON FUNCTION saas_register_catalog_asset(TEXT, TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION saas_register_catalog_asset(TEXT, TEXT, TEXT, BIGINT, TEXT) TO service_role;

COMMIT;
