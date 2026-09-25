-- Registro neutral de assets: el productor sólo lo invoca tras verificar PUT/conflicto.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS saas_catalog_asset_cutover_batches (
    batch_id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
    manifest_digest TEXT NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    keyset_digest TEXT NOT NULL CHECK (keyset_digest ~ '^[0-9a-f]{64}$'),
    expected_count INTEGER NOT NULL CHECK (expected_count > 0),
    verified_count INTEGER NOT NULL DEFAULT 0 CHECK (verified_count >= 0),
    missing_count INTEGER NOT NULL DEFAULT 0 CHECK (missing_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','loading','verified','failed')),
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

CREATE TABLE IF NOT EXISTS saas_catalog_asset_cutover_entries (
    batch_id UUID NOT NULL REFERENCES public.saas_catalog_asset_cutover_batches(batch_id) ON DELETE RESTRICT,
    object_name TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    byte_size BIGINT NOT NULL CHECK (byte_size > 0),
    mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png','image/jpeg','image/webp')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (batch_id, object_name),
    CHECK (object_name ~ '^[0-9a-f]{64}[.](png|jpg|jpeg|webp)$'),
    CHECK (split_part(object_name, '.', 1) = sha256),
    CHECK ((object_name ~ '[.]png$' AND mime_type = 'image/png') OR (object_name ~ '[.](jpg|jpeg)$' AND mime_type = 'image/jpeg') OR (object_name ~ '[.]webp$' AND mime_type = 'image/webp'))
);

ALTER TABLE saas_catalog_asset_cutover_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_catalog_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_catalog_asset_cutover_entries ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE saas_catalog_asset_cutover_batches FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_assets FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_asset_cutover_entries FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_asset_cutover_batches FROM service_role;
REVOKE ALL ON TABLE saas_catalog_assets FROM service_role;
REVOKE ALL ON TABLE saas_catalog_asset_cutover_entries FROM service_role;
GRANT SELECT ON TABLE saas_catalog_asset_cutover_batches TO service_role;
GRANT SELECT ON TABLE saas_catalog_assets TO service_role;
GRANT SELECT ON TABLE saas_catalog_asset_cutover_entries TO service_role;

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
    v_existing public.saas_catalog_assets%ROWTYPE;
    v_sha256 TEXT;
BEGIN
    IF p_object_name IS NULL OR p_storage_provider IS NULL OR p_physical_bucket IS NULL OR p_mime_type IS NULL
       OR p_object_name !~ '^[0-9a-f]{64}[.](png|jpg|jpeg|webp)$'
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
    INSERT INTO public.saas_catalog_assets (object_name, storage_provider, physical_bucket, sha256, byte_size, mime_type, verified_at)
    VALUES (p_object_name, p_storage_provider, p_physical_bucket, v_sha256, p_byte_size, p_mime_type, NOW())
    ON CONFLICT DO NOTHING;
    SELECT * INTO v_existing FROM public.saas_catalog_assets WHERE object_name = p_object_name FOR UPDATE;
    IF FOUND THEN
        IF v_existing.physical_bucket IS DISTINCT FROM p_physical_bucket
           OR v_existing.sha256 IS DISTINCT FROM v_sha256
           OR v_existing.byte_size IS DISTINCT FROM p_byte_size
           OR v_existing.mime_type IS DISTINCT FROM p_mime_type
           OR v_existing.verified_at IS NULL THEN
            RAISE EXCEPTION 'Catalog asset registry conflict';
        END IF;
        RETURN p_object_name;
    END IF;

    RETURN p_object_name;
END;
$$;

CREATE OR REPLACE FUNCTION saas_start_catalog_asset_cutover_batch(
    p_batch_id UUID,
    p_expected_count INTEGER,
    p_manifest_digest TEXT,
    p_keyset_digest TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch public.saas_catalog_asset_cutover_batches%ROWTYPE;
BEGIN
    IF p_batch_id IS NULL
       OR p_expected_count IS NULL
       OR p_manifest_digest IS NULL
       OR p_keyset_digest IS NULL
       OR p_expected_count <> 2214
       OR p_manifest_digest !~ '^[0-9a-f]{64}$'
       OR p_keyset_digest !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid catalog asset cutover batch';
    END IF;

    INSERT INTO public.saas_catalog_asset_cutover_batches (
        batch_id, manifest_digest, keyset_digest, expected_count, status
    ) VALUES (
        p_batch_id, p_manifest_digest, p_keyset_digest, p_expected_count, 'pending'
    )
    ON CONFLICT DO NOTHING;

    SELECT * INTO v_batch
    FROM public.saas_catalog_asset_cutover_batches
    WHERE batch_id = p_batch_id
    FOR UPDATE;
    IF NOT FOUND
       OR v_batch.manifest_digest IS DISTINCT FROM p_manifest_digest
       OR v_batch.keyset_digest IS DISTINCT FROM p_keyset_digest
       OR v_batch.expected_count IS DISTINCT FROM p_expected_count
       OR v_batch.status NOT IN ('pending','loading') THEN
        RAISE EXCEPTION 'catalog asset cutover batch conflict';
    END IF;

    UPDATE public.saas_catalog_asset_cutover_batches
    SET status = 'loading', updated_at = NOW()
    WHERE batch_id = p_batch_id;
    RETURN p_batch_id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_add_catalog_asset_cutover_entry(
    p_batch_id UUID,
    p_object_name TEXT,
    p_sha256 TEXT,
    p_byte_size BIGINT,
    p_mime_type TEXT
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_entry public.saas_catalog_asset_cutover_entries%ROWTYPE;
BEGIN
    IF p_batch_id IS NULL
       OR p_object_name IS NULL
       OR p_sha256 IS NULL
       OR p_byte_size IS NULL
       OR p_mime_type IS NULL
       OR p_object_name !~ '^[0-9a-f]{64}[.](png|jpg|jpeg|webp)$'
       OR p_sha256 !~ '^[0-9a-f]{64}$'
       OR split_part(p_object_name, '.', 1) <> p_sha256
       OR p_byte_size <= 0
       OR p_mime_type NOT IN ('image/png','image/jpeg','image/webp')
       OR (p_object_name ~ '[.]png$' AND p_mime_type <> 'image/png')
       OR (p_object_name ~ '[.](jpg|jpeg)$' AND p_mime_type <> 'image/jpeg')
       OR (p_object_name ~ '[.]webp$' AND p_mime_type <> 'image/webp') THEN
        RAISE EXCEPTION 'invalid catalog asset cutover entry';
    END IF;

    PERFORM 1
    FROM public.saas_catalog_asset_cutover_batches
    WHERE batch_id = p_batch_id AND status = 'loading'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog asset cutover batch is not loading';
    END IF;

    INSERT INTO public.saas_catalog_asset_cutover_entries (
        batch_id, object_name, sha256, byte_size, mime_type
    ) VALUES (
        p_batch_id, p_object_name, p_sha256, p_byte_size, p_mime_type
    )
    ON CONFLICT DO NOTHING;

    SELECT * INTO v_entry
    FROM public.saas_catalog_asset_cutover_entries
    WHERE batch_id = p_batch_id AND object_name = p_object_name
    FOR UPDATE;
    IF NOT FOUND
       OR v_entry.sha256 IS DISTINCT FROM p_sha256
       OR v_entry.byte_size IS DISTINCT FROM p_byte_size
       OR v_entry.mime_type IS DISTINCT FROM p_mime_type THEN
        RAISE EXCEPTION 'catalog asset cutover entry conflict';
    END IF;
    RETURN p_object_name;
END;
$$;

CREATE OR REPLACE FUNCTION saas_finalize_catalog_asset_cutover_batch(
    p_batch_id UUID
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch public.saas_catalog_asset_cutover_batches%ROWTYPE;
    v_count INTEGER;
    v_matches INTEGER;
    v_keyset TEXT;
    v_manifest TEXT;
BEGIN
    IF p_batch_id IS NULL THEN
        RAISE EXCEPTION 'invalid catalog asset cutover batch';
    END IF;
    SELECT * INTO v_batch
    FROM public.saas_catalog_asset_cutover_batches
    WHERE batch_id = p_batch_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog asset cutover batch does not exist';
    END IF;
    IF v_batch.status = 'verified' THEN
        RETURN p_batch_id;
    END IF;
    IF v_batch.status <> 'loading' OR v_batch.expected_count <> 2214 THEN
        RAISE EXCEPTION 'catalog asset cutover batch is not finalizable';
    END IF;

    SELECT COUNT(*)::INTEGER,
           encode(extensions.digest(convert_to(
               string_agg(object_name, E'\n' ORDER BY object_name), 'UTF8'
           ), 'sha256'), 'hex'),
           encode(extensions.digest(convert_to(string_agg(
               object_name || '|' || sha256 || '|' || byte_size::TEXT || '|' || mime_type,
               E'\n' ORDER BY object_name
           ), 'UTF8'), 'sha256'), 'hex')
    INTO v_count, v_keyset, v_manifest
    FROM public.saas_catalog_asset_cutover_entries
    WHERE batch_id = p_batch_id;
    IF v_count <> 2214
       OR v_keyset IS DISTINCT FROM v_batch.keyset_digest
       OR v_manifest IS DISTINCT FROM v_batch.manifest_digest THEN
        RAISE EXCEPTION 'catalog asset cutover manifest is not verified';
    END IF;

    SELECT COUNT(*)::INTEGER INTO v_matches
    FROM public.saas_catalog_asset_cutover_entries AS entry
    JOIN public.saas_catalog_assets AS asset
      ON asset.object_name = entry.object_name
     AND asset.sha256 = entry.sha256
     AND asset.byte_size = entry.byte_size
     AND asset.mime_type = entry.mime_type
     AND asset.storage_provider = 'r2'
     AND asset.physical_bucket = 'catalog-assets'
     AND asset.verified_at IS NOT NULL
     AND (asset.cutover_batch_id IS NULL OR asset.cutover_batch_id = p_batch_id)
    WHERE entry.batch_id = p_batch_id;
    IF v_matches <> 2214 THEN
        RAISE EXCEPTION 'catalog asset cutover registry mismatch';
    END IF;

    UPDATE public.saas_catalog_assets AS asset
    SET cutover_batch_id = p_batch_id, updated_at = NOW()
    FROM public.saas_catalog_asset_cutover_entries AS entry
    WHERE entry.batch_id = p_batch_id
      AND asset.object_name = entry.object_name
      AND asset.sha256 = entry.sha256
      AND asset.byte_size = entry.byte_size
      AND asset.mime_type = entry.mime_type
      AND asset.storage_provider = 'r2'
      AND asset.physical_bucket = 'catalog-assets'
      AND asset.verified_at IS NOT NULL;
    UPDATE public.saas_catalog_asset_cutover_batches
    SET status = 'verified', verified_count = v_count,
        missing_count = 0, failed_count = 0,
        verified_at = NOW(), updated_at = NOW()
    WHERE batch_id = p_batch_id;
    RETURN p_batch_id;
END;
$$;

REVOKE ALL ON FUNCTION saas_register_catalog_asset(TEXT, TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION saas_register_catalog_asset(TEXT, TEXT, TEXT, BIGINT, TEXT) TO service_role;
REVOKE ALL ON FUNCTION saas_start_catalog_asset_cutover_batch(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_add_catalog_asset_cutover_entry(UUID, TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_finalize_catalog_asset_cutover_batch(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION saas_start_catalog_asset_cutover_batch(UUID, INTEGER, TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION saas_add_catalog_asset_cutover_entry(UUID, TEXT, TEXT, BIGINT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION saas_finalize_catalog_asset_cutover_batch(UUID) TO service_role;

COMMIT;
