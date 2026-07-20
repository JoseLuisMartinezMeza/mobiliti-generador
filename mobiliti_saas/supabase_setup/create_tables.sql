-- ============================================================
-- Tablas para Mobiliti SaaS (backend en Vercel + Supabase)
-- Ejecutar en SQL Editor de Supabase Dashboard
-- ============================================================

-- Tabla de usuarios
CREATE TABLE IF NOT EXISTS saas_usuarios (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    nombre TEXT,
    empresa TEXT,
    es_admin BOOLEAN DEFAULT FALSE,
    activo BOOLEAN DEFAULT TRUE,
    creado TIMESTAMPTZ DEFAULT NOW()
);

-- Tabla de suscripciones
CREATE TABLE IF NOT EXISTS saas_suscripciones (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES saas_usuarios(id) ON DELETE CASCADE,
    estado TEXT DEFAULT 'activa' CHECK (estado IN ('activa','vencida','suspendida','cancelada')),
    plan TEXT DEFAULT 'mensual',
    fecha_inicio TIMESTAMPTZ DEFAULT NOW(),
    fecha_fin TIMESTAMPTZ NOT NULL,
    creado TIMESTAMPTZ DEFAULT NOW()
);

-- Tabla de sesiones
CREATE TABLE IF NOT EXISTS saas_sesiones (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES saas_usuarios(id) ON DELETE CASCADE,
    token_jwt TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    activa BOOLEAN DEFAULT TRUE,
    creado TIMESTAMPTZ DEFAULT NOW(),
    ultimo_uso TIMESTAMPTZ DEFAULT NOW()
);

-- Ãndices Ãºtiles
CREATE INDEX IF NOT EXISTS idx_usuarios_email ON saas_usuarios(email);
CREATE INDEX IF NOT EXISTS idx_suscripciones_usuario ON saas_suscripciones(usuario_id);
CREATE INDEX IF NOT EXISTS idx_sesiones_token ON saas_sesiones(token_jwt);

-- ============================================================
-- Tabla de versiones (para auto-updater)
-- ============================================================

CREATE TABLE IF NOT EXISTS saas_versiones (
    id SERIAL PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    download_url TEXT NOT NULL,
    release_notes TEXT,
    force_update BOOLEAN DEFAULT FALSE,
    activa BOOLEAN DEFAULT TRUE,
    creado TIMESTAMPTZ DEFAULT NOW(),
    actualizado TIMESTAMPTZ DEFAULT NOW()
);

-- Insertar versiÃ³n actual (v1.5.6)
CREATE UNIQUE INDEX IF NOT EXISTS idx_versiones_version ON saas_versiones(version);

INSERT INTO saas_versiones (version, download_url, release_notes, force_update)
VALUES (
    '1.5.6',
    'https://github.com/JoseLuisMartinezMeza/mobiliti-generador/releases/download/v1.5.6/Mobiliti_Generador.exe',
    'Fix: eliminado subprocess en modo .exe para evitar crash _MEI Tcl data',
    FALSE
)
ON CONFLICT (version) DO UPDATE SET
    download_url = EXCLUDED.download_url,
    release_notes = EXCLUDED.release_notes,
    force_update = EXCLUDED.force_update,
    actualizado = NOW();

-- Solo debe haber una versiÃ³n activa: desactivar las anteriores automÃ¡ticamente
CREATE OR REPLACE FUNCTION mantener_unica_version_activa()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.activa THEN
        UPDATE saas_versiones SET activa = FALSE WHERE id != NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_unica_version_activa ON saas_versiones;
CREATE TRIGGER trigger_unica_version_activa
    AFTER INSERT OR UPDATE ON saas_versiones
    FOR EACH ROW
    EXECUTE FUNCTION mantener_unica_version_activa();

-- Ãndice Ãºtil
CREATE INDEX IF NOT EXISTS idx_versiones_activa ON saas_versiones(activa);

-- ============================================================
-- Storage privado y cola de cotizaciones web
-- ============================================================

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'quote-files',
    'quote-files',
    FALSE,
    104857600,
    ARRAY[
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/pdf',
        'application/octet-stream'
    ]
)
ON CONFLICT (id) DO UPDATE SET
    public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

CREATE TABLE IF NOT EXISTS saas_quote_jobs (
    id UUID PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES saas_usuarios(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','queued','processing','completed','failed')),
    input_path TEXT NOT NULL,
    output_path TEXT,
    template TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_quote_jobs_usuario ON saas_quote_jobs(usuario_id);
CREATE INDEX IF NOT EXISTS idx_quote_jobs_status ON saas_quote_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_quote_jobs_updated ON saas_quote_jobs(updated_at DESC);

CREATE TABLE IF NOT EXISTS saas_tarkett_reservations (
    id UUID PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES saas_usuarios(id) ON DELETE CASCADE,
    quote_job_id UUID NOT NULL REFERENCES saas_quote_jobs(id) ON DELETE CASCADE,
    product_code TEXT NOT NULL,
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','released')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tarkett_reservations_product_status
    ON saas_tarkett_reservations(product_code, status);
CREATE INDEX IF NOT EXISTS idx_tarkett_reservations_usuario
    ON saas_tarkett_reservations(usuario_id);
CREATE INDEX IF NOT EXISTS idx_tarkett_reservations_quote_job
    ON saas_tarkett_reservations(quote_job_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tarkett_reservations_quote_job_product
    ON saas_tarkett_reservations(quote_job_id, product_code);

ALTER TABLE saas_tarkett_reservations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE saas_tarkett_reservations FROM anon, authenticated;
GRANT ALL ON TABLE saas_tarkett_reservations TO service_role;

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

-- ============================================================
-- Multi-supplier catalog staging and jobs RLS
-- ============================================================

-- Additive staging, immutable versions, and publication workflow for new suppliers.

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS saas_catalog_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier TEXT NOT NULL UNIQUE CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro')),
    label TEXT NOT NULL,
    adapter TEXT NOT NULL,
    graph_drive_id TEXT NOT NULL,
    graph_root_item_id TEXT NOT NULL,
    delta_link TEXT,
    sync_interval INTERVAL NOT NULL DEFAULT INTERVAL '6 hours'
        CHECK (sync_interval > INTERVAL '0 seconds'),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE saas_catalog_sources
    ALTER COLUMN sync_interval SET DEFAULT INTERVAL '6 hours';

CREATE TABLE IF NOT EXISTS saas_catalog_sync_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES saas_catalog_sources(id) ON DELETE RESTRICT,
    request_key UUID,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('scheduled','manual')),
    status TEXT NOT NULL DEFAULT 'requested'
        CHECK (status IN (
            'requested','running','no_changes','awaiting_approval',
            'published','rejected','failed'
        )),
    requested_by INTEGER REFERENCES saas_usuarios(id) ON DELETE RESTRICT,
    reviewed_by INTEGER REFERENCES saas_usuarios(id) ON DELETE RESTRICT,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics) = 'object'),
    error_summary TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    reviewed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT saas_catalog_sync_runs_id_source_unique UNIQUE (id, source_id)
);

ALTER TABLE saas_catalog_sync_runs
    ADD COLUMN IF NOT EXISTS request_key UUID;
ALTER TABLE saas_catalog_sync_runs
    DROP CONSTRAINT IF EXISTS saas_catalog_sync_runs_request_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_sync_runs_request_key
    ON saas_catalog_sync_runs(request_key)
    WHERE request_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS saas_catalog_source_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES saas_catalog_sources(id) ON DELETE RESTRICT,
    drive_item_id TEXT NOT NULL,
    path TEXT NOT NULL,
    e_tag TEXT NOT NULL,
    c_tag TEXT,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    mime_type TEXT NOT NULL CHECK (mime_type IN (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/pdf'
    )),
    private_object_path TEXT NOT NULL
        CHECK (private_object_path ~ '^catalog-sources/[0-9a-f]{64}\.(xlsx|pdf)$'),
    validation_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (validation_status IN ('pending','valid','invalid')),
    validation_summary JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(validation_summary) = 'object'),
    last_sync_run_id UUID REFERENCES saas_catalog_sync_runs(id) ON DELETE RESTRICT,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_sync_run_id UUID,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    validated_at TIMESTAMPTZ,
    CONSTRAINT saas_catalog_source_files_deletion_state_check CHECK (
        (NOT is_deleted AND deleted_at IS NULL AND deleted_sync_run_id IS NULL)
        OR (is_deleted AND deleted_at IS NOT NULL AND deleted_sync_run_id IS NOT NULL)
    ),
    CONSTRAINT saas_catalog_source_files_deleted_run_source_fkey
        FOREIGN KEY (deleted_sync_run_id, source_id)
        REFERENCES saas_catalog_sync_runs(id, source_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS saas_catalog_snapshot_versions (
    id UUID PRIMARY KEY,
    supplier TEXT NOT NULL CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro')),
    source_hash TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate','published','superseded','rejected')),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    previous_snapshot_id UUID REFERENCES saas_catalog_snapshot_versions(id) ON DELETE RESTRICT,
    base_published_version_id UUID REFERENCES saas_catalog_snapshot_versions(id) ON DELETE RESTRICT,
    sync_run_id UUID,
    reviewed_by INTEGER REFERENCES saas_usuarios(id) ON DELETE RESTRICT,
    review_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_snapshot_supplier_hash_base
    ON saas_catalog_snapshot_versions(
        supplier, source_hash, COALESCE(base_published_version_id, '00000000-0000-0000-0000-000000000000'::UUID)
    );

ALTER TABLE saas_catalog_sources
    ADD COLUMN IF NOT EXISTS published_version_id UUID
    REFERENCES saas_catalog_snapshot_versions(id) ON DELETE RESTRICT;

ALTER TABLE saas_catalog_sync_runs
    ADD COLUMN IF NOT EXISTS candidate_version_id UUID
    REFERENCES saas_catalog_snapshot_versions(id) ON DELETE RESTRICT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'saas_catalog_snapshot_versions_sync_run_id_fkey'
          AND conrelid = 'saas_catalog_snapshot_versions'::regclass
    ) THEN
        ALTER TABLE saas_catalog_snapshot_versions
            ADD CONSTRAINT saas_catalog_snapshot_versions_sync_run_id_fkey
            FOREIGN KEY (sync_run_id) REFERENCES saas_catalog_sync_runs(id) ON DELETE RESTRICT;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS saas_catalog_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier TEXT NOT NULL CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro')),
    internal_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    usuario_id INTEGER NOT NULL REFERENCES saas_usuarios(id) ON DELETE RESTRICT,
    quote_job_id UUID REFERENCES saas_quote_jobs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','released')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (supplier, internal_id, quote_job_id)
);

CREATE TABLE IF NOT EXISTS saas_exchange_rates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    currency TEXT NOT NULL CHECK (currency IN ('USD','EUR')),
    effective_date DATE NOT NULL,
    mxn_per_unit NUMERIC(18,6) NOT NULL CHECK (mxn_per_unit > 0),
    series_id TEXT NOT NULL,
    source TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    raw_hash TEXT NOT NULL CHECK (raw_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (currency, effective_date)
);

CREATE INDEX IF NOT EXISTS idx_catalog_source_files_source_path
    ON saas_catalog_source_files(source_id, path);
-- Task 6 must use only the latest observation per source and drive item.
CREATE INDEX IF NOT EXISTS idx_catalog_source_files_latest
    ON saas_catalog_source_files(source_id, drive_item_id, discovered_at DESC, id DESC);
ALTER TABLE saas_catalog_source_files
    DROP CONSTRAINT IF EXISTS saas_catalog_source_files_source_id_drive_item_id_e_tag_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_source_files_active_identity
    ON saas_catalog_source_files(source_id, drive_item_id, e_tag)
    WHERE is_deleted IS FALSE;
CREATE INDEX IF NOT EXISTS idx_catalog_sync_runs_source_status
    ON saas_catalog_sync_runs(source_id, status, requested_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_sync_runs_active_source
    ON saas_catalog_sync_runs(source_id)
    WHERE status IN ('requested','running');
CREATE INDEX IF NOT EXISTS idx_catalog_snapshot_versions_supplier_status
    ON saas_catalog_snapshot_versions(supplier, status, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_catalog_reservations_product_status
    ON saas_catalog_reservations(supplier, internal_id, status);
CREATE INDEX IF NOT EXISTS idx_catalog_reservations_usuario
    ON saas_catalog_reservations(usuario_id);
CREATE INDEX IF NOT EXISTS idx_catalog_reservations_quote_job
    ON saas_catalog_reservations(quote_job_id);
CREATE INDEX IF NOT EXISTS idx_exchange_rates_lookup
    ON saas_exchange_rates(currency, effective_date DESC);

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'catalog-sources',
    'catalog-sources',
    FALSE,
    67108864,
    ARRAY[
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/pdf'
    ]
)
ON CONFLICT (id) DO UPDATE SET
    public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'catalog-assets',
    'catalog-assets',
    TRUE,
    8388608,
    ARRAY['image/png','image/jpeg','image/webp']
)
ON CONFLICT (id) DO UPDATE SET
    public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname = 'catalog sources service role only'
    ) THEN
        CREATE POLICY "catalog sources service role only"
            ON storage.objects FOR ALL TO service_role
            USING (
                bucket_id = 'catalog-sources'
                AND name ~ '^[0-9a-f]{64}\.(xlsx|pdf)$'
            )
            WITH CHECK (
                bucket_id = 'catalog-sources'
                AND name ~ '^[0-9a-f]{64}\.(xlsx|pdf)$'
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname = 'catalog assets service role writes'
    ) THEN
        CREATE POLICY "catalog assets service role writes"
            ON storage.objects FOR ALL TO service_role
            USING (
                bucket_id = 'catalog-assets'
                AND name ~ '^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$'
            )
            WITH CHECK (
                bucket_id = 'catalog-assets'
                AND name ~ '^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$'
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname = 'catalog sources deny clients'
    ) THEN
        CREATE POLICY "catalog sources deny clients"
            ON storage.objects AS RESTRICTIVE FOR ALL TO anon, authenticated
            USING (bucket_id <> 'catalog-sources')
            WITH CHECK (bucket_id <> 'catalog-sources');
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION saas_enforce_catalog_snapshot_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_published_version_id UUID;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'candidate' THEN
            RAISE EXCEPTION 'catalog snapshots must be inserted as candidates';
        END IF;

        SELECT published_version_id INTO v_published_version_id
        FROM saas_catalog_sources
        WHERE supplier = NEW.supplier
        FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'catalog source does not exist';
        END IF;
        IF NEW.base_published_version_id IS DISTINCT FROM v_published_version_id THEN
            RAISE EXCEPTION 'catalog candidate base is stale';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.supplier IS DISTINCT FROM OLD.supplier
       OR NEW.source_hash IS DISTINCT FROM OLD.source_hash
       OR NEW.generated_at IS DISTINCT FROM OLD.generated_at
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.previous_snapshot_id IS DISTINCT FROM OLD.previous_snapshot_id
       OR NEW.base_published_version_id IS DISTINCT FROM OLD.base_published_version_id
       OR NEW.sync_run_id IS DISTINCT FROM OLD.sync_run_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'catalog snapshot content is immutable';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT (
           (OLD.status = 'candidate' AND NEW.status IN ('published','superseded','rejected'))
           OR (OLD.status = 'published' AND NEW.status = 'superseded')
       ) THEN
        RAISE EXCEPTION 'invalid catalog snapshot status transition: % to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'enforce_catalog_snapshot_base_on_insert'
          AND tgrelid = 'saas_catalog_snapshot_versions'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER enforce_catalog_snapshot_base_on_insert
            BEFORE INSERT ON saas_catalog_snapshot_versions
            FOR EACH ROW EXECUTE FUNCTION saas_enforce_catalog_snapshot_immutability();
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'enforce_catalog_snapshot_immutability'
          AND tgrelid = 'saas_catalog_snapshot_versions'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER enforce_catalog_snapshot_immutability
            BEFORE UPDATE ON saas_catalog_snapshot_versions
            FOR EACH ROW EXECUTE FUNCTION saas_enforce_catalog_snapshot_immutability();
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION saas_enforce_catalog_sync_run_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT (
               (NEW.status = 'requested'
                AND NEW.request_key IS NULL
                AND NEW.started_at IS NULL)
               OR (NEW.status = 'running'
                   AND NEW.request_key IS NOT NULL
                   AND NEW.started_at IS NOT NULL)
           )
           OR NEW.reviewed_by IS NOT NULL
           OR NEW.candidate_version_id IS NOT NULL
           OR NEW.error_summary IS NOT NULL
           OR NEW.finished_at IS NOT NULL
           OR NEW.reviewed_at IS NOT NULL THEN
            RAISE EXCEPTION 'catalog sync runs must start in a clean state';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.source_id IS DISTINCT FROM OLD.source_id
       OR NEW.trigger_type IS DISTINCT FROM OLD.trigger_type
       OR NEW.request_key IS DISTINCT FROM OLD.request_key
       OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
       OR NEW.requested_at IS DISTINCT FROM OLD.requested_at THEN
        RAISE EXCEPTION 'catalog sync run request identity is immutable';
    END IF;

    IF OLD.status IN ('no_changes','published','rejected','failed') THEN
        RAISE EXCEPTION 'closed catalog sync runs are immutable';
    END IF;

    IF current_user = 'service_role'
       AND (NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by
            OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at) THEN
        RAISE EXCEPTION 'service role cannot record catalog approval';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 'requested' AND NEW.status IN ('running','failed'))
            OR (OLD.status = 'running' AND NEW.status IN ('no_changes','awaiting_approval','failed'))
            OR (OLD.status = 'awaiting_approval' AND NEW.status IN ('published','rejected','failed'))
        ) THEN
            RAISE EXCEPTION 'invalid catalog sync run transition: % to %', OLD.status, NEW.status;
        END IF;

        IF NEW.status IN ('no_changes','published','rejected')
           AND current_user = 'service_role' THEN
            RAISE EXCEPTION 'catalog completion requires a security definer RPC';
        END IF;
    END IF;

    IF NEW.status = 'awaiting_approval' AND NEW.candidate_version_id IS NULL THEN
        RAISE EXCEPTION 'awaiting approval requires a catalog candidate';
    END IF;

    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'enforce_catalog_sync_run_transition'
          AND tgrelid = 'saas_catalog_sync_runs'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER enforce_catalog_sync_run_transition
            BEFORE INSERT OR UPDATE ON saas_catalog_sync_runs
            FOR EACH ROW EXECUTE FUNCTION saas_enforce_catalog_sync_run_transition();
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION saas_start_catalog_sync(
    p_source_id UUID,
    p_trigger_type TEXT,
    p_requested_by INTEGER,
    p_request_key UUID
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_source saas_catalog_sources%ROWTYPE;
    v_existing saas_catalog_sync_runs%ROWTYPE;
    v_run_id UUID := gen_random_uuid();
BEGIN
    IF p_source_id IS NULL
       OR p_request_key IS NULL
       OR p_trigger_type NOT IN ('scheduled','manual')
       OR (p_requested_by IS NOT NULL AND p_requested_by < 1) THEN
        RAISE EXCEPTION 'invalid catalog sync start input';
    END IF;

    SELECT * INTO v_source
    FROM saas_catalog_sources
    WHERE id = p_source_id
    FOR UPDATE;
    IF NOT FOUND OR NOT v_source.enabled THEN
        RAISE EXCEPTION 'catalog source cannot be started';
    END IF;

    SELECT * INTO v_existing
    FROM saas_catalog_sync_runs
    WHERE request_key = p_request_key
    FOR UPDATE;
    IF FOUND THEN
        IF v_existing.source_id IS DISTINCT FROM p_source_id
           OR v_existing.trigger_type IS DISTINCT FROM p_trigger_type
           OR v_existing.requested_by IS DISTINCT FROM p_requested_by
           OR v_existing.status <> 'running' THEN
            RAISE EXCEPTION 'catalog sync start replay conflict';
        END IF;
        RETURN v_existing.id;
    END IF;

    PERFORM 1
    FROM saas_catalog_sync_runs
    WHERE source_id = p_source_id
      AND status IN ('requested','running')
    FOR UPDATE;
    IF FOUND THEN
        RETURN NULL;
    END IF;

    INSERT INTO saas_catalog_sync_runs (
        id, source_id, request_key, trigger_type, status, requested_by, metrics,
        started_at
    ) VALUES (
        v_run_id, p_source_id, p_request_key, p_trigger_type, 'running',
        p_requested_by, '{}'::jsonb, NOW()
    );
    RETURN v_run_id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_recover_stale_catalog_sync_runs(
    p_enabled_suppliers TEXT[]
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_recovered INTEGER;
BEGIN
    IF p_enabled_suppliers IS NULL
       OR CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 5
       OR (SELECT COUNT(*) FROM UNNEST(p_enabled_suppliers))
          <> (SELECT COUNT(DISTINCT value) FROM UNNEST(p_enabled_suppliers) AS enabled_supplier(value))
       OR EXISTS (
           SELECT 1 FROM UNNEST(p_enabled_suppliers) AS enabled_supplier(value)
           WHERE value NOT IN ('cr-global','sonara','sunon','alma','lumbro')
       ) THEN
        RAISE EXCEPTION 'invalid catalog sync supplier whitelist';
    END IF;

    UPDATE saas_catalog_sync_runs r
    SET status = 'failed',
        error_summary = 'lease_expired',
        finished_at = NOW(),
        updated_at = NOW()
    FROM saas_catalog_sources s
    WHERE s.id = r.source_id
      AND s.enabled
      AND s.supplier = ANY(p_enabled_suppliers)
      AND r.status = 'running'
      AND r.started_at < NOW() - INTERVAL '45 minutes';
    GET DIAGNOSTICS v_recovered = ROW_COUNT;
    RETURN v_recovered;
END;
$$;

CREATE OR REPLACE FUNCTION saas_claim_next_catalog_sync(
    p_enabled_suppliers TEXT[]
)
RETURNS TABLE(
    run_id UUID,
    supplier TEXT,
    trigger_type TEXT,
    requested_by INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_run_id UUID;
    v_source_id UUID;
    v_supplier TEXT;
    v_trigger_type TEXT;
    v_requested_by INTEGER;
BEGIN
    IF p_enabled_suppliers IS NULL
       OR CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 5
       OR (SELECT COUNT(*) FROM UNNEST(p_enabled_suppliers))
          <> (SELECT COUNT(DISTINCT value) FROM UNNEST(p_enabled_suppliers) AS enabled_supplier(value))
       OR EXISTS (
           SELECT 1 FROM UNNEST(p_enabled_suppliers) AS enabled_supplier(value)
           WHERE value NOT IN ('cr-global','sonara','sunon','alma','lumbro')
       ) THEN
        RAISE EXCEPTION 'invalid catalog sync supplier whitelist';
    END IF;

    SELECT r.id, r.source_id, s.supplier, r.trigger_type, r.requested_by
    INTO v_run_id, v_source_id, v_supplier, v_trigger_type, v_requested_by
    FROM saas_catalog_sync_runs r
    JOIN saas_catalog_sources s ON s.id = r.source_id
    WHERE r.status = 'requested'
      AND r.trigger_type = 'manual'
      AND s.enabled
      AND s.supplier = ANY(p_enabled_suppliers)
    ORDER BY r.requested_at, r.id
    FOR UPDATE OF r SKIP LOCKED
    LIMIT 1;

    IF v_run_id IS NOT NULL THEN
        UPDATE saas_catalog_sync_runs
        SET status = 'running', started_at = NOW(), updated_at = NOW()
        WHERE id = v_run_id AND status = 'requested';
        RETURN QUERY SELECT v_run_id, v_supplier, v_trigger_type, v_requested_by;
        RETURN;
    END IF;

    SELECT s.id, s.supplier
    INTO v_source_id, v_supplier
    FROM saas_catalog_sources s
    WHERE s.enabled
      AND s.supplier = ANY(p_enabled_suppliers)
      AND NOT EXISTS (
          SELECT 1 FROM saas_catalog_sync_runs active
          WHERE active.source_id = s.id
            AND active.status IN ('requested','running')
      )
      AND NOT EXISTS (
          SELECT 1 FROM saas_catalog_sync_runs recent
          WHERE recent.source_id = s.id
            AND recent.requested_at > NOW() - s.sync_interval
      )
    ORDER BY (
        SELECT MAX(previous.requested_at)
        FROM saas_catalog_sync_runs previous
        WHERE previous.source_id = s.id
    ) NULLS FIRST, s.supplier
    FOR UPDATE OF s SKIP LOCKED
    LIMIT 1;

    IF v_source_id IS NULL THEN
        RETURN;
    END IF;

    v_run_id := gen_random_uuid();
    v_trigger_type := 'scheduled';
    v_requested_by := NULL;
    INSERT INTO saas_catalog_sync_runs (
        id, source_id, request_key, trigger_type, status, requested_by, metrics,
        started_at
    ) VALUES (
        v_run_id, v_source_id, gen_random_uuid(), v_trigger_type, 'running',
        NULL, '{}'::jsonb, NOW()
    );
    RETURN QUERY SELECT v_run_id, v_supplier, v_trigger_type, v_requested_by;
END;
$$;

CREATE OR REPLACE FUNCTION saas_stage_catalog_candidate(
    p_run_id UUID,
    p_source_hash TEXT,
    p_generated_at TIMESTAMPTZ,
    p_payload JSONB,
    p_metrics JSONB,
    p_delta_link TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_run saas_catalog_sync_runs%ROWTYPE;
    v_source saas_catalog_sources%ROWTYPE;
    v_candidate saas_catalog_snapshot_versions%ROWTYPE;
    v_candidate_id UUID := gen_random_uuid();
    v_canonical_hash TEXT;
    v_canonical_generated_at TEXT;
    v_canonical_payload JSONB;
    v_payload_generated_at TIMESTAMPTZ;
BEGIN
    IF p_run_id IS NULL
       OR p_generated_at IS NULL
       OR NOT isfinite(p_generated_at)
       OR p_source_hash IS NULL
       OR p_source_hash !~ '^[0-9A-Fa-f]{64}$'
       OR p_payload IS NULL
       OR jsonb_typeof(p_payload) <> 'object'
       OR jsonb_typeof(p_payload -> 'items') IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_payload -> 'items') > 100000
       OR pg_column_size(p_payload) > 104857600
       OR jsonb_typeof(p_payload -> 'source_hash') IS DISTINCT FROM 'string'
       OR p_payload ->> 'source_hash' !~ '^[0-9A-Fa-f]{64}$'
       OR LOWER(p_payload ->> 'source_hash') IS DISTINCT FROM LOWER(p_source_hash)
       OR jsonb_typeof(p_payload -> 'generated_at') IS DISTINCT FROM 'string'
       OR p_payload ->> 'generated_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
       OR p_metrics IS NULL
       OR jsonb_typeof(p_metrics) <> 'object'
       OR pg_column_size(p_metrics) > 1048576
       OR NULLIF(BTRIM(p_delta_link), '') IS NULL
       OR LENGTH(p_delta_link) > 8192 THEN
        RAISE EXCEPTION 'invalid catalog candidate input';
    END IF;

    BEGIN
        v_payload_generated_at := (p_payload ->> 'generated_at')::TIMESTAMPTZ;
    EXCEPTION
        WHEN invalid_datetime_format OR datetime_field_overflow THEN
            RAISE EXCEPTION 'invalid catalog generated_at';
    END;
    IF v_payload_generated_at IS DISTINCT FROM p_generated_at THEN
        RAISE EXCEPTION 'catalog generated_at does not match';
    END IF;

    v_canonical_hash := LOWER(p_source_hash);
    v_canonical_payload := jsonb_set(
        p_payload, '{source_hash}', to_jsonb(v_canonical_hash), FALSE
    );
    v_canonical_generated_at := to_char(
        p_generated_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
    );
    v_canonical_payload := jsonb_set(
        v_canonical_payload, '{generated_at}', to_jsonb(v_canonical_generated_at), FALSE
    );

    SELECT * INTO v_run
    FROM saas_catalog_sync_runs
    WHERE id = p_run_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog sync run is not stageable';
    END IF;

    SELECT * INTO v_source
    FROM saas_catalog_sources
    WHERE id = v_run.source_id
    FOR UPDATE;
    IF NOT FOUND
       OR p_payload ->> 'supplier' IS DISTINCT FROM v_source.supplier THEN
        RAISE EXCEPTION 'catalog candidate source does not match';
    END IF;

    IF v_run.status = 'awaiting_approval'
       AND v_run.candidate_version_id IS NOT NULL THEN
        SELECT * INTO v_candidate
        FROM saas_catalog_snapshot_versions
        WHERE id = v_run.candidate_version_id
        FOR UPDATE;
        IF NOT FOUND
           OR v_candidate.status <> 'candidate'
           OR v_candidate.sync_run_id IS DISTINCT FROM p_run_id
           OR v_candidate.supplier IS DISTINCT FROM v_source.supplier
           OR v_candidate.source_hash IS DISTINCT FROM v_canonical_hash
           OR v_candidate.generated_at IS DISTINCT FROM p_generated_at
           OR v_candidate.payload IS DISTINCT FROM v_canonical_payload
           OR v_candidate.base_published_version_id IS DISTINCT FROM v_source.published_version_id
           OR v_run.metrics IS DISTINCT FROM p_metrics
           OR v_source.delta_link IS DISTINCT FROM p_delta_link THEN
            RAISE EXCEPTION 'Catalog candidate replay conflict';
        END IF;
        RETURN v_candidate.id;
    END IF;

    IF v_run.status <> 'running'
       OR v_run.candidate_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'catalog sync run is not stageable';
    END IF;

    INSERT INTO saas_catalog_snapshot_versions (
        id, supplier, source_hash, generated_at, status, payload,
        base_published_version_id, sync_run_id
    ) VALUES (
        v_candidate_id, v_source.supplier, v_canonical_hash, p_generated_at,
        'candidate', v_canonical_payload, v_source.published_version_id, v_run.id
    );

    UPDATE saas_catalog_sources
    SET delta_link = p_delta_link,
        updated_at = NOW()
    WHERE id = v_source.id;

    UPDATE saas_catalog_sync_runs
    SET status = 'awaiting_approval',
        candidate_version_id = v_candidate_id,
        metrics = p_metrics,
        finished_at = NOW(),
        updated_at = NOW()
    WHERE id = v_run.id;

    RETURN v_candidate_id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_finish_catalog_sync_no_changes(
    p_run_id UUID,
    p_metrics JSONB,
    p_delta_link TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_run saas_catalog_sync_runs%ROWTYPE;
    v_source saas_catalog_sources%ROWTYPE;
BEGIN
    IF p_run_id IS NULL
       OR p_metrics IS NULL
       OR jsonb_typeof(p_metrics) <> 'object'
       OR pg_column_size(p_metrics) > 1048576
       OR NULLIF(BTRIM(p_delta_link), '') IS NULL
       OR LENGTH(p_delta_link) > 8192 THEN
        RAISE EXCEPTION 'invalid no-change sync result';
    END IF;

    SELECT * INTO v_run
    FROM saas_catalog_sync_runs
    WHERE id = p_run_id
    FOR UPDATE;
    IF NOT FOUND
       OR v_run.status <> 'running'
       OR v_run.candidate_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'catalog sync run is not finishable without changes';
    END IF;

    SELECT * INTO v_source
    FROM saas_catalog_sources
    WHERE id = v_run.source_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog source does not exist';
    END IF;

    UPDATE saas_catalog_sources
    SET delta_link = p_delta_link,
        updated_at = NOW()
    WHERE id = v_source.id;

    UPDATE saas_catalog_sync_runs
    SET status = 'no_changes',
        metrics = p_metrics,
        finished_at = NOW(),
        updated_at = NOW()
    WHERE id = v_run.id;

    RETURN v_run.id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_mark_catalog_source_file_deleted(
    p_source_id UUID,
    p_drive_item_id TEXT,
    p_run_id UUID
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_run saas_catalog_sync_runs%ROWTYPE;
    v_source saas_catalog_sources%ROWTYPE;
    v_file saas_catalog_source_files%ROWTYPE;
BEGIN
    IF p_source_id IS NULL
       OR p_run_id IS NULL
       OR NULLIF(BTRIM(p_drive_item_id), '') IS NULL
       OR LENGTH(p_drive_item_id) > 1024 THEN
        RAISE EXCEPTION 'invalid source file deletion input';
    END IF;

    SELECT * INTO v_run
    FROM saas_catalog_sync_runs
    WHERE id = p_run_id
    FOR UPDATE;
    IF NOT FOUND
       OR v_run.status <> 'running'
       OR v_run.source_id IS DISTINCT FROM p_source_id THEN
        RAISE EXCEPTION 'catalog sync run cannot delete this source file';
    END IF;

    SELECT * INTO v_source
    FROM saas_catalog_sources
    WHERE id = p_source_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog source does not exist';
    END IF;

    SELECT * INTO v_file
    FROM saas_catalog_source_files
    WHERE source_id = p_source_id
      AND drive_item_id = p_drive_item_id
    ORDER BY discovered_at DESC, id DESC
    LIMIT 1
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'latest catalog source file is missing';
    END IF;
    IF v_file.is_deleted THEN
        RETURN v_file.id;
    END IF;

    UPDATE saas_catalog_source_files
    SET is_deleted = TRUE,
        deleted_at = NOW(),
        deleted_sync_run_id = v_run.id,
        last_sync_run_id = v_run.id
    WHERE id = v_file.id;

    RETURN v_file.id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_auto_publish_catalog_snapshot(
    p_candidate_id UUID
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_candidate saas_catalog_snapshot_versions%ROWTYPE;
    v_base saas_catalog_snapshot_versions%ROWTYPE;
    v_source saas_catalog_sources%ROWTYPE;
    v_candidate_count INTEGER;
    v_base_count INTEGER;
    v_invalid_count INTEGER;
    v_distinct_count INTEGER;
    v_candidate_ids JSONB;
    v_base_ids JSONB;
    v_candidate_items JSONB;
    v_base_items JSONB;
    v_candidate_mutable_items JSONB;
    v_base_mutable_items JSONB;
    v_candidate_top_level JSONB;
    v_base_top_level JSONB;
BEGIN
    SELECT * INTO v_candidate
    FROM saas_catalog_snapshot_versions
    WHERE id = p_candidate_id
    FOR UPDATE;
    IF NOT FOUND
       OR v_candidate.status <> 'candidate'
       OR v_candidate.sync_run_id IS NULL
       OR v_candidate.base_published_version_id IS NULL THEN
        RAISE EXCEPTION 'catalog candidate is not auto-publishable';
    END IF;

    SELECT * INTO v_source
    FROM saas_catalog_sources
    WHERE supplier = v_candidate.supplier
    FOR UPDATE;
    IF NOT FOUND
       OR v_candidate.base_published_version_id IS DISTINCT FROM v_source.published_version_id THEN
        RAISE EXCEPTION 'catalog candidate base is stale';
    END IF;

    PERFORM 1
    FROM saas_catalog_sync_runs
    WHERE id = v_candidate.sync_run_id
      AND source_id = v_source.id
      AND candidate_version_id = v_candidate.id
      AND status = 'awaiting_approval'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog sync run is not awaiting approval';
    END IF;

    SELECT * INTO v_base
    FROM saas_catalog_snapshot_versions
    WHERE id = v_candidate.base_published_version_id
      AND supplier = v_candidate.supplier
      AND status = 'published'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'published catalog base is invalid';
    END IF;

    IF jsonb_typeof(v_candidate.payload -> 'items') <> 'array'
       OR jsonb_typeof(v_base.payload -> 'items') <> 'array' THEN
        RAISE EXCEPTION 'catalog payload items must be arrays';
    END IF;

    v_candidate_top_level := v_candidate.payload - 'items' - 'source_hash' - 'generated_at';
    v_base_top_level := v_base.payload - 'items' - 'source_hash' - 'generated_at';
    IF v_candidate_top_level IS DISTINCT FROM v_base_top_level THEN
        RAISE EXCEPTION 'automatic publication forbids other payload metadata changes';
    END IF;

    SELECT COUNT(*),
           COUNT(*) FILTER (
               WHERE jsonb_typeof(item) <> 'object'
                  OR NULLIF(BTRIM(item ->> 'internal_id'), '') IS NULL
           ),
           COUNT(DISTINCT item ->> 'internal_id')
    INTO v_candidate_count, v_invalid_count, v_distinct_count
    FROM jsonb_array_elements(v_candidate.payload -> 'items') AS candidate_items(item);
    IF v_candidate_count = 0
       OR v_invalid_count <> 0
       OR v_distinct_count <> v_candidate_count THEN
        RAISE EXCEPTION 'candidate item IDs must be unique and nonempty';
    END IF;

    SELECT jsonb_agg(item ->> 'internal_id' ORDER BY item ->> 'internal_id'),
           jsonb_object_agg(item ->> 'internal_id', item - 'stock' - 'lead_time'),
           jsonb_object_agg(
               item ->> 'internal_id',
               jsonb_build_object('stock', item -> 'stock', 'lead_time', item -> 'lead_time')
           )
    INTO v_candidate_ids, v_candidate_items, v_candidate_mutable_items
    FROM jsonb_array_elements(v_candidate.payload -> 'items') AS candidate_items(item);

    SELECT COUNT(*),
           COUNT(*) FILTER (
               WHERE jsonb_typeof(item) <> 'object'
                  OR NULLIF(BTRIM(item ->> 'internal_id'), '') IS NULL
           ),
           COUNT(DISTINCT item ->> 'internal_id')
    INTO v_base_count, v_invalid_count, v_distinct_count
    FROM jsonb_array_elements(v_base.payload -> 'items') AS base_items(item);
    IF v_base_count = 0
       OR v_invalid_count <> 0
       OR v_distinct_count <> v_base_count THEN
        RAISE EXCEPTION 'base item IDs must be unique and nonempty';
    END IF;

    SELECT jsonb_agg(item ->> 'internal_id' ORDER BY item ->> 'internal_id'),
           jsonb_object_agg(item ->> 'internal_id', item - 'stock' - 'lead_time'),
           jsonb_object_agg(
               item ->> 'internal_id',
               jsonb_build_object('stock', item -> 'stock', 'lead_time', item -> 'lead_time')
           )
    INTO v_base_ids, v_base_items, v_base_mutable_items
    FROM jsonb_array_elements(v_base.payload -> 'items') AS base_items(item);

    IF v_candidate_ids IS DISTINCT FROM v_base_ids
       OR v_candidate_items IS DISTINCT FROM v_base_items THEN
        RAISE EXCEPTION 'automatic publication permits only stock or lead_time changes';
    END IF;
    IF v_candidate_mutable_items IS NOT DISTINCT FROM v_base_mutable_items THEN
        RAISE EXCEPTION 'automatic publication requires a stock or lead_time change';
    END IF;

    UPDATE saas_catalog_snapshot_versions
    SET status = 'superseded'
    WHERE id = v_base.id;

    UPDATE saas_catalog_snapshot_versions
    SET status = 'published',
        reviewed_by = NULL,
        review_note = 'system:auto-published stock/lead_time-only change',
        reviewed_at = NULL
    WHERE id = v_candidate.id;

    UPDATE saas_catalog_sources
    SET published_version_id = v_candidate.id,
        updated_at = NOW()
    WHERE id = v_source.id;

    UPDATE saas_catalog_sync_runs
    SET status = 'published',
        reviewed_by = NULL,
        reviewed_at = NULL,
        finished_at = NOW(),
        updated_at = NOW()
    WHERE id = v_candidate.sync_run_id;

    RETURN v_candidate.id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_insert_exchange_rates_if_absent(
    p_rates JSONB
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_rate JSONB;
    v_keys TEXT[];
    v_currency TEXT;
    v_effective_date DATE;
    v_mxn_per_unit NUMERIC(18,6);
    v_series_id TEXT;
    v_retrieved_at TIMESTAMPTZ;
    v_existing saas_exchange_rates%ROWTYPE;
    v_inserted INTEGER := 0;
BEGIN
    IF p_rates IS NULL
       OR jsonb_typeof(p_rates) <> 'array'
       OR jsonb_array_length(p_rates) = 0
       OR jsonb_array_length(p_rates) > 1000 THEN
        RAISE EXCEPTION 'exchange rates must be a nonempty bounded array';
    END IF;

    FOR v_rate IN SELECT value FROM jsonb_array_elements(p_rates)
    LOOP
        IF jsonb_typeof(v_rate) <> 'object' THEN
            RAISE EXCEPTION 'each exchange rate must be an object';
        END IF;

        SELECT array_agg(key ORDER BY key)
        INTO v_keys
        FROM jsonb_object_keys(v_rate) AS keys(key);
        IF v_keys IS DISTINCT FROM ARRAY[
            'currency', 'effective_date', 'mxn_per_unit', 'raw_hash',
            'retrieved_at', 'series_id', 'source'
        ]::TEXT[] THEN
            RAISE EXCEPTION 'exchange rate fields are invalid';
        END IF;

        IF jsonb_typeof(v_rate -> 'currency') <> 'string'
           OR jsonb_typeof(v_rate -> 'effective_date') <> 'string'
           OR jsonb_typeof(v_rate -> 'mxn_per_unit') <> 'string'
           OR jsonb_typeof(v_rate -> 'series_id') <> 'string'
           OR jsonb_typeof(v_rate -> 'source') <> 'string'
           OR jsonb_typeof(v_rate -> 'retrieved_at') <> 'string'
           OR jsonb_typeof(v_rate -> 'raw_hash') <> 'string' THEN
            RAISE EXCEPTION 'exchange rate field types are invalid';
        END IF;

        v_currency := v_rate ->> 'currency';
        v_series_id := CASE v_currency
            WHEN 'USD' THEN 'SF43718'
            WHEN 'EUR' THEN 'SF46410'
            ELSE NULL
        END;
        IF v_series_id IS NULL
           OR v_rate ->> 'series_id' IS DISTINCT FROM v_series_id
           OR v_rate ->> 'source' <> 'BANXICO_SIE'
           OR v_rate ->> 'effective_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
           OR v_rate ->> 'mxn_per_unit' !~ '^(0|[1-9][0-9]{0,11})\.[0-9]{6}$'
           OR v_rate ->> 'retrieved_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
           OR v_rate ->> 'raw_hash' !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'exchange rate values are invalid';
        END IF;

        v_effective_date := (v_rate ->> 'effective_date')::DATE;
        v_mxn_per_unit := (v_rate ->> 'mxn_per_unit')::NUMERIC(18,6);
        v_retrieved_at := (v_rate ->> 'retrieved_at')::TIMESTAMPTZ;
        IF v_mxn_per_unit <= 0 THEN
            RAISE EXCEPTION 'exchange rate must be positive';
        END IF;
    END LOOP;

    PERFORM 1
    FROM jsonb_array_elements(p_rates) AS entries(rate)
    GROUP BY rate ->> 'currency', rate ->> 'effective_date'
    HAVING COUNT(*) > 1;
    IF FOUND THEN
        RAISE EXCEPTION 'duplicate exchange rate key in batch';
    END IF;

    FOR v_rate IN
        SELECT value
        FROM jsonb_array_elements(p_rates)
        ORDER BY value ->> 'currency', value ->> 'effective_date'
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended(
                'saas_exchange_rate:' || (v_rate ->> 'currency') || ':' || (v_rate ->> 'effective_date'),
                0
            )
        );
    END LOOP;

    FOR v_rate IN SELECT value FROM jsonb_array_elements(p_rates)
    LOOP
        v_currency := v_rate ->> 'currency';
        v_effective_date := (v_rate ->> 'effective_date')::DATE;
        v_mxn_per_unit := (v_rate ->> 'mxn_per_unit')::NUMERIC(18,6);
        v_series_id := v_rate ->> 'series_id';
        v_retrieved_at := (v_rate ->> 'retrieved_at')::TIMESTAMPTZ;

        SELECT * INTO v_existing
        FROM saas_exchange_rates
        WHERE currency = v_currency
          AND effective_date = v_effective_date
        FOR UPDATE;

        IF FOUND THEN
            IF v_existing.mxn_per_unit IS DISTINCT FROM v_mxn_per_unit
               OR v_existing.series_id IS DISTINCT FROM v_series_id
               OR v_existing.source IS DISTINCT FROM v_rate ->> 'source'
               OR v_existing.retrieved_at IS DISTINCT FROM v_retrieved_at
               OR v_existing.raw_hash IS DISTINCT FROM v_rate ->> 'raw_hash' THEN
                RAISE EXCEPTION 'conflicting exchange rate observation';
            END IF;
        ELSE
            INSERT INTO saas_exchange_rates (
                currency, effective_date, mxn_per_unit, series_id,
                source, retrieved_at, raw_hash
            ) VALUES (
                v_currency, v_effective_date, v_mxn_per_unit, v_series_id,
                v_rate ->> 'source', v_retrieved_at, v_rate ->> 'raw_hash'
            );
            v_inserted := v_inserted + 1;
        END IF;
    END LOOP;

    RETURN v_inserted;
END;
$$;

CREATE OR REPLACE FUNCTION saas_publish_catalog_snapshot(
    p_candidate_id UUID,
    p_reviewed_by INTEGER,
    p_review_note TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_candidate saas_catalog_snapshot_versions%ROWTYPE;
    v_source saas_catalog_sources%ROWTYPE;
BEGIN
    IF p_reviewed_by IS NULL OR NOT EXISTS (
        SELECT 1 FROM saas_usuarios
        WHERE id = p_reviewed_by AND activo IS TRUE AND es_admin IS TRUE
    ) THEN
        RAISE EXCEPTION 'active admin reviewer is required';
    END IF;

    SELECT * INTO v_candidate
    FROM saas_catalog_snapshot_versions
    WHERE id = p_candidate_id
    FOR UPDATE;

    IF NOT FOUND OR v_candidate.status <> 'candidate' OR v_candidate.sync_run_id IS NULL THEN
        RAISE EXCEPTION 'catalog candidate is not publishable';
    END IF;

    SELECT * INTO v_source
    FROM saas_catalog_sources
    WHERE supplier = v_candidate.supplier
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog source does not exist';
    END IF;

    IF v_candidate.base_published_version_id
       IS DISTINCT FROM v_source.published_version_id THEN
        RAISE EXCEPTION 'catalog candidate base is stale';
    END IF;

    PERFORM 1 FROM saas_catalog_sync_runs
    WHERE id = v_candidate.sync_run_id
      AND source_id = v_source.id
      AND candidate_version_id = v_candidate.id
      AND status = 'awaiting_approval'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog sync run is not awaiting approval';
    END IF;

    IF v_source.published_version_id IS NOT NULL
       AND v_source.published_version_id <> v_candidate.id THEN
        PERFORM 1 FROM saas_catalog_snapshot_versions
        WHERE id = v_source.published_version_id AND status = 'published'
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'published catalog pointer is invalid';
        END IF;
        UPDATE saas_catalog_snapshot_versions
        SET status = 'superseded'
        WHERE id = v_source.published_version_id;
    END IF;

    UPDATE saas_catalog_snapshot_versions
    SET status = 'published',
        reviewed_by = p_reviewed_by,
        review_note = p_review_note,
        reviewed_at = NOW()
    WHERE id = v_candidate.id;

    UPDATE saas_catalog_sources
    SET published_version_id = v_candidate.id,
        updated_at = NOW()
    WHERE id = v_source.id;

    UPDATE saas_catalog_sync_runs
    SET status = 'published',
        reviewed_by = p_reviewed_by,
        reviewed_at = NOW(),
        finished_at = NOW(),
        updated_at = NOW()
    WHERE id = v_candidate.sync_run_id;

    RETURN v_candidate.id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_reject_catalog_snapshot(
    p_candidate_id UUID,
    p_reviewed_by INTEGER,
    p_review_note TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_candidate saas_catalog_snapshot_versions%ROWTYPE;
BEGIN
    IF p_reviewed_by IS NULL OR NOT EXISTS (
        SELECT 1 FROM saas_usuarios
        WHERE id = p_reviewed_by AND activo IS TRUE AND es_admin IS TRUE
    ) THEN
        RAISE EXCEPTION 'active admin reviewer is required';
    END IF;

    SELECT * INTO v_candidate
    FROM saas_catalog_snapshot_versions
    WHERE id = p_candidate_id
    FOR UPDATE;

    IF NOT FOUND OR v_candidate.status <> 'candidate' OR v_candidate.sync_run_id IS NULL THEN
        RAISE EXCEPTION 'catalog candidate is not rejectable';
    END IF;

    PERFORM 1 FROM saas_catalog_sync_runs
    WHERE id = v_candidate.sync_run_id
      AND candidate_version_id = v_candidate.id
      AND status = 'awaiting_approval'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog sync run is not awaiting approval';
    END IF;

    UPDATE saas_catalog_snapshot_versions
    SET status = 'rejected',
        reviewed_by = p_reviewed_by,
        review_note = p_review_note,
        reviewed_at = NOW()
    WHERE id = v_candidate.id;

    UPDATE saas_catalog_sync_runs
    SET status = 'rejected',
        reviewed_by = p_reviewed_by,
        reviewed_at = NOW(),
        finished_at = NOW(),
        updated_at = NOW()
    WHERE id = v_candidate.sync_run_id;

    RETURN v_candidate.id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_clone_catalog_candidate_with_asset(
    p_candidate_id UUID,
    p_reviewed_by INTEGER,
    p_asset_object_name TEXT,
    p_json_path TEXT[]
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_candidate saas_catalog_snapshot_versions%ROWTYPE;
    v_new_id UUID := gen_random_uuid();
    v_existing_item JSONB;
    v_new_item JSONB;
    v_new_payload JSONB;
    v_new_hash TEXT;
    v_approved_at TIMESTAMPTZ := NOW();
BEGIN
    IF p_asset_object_name !~ '^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$'
       OR COALESCE(array_length(p_json_path, 1), 0) <> 2
       OR COALESCE(array_lower(p_json_path, 1), 0) <> 1
       OR p_json_path[1] IS DISTINCT FROM 'items'
       OR p_json_path[2] IS NULL
       OR p_json_path[2] !~ '^(0|[1-9][0-9]*)$' THEN
        RAISE EXCEPTION 'invalid catalog item asset target';
    END IF;

    IF p_reviewed_by IS NULL OR NOT EXISTS (
        SELECT 1 FROM saas_usuarios
        WHERE id = p_reviewed_by AND activo IS TRUE AND es_admin IS TRUE
    ) THEN
        RAISE EXCEPTION 'active admin reviewer is required';
    END IF;

    PERFORM 1 FROM storage.objects
    WHERE bucket_id = 'catalog-assets' AND name = p_asset_object_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approved catalog asset does not exist';
    END IF;

    SELECT * INTO v_candidate
    FROM saas_catalog_snapshot_versions
    WHERE id = p_candidate_id
    FOR UPDATE;

    IF NOT FOUND OR v_candidate.status <> 'candidate' OR v_candidate.sync_run_id IS NULL THEN
        RAISE EXCEPTION 'catalog candidate is not cloneable';
    END IF;

    v_existing_item := v_candidate.payload #> p_json_path;
    IF v_existing_item IS NULL
       OR jsonb_typeof(v_existing_item) <> 'object'
       OR jsonb_typeof(v_existing_item -> 'attributes') <> 'object' THEN
        RAISE EXCEPTION 'catalog item asset target does not exist';
    END IF;

    PERFORM 1 FROM saas_catalog_sync_runs
    WHERE id = v_candidate.sync_run_id
      AND candidate_version_id = v_candidate.id
      AND status = 'awaiting_approval'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog sync run is not awaiting approval';
    END IF;

    v_new_item := jsonb_set(
        jsonb_set(
            jsonb_set(
                v_existing_item,
                '{attributes,approved_asset}',
                jsonb_build_object(
                    'bucket', 'catalog-assets',
                    'path', p_asset_object_name,
                    'label', 'Imagen de referencia',
                    'approved', TRUE,
                    'approved_by', p_reviewed_by,
                    'approved_at', v_approved_at
                ),
                TRUE
            ),
            '{image_url}',
            '""'::JSONB,
            TRUE
        ),
        '{image_kind}',
        '"generated_reference"'::JSONB,
        TRUE
    );
    v_new_payload := jsonb_set(v_candidate.payload, p_json_path, v_new_item, FALSE);

    IF v_new_payload IS NOT DISTINCT FROM v_candidate.payload
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','bucket']) <> 'catalog-assets'
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','path']) IS DISTINCT FROM p_asset_object_name
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','label']) <> 'Imagen de referencia'
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','approved']) <> 'true'
       OR v_new_payload #>> (p_json_path || ARRAY['image_kind']) <> 'generated_reference' THEN
        RAISE EXCEPTION 'catalog asset clone did not produce the approved payload';
    END IF;
    v_new_hash := encode(extensions.digest(convert_to(v_new_payload::TEXT, 'UTF8'), 'sha256'), 'hex');
    v_new_payload := jsonb_set(v_new_payload, '{source_hash}', to_jsonb(v_new_hash), TRUE);

    INSERT INTO saas_catalog_snapshot_versions (
        id, supplier, source_hash, generated_at, status, payload,
        previous_snapshot_id, sync_run_id, base_published_version_id, reviewed_by,
        review_note, reviewed_at
    ) VALUES (
        v_new_id, v_candidate.supplier, v_new_hash, v_candidate.generated_at, 'candidate', v_new_payload,
        v_candidate.id, v_candidate.sync_run_id, v_candidate.base_published_version_id, p_reviewed_by,
        'Approved catalog asset ' || p_asset_object_name, v_approved_at
    );

    UPDATE saas_catalog_snapshot_versions
    SET status = 'superseded'
    WHERE id = v_candidate.id;

    UPDATE saas_catalog_sync_runs
    SET candidate_version_id = v_new_id,
        updated_at = NOW()
    WHERE id = v_candidate.sync_run_id;

    RETURN v_new_id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_clone_catalog_candidate_with_image_metadata(
    p_candidate_id UUID,
    p_reviewed_by INTEGER,
    p_asset_object_name TEXT,
    p_json_path TEXT[],
    p_image_kind TEXT,
    p_image_label TEXT,
    p_image_references TEXT[]
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_candidate saas_catalog_snapshot_versions%ROWTYPE;
    v_new_id UUID := gen_random_uuid();
    v_existing_item JSONB;
    v_new_item JSONB;
    v_new_payload JSONB;
    v_new_hash TEXT;
    v_approved_at TIMESTAMPTZ := NOW();
BEGIN
    IF p_asset_object_name !~ '^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$'
       OR COALESCE(array_length(p_json_path, 1), 0) <> 2
       OR COALESCE(array_lower(p_json_path, 1), 0) <> 1
       OR p_json_path[1] IS DISTINCT FROM 'items'
       OR p_json_path[2] IS NULL
       OR p_json_path[2] !~ '^(0|[1-9][0-9]*)$'
       OR p_image_kind NOT IN ('official', 'generated_reference')
       OR COALESCE(array_length(p_image_references, 1), 0) > 20 THEN
        RAISE EXCEPTION 'invalid catalog image metadata';
    END IF;

    IF p_image_kind = 'generated_reference' AND (
        NULLIF(BTRIM(p_image_label), '') IS NULL
        OR LENGTH(BTRIM(p_image_label)) > 300
        OR COALESCE(array_length(p_image_references, 1), 0) = 0
        OR EXISTS (
            SELECT 1
            FROM unnest(p_image_references) AS reference(url)
            WHERE url !~ '^https://[^[:space:]/]+(?:/[^[:space:]]*)?$'
        )
    ) THEN
        RAISE EXCEPTION 'generated catalog image requires label and HTTPS references';
    END IF;

    IF p_reviewed_by IS NULL OR NOT EXISTS (
        SELECT 1 FROM saas_usuarios
        WHERE id = p_reviewed_by AND activo IS TRUE AND es_admin IS TRUE
    ) THEN
        RAISE EXCEPTION 'active admin reviewer is required';
    END IF;

    PERFORM 1 FROM storage.objects
    WHERE bucket_id = 'catalog-assets' AND name = p_asset_object_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approved catalog asset does not exist';
    END IF;

    SELECT * INTO v_candidate
    FROM saas_catalog_snapshot_versions
    WHERE id = p_candidate_id
    FOR UPDATE;

    IF NOT FOUND OR v_candidate.status <> 'candidate' OR v_candidate.sync_run_id IS NULL THEN
        RAISE EXCEPTION 'catalog candidate is not cloneable';
    END IF;

    v_existing_item := v_candidate.payload #> p_json_path;
    IF v_existing_item IS NULL
       OR jsonb_typeof(v_existing_item) <> 'object'
       OR jsonb_typeof(v_existing_item -> 'attributes') <> 'object' THEN
        RAISE EXCEPTION 'catalog item asset target does not exist';
    END IF;

    PERFORM 1 FROM saas_catalog_sync_runs
    WHERE id = v_candidate.sync_run_id
      AND candidate_version_id = v_candidate.id
      AND status = 'awaiting_approval'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog sync run is not awaiting approval';
    END IF;

    v_new_item := jsonb_set(
        jsonb_set(
            jsonb_set(
                v_existing_item,
                '{attributes,approved_asset}',
                jsonb_build_object(
                    'bucket', 'catalog-assets',
                    'path', p_asset_object_name,
                    'label', CASE WHEN p_image_kind = 'generated_reference' THEN 'Imagen de referencia' ELSE 'Imagen oficial' END,
                    'image_kind', p_image_kind,
                    'source_label', NULLIF(BTRIM(p_image_label), ''),
                    'references', to_jsonb(COALESCE(p_image_references, ARRAY[]::TEXT[])),
                    'approved', TRUE,
                    'approved_by', p_reviewed_by,
                    'approved_at', v_approved_at
                ),
                TRUE
            ),
            '{image_url}',
            '""'::JSONB,
            TRUE
        ),
        '{image_kind}',
        to_jsonb(p_image_kind),
        TRUE
    );
    v_new_payload := jsonb_set(v_candidate.payload, p_json_path, v_new_item, FALSE);

    IF v_new_payload IS NOT DISTINCT FROM v_candidate.payload
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','bucket']) <> 'catalog-assets'
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','path']) IS DISTINCT FROM p_asset_object_name
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','image_kind']) IS DISTINCT FROM p_image_kind
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','approved']) <> 'true'
       OR v_new_payload #>> (p_json_path || ARRAY['image_kind']) IS DISTINCT FROM p_image_kind THEN
        RAISE EXCEPTION 'catalog image metadata clone did not produce the approved payload';
    END IF;
    v_new_hash := encode(extensions.digest(convert_to(v_new_payload::TEXT, 'UTF8'), 'sha256'), 'hex');
    v_new_payload := jsonb_set(v_new_payload, '{source_hash}', to_jsonb(v_new_hash), TRUE);

    INSERT INTO saas_catalog_snapshot_versions (
        id, supplier, source_hash, generated_at, status, payload,
        previous_snapshot_id, sync_run_id, base_published_version_id, reviewed_by,
        review_note, reviewed_at
    ) VALUES (
        v_new_id, v_candidate.supplier, v_new_hash, v_candidate.generated_at, 'candidate', v_new_payload,
        v_candidate.id, v_candidate.sync_run_id, v_candidate.base_published_version_id, p_reviewed_by,
        'Approved catalog asset ' || p_asset_object_name, v_approved_at
    );

    UPDATE saas_catalog_snapshot_versions
    SET status = 'superseded'
    WHERE id = v_candidate.id;

    UPDATE saas_catalog_sync_runs
    SET candidate_version_id = v_new_id,
        updated_at = NOW()
    WHERE id = v_candidate.sync_run_id;

    RETURN v_new_id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_catalog_reservation_summary(
    p_supplier TEXT,
    p_usuario_id INTEGER
)
RETURNS TABLE (
    internal_id TEXT,
    reserved_quantity NUMERIC(18,6),
    reserved_by_others BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF p_supplier IS NULL
       OR p_supplier NOT IN ('cr-global','sonara','sunon','alma','lumbro') THEN
        RAISE EXCEPTION 'invalid catalog supplier';
    END IF;
    IF p_usuario_id IS NULL OR p_usuario_id <= 0 THEN
        RAISE EXCEPTION 'invalid catalog user';
    END IF;

    RETURN QUERY
    SELECT reservations.internal_id,
           SUM(reservations.quantity)::NUMERIC(18,6),
           BOOL_OR(reservations.usuario_id <> p_usuario_id)
    FROM saas_catalog_reservations AS reservations
    WHERE reservations.supplier = p_supplier
      AND reservations.status = 'active'
    GROUP BY reservations.internal_id
    ORDER BY reservations.internal_id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_reserve_catalog_items(
    p_usuario_id INTEGER,
    p_quote_job_id UUID,
    p_supplier TEXT,
    p_lines JSONB
)
RETURNS TABLE (
    internal_id TEXT,
    reserved_before NUMERIC(18,6),
    available_before NUMERIC(18,6),
    insufficient BOOLEAN,
    reserved_by_others BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    job saas_quote_jobs%ROWTYPE;
    v_line JSONB;
    v_internal_id TEXT;
    v_sku TEXT;
    v_quantity NUMERIC(18,6);
    v_stock NUMERIC(18,6);
    v_reserved_before NUMERIC(18,6);
    v_reserved_by_others BOOLEAN;
BEGIN
    IF p_usuario_id IS NULL OR p_usuario_id <= 0 THEN
        RAISE EXCEPTION 'invalid catalog user';
    END IF;
    IF p_quote_job_id IS NULL THEN
        RAISE EXCEPTION 'invalid quote job';
    END IF;
    IF p_supplier IS NULL
       OR p_supplier NOT IN ('cr-global','sonara','sunon','alma','lumbro') THEN
        RAISE EXCEPTION 'invalid catalog supplier';
    END IF;
    IF p_lines IS NULL
       OR jsonb_typeof(p_lines) <> 'array'
       OR jsonb_array_length(p_lines) = 0
       OR jsonb_array_length(p_lines) > 500 THEN
        RAISE EXCEPTION 'invalid catalog reservation lines';
    END IF;

    SELECT jobs.*
    INTO job
    FROM saas_quote_jobs AS jobs
    WHERE jobs.id = p_quote_job_id
    FOR UPDATE;

    IF NOT FOUND
       OR job.usuario_id IS DISTINCT FROM p_usuario_id
       OR job.status <> 'draft' THEN
        RAISE EXCEPTION 'invalid quote job for catalog reservation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM saas_catalog_reservations AS reservations
        WHERE reservations.quote_job_id = p_quote_job_id
          AND reservations.supplier = p_supplier
    ) THEN
        RAISE EXCEPTION 'quote job already has catalog reservations';
    END IF;

    FOR v_line IN
        SELECT line
        FROM jsonb_array_elements(p_lines) AS entries(line)
    LOOP
        IF jsonb_typeof(v_line) <> 'object'
           OR (SELECT COUNT(*) FROM jsonb_object_keys(v_line)) <> 4
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(v_line) AS keys(key)
               WHERE key NOT IN ('internal_id','sku','quantity','stock')
           )
           OR jsonb_typeof(v_line -> 'internal_id') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_line -> 'sku') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_line -> 'quantity') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_line -> 'stock') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'invalid catalog reservation line';
        END IF;

        v_internal_id := v_line ->> 'internal_id';
        v_sku := v_line ->> 'sku';
        IF NULLIF(BTRIM(v_internal_id), '') IS NULL
           OR v_internal_id IS DISTINCT FROM BTRIM(v_internal_id)
           OR LENGTH(v_internal_id) > 300
           OR v_internal_id ~ '[[:cntrl:]]'
           OR NULLIF(BTRIM(v_sku), '') IS NULL
           OR v_sku IS DISTINCT FROM BTRIM(v_sku)
           OR LENGTH(v_sku) > 300
           OR v_sku ~ '[[:cntrl:]]'
           OR v_line ->> 'quantity' !~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,6})?$'
           OR v_line ->> 'stock' !~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,6})?$' THEN
            RAISE EXCEPTION 'invalid catalog reservation line values';
        END IF;

        v_quantity := (v_line ->> 'quantity')::NUMERIC(18,6);
        v_stock := (v_line ->> 'stock')::NUMERIC(18,6);
        IF v_quantity <= 0 OR v_stock < 0 THEN
            RAISE EXCEPTION 'invalid catalog reservation quantities';
        END IF;
    END LOOP;

    IF (
        SELECT COUNT(DISTINCT line ->> 'internal_id')
        FROM jsonb_array_elements(p_lines) AS entries(line)
    ) <> jsonb_array_length(p_lines) THEN
        RAISE EXCEPTION 'duplicate catalog reservation internal_id';
    END IF;

    FOR v_internal_id IN
        SELECT line ->> 'internal_id'
        FROM jsonb_array_elements(p_lines) AS entries(line)
        ORDER BY line ->> 'internal_id'
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended(p_supplier || ':' || v_internal_id, 0)
        );
    END LOOP;

    FOR v_line IN
        SELECT line
        FROM jsonb_array_elements(p_lines) AS entries(line)
        ORDER BY line ->> 'internal_id'
    LOOP
        v_internal_id := v_line ->> 'internal_id';
        v_sku := v_line ->> 'sku';
        v_quantity := (v_line ->> 'quantity')::NUMERIC(18,6);
        v_stock := (v_line ->> 'stock')::NUMERIC(18,6);

        SELECT COALESCE(SUM(reservations.quantity), 0)::NUMERIC(18,6),
               COALESCE(BOOL_OR(reservations.usuario_id <> p_usuario_id), FALSE)
        INTO v_reserved_before, v_reserved_by_others
        FROM saas_catalog_reservations AS reservations
        WHERE reservations.supplier = p_supplier
          AND reservations.internal_id = v_internal_id
          AND reservations.status = 'active';

        INSERT INTO saas_catalog_reservations (
            supplier,
            internal_id,
            sku,
            quantity,
            usuario_id,
            quote_job_id,
            status
        )
        VALUES (
            p_supplier,
            v_internal_id,
            v_sku,
            v_quantity,
            p_usuario_id,
            p_quote_job_id,
            'active'
        );

        internal_id := v_internal_id;
        reserved_before := v_reserved_before;
        available_before := GREATEST(v_stock - v_reserved_before, 0)::NUMERIC(18,6);
        insufficient := v_quantity > available_before;
        reserved_by_others := v_reserved_by_others;
        RETURN NEXT;
    END LOOP;
END;
$$;

ALTER TABLE saas_catalog_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_catalog_source_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_catalog_sync_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_catalog_snapshot_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_catalog_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_exchange_rates ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE saas_catalog_sources FROM anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_source_files FROM anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_sync_runs FROM anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_snapshot_versions FROM anon, authenticated;
REVOKE ALL ON TABLE saas_catalog_reservations FROM anon, authenticated;
REVOKE ALL ON TABLE saas_exchange_rates FROM anon, authenticated;

REVOKE ALL ON TABLE saas_catalog_sources FROM service_role;
REVOKE ALL ON TABLE saas_catalog_source_files FROM service_role;
REVOKE ALL ON TABLE saas_catalog_sync_runs FROM service_role;
REVOKE ALL ON TABLE saas_catalog_snapshot_versions FROM service_role;
REVOKE ALL ON TABLE saas_catalog_reservations FROM service_role;
REVOKE ALL ON TABLE saas_exchange_rates FROM service_role;

GRANT SELECT ON TABLE saas_catalog_sources TO service_role;
GRANT INSERT (supplier, label, adapter, graph_drive_id, graph_root_item_id, delta_link, sync_interval, enabled)
    ON TABLE saas_catalog_sources TO service_role;
GRANT UPDATE (label, adapter, graph_drive_id, graph_root_item_id, sync_interval, enabled, updated_at)
    ON TABLE saas_catalog_sources TO service_role;
GRANT SELECT ON TABLE saas_catalog_source_files TO service_role;
GRANT INSERT (source_id, drive_item_id, path, e_tag, c_tag, size_bytes, sha256, mime_type,
              private_object_path, validation_status, validation_summary, last_sync_run_id,
              discovered_at, validated_at)
    ON TABLE saas_catalog_source_files TO service_role;
GRANT UPDATE (validation_status, validation_summary, last_sync_run_id, validated_at)
    ON TABLE saas_catalog_source_files TO service_role;
GRANT SELECT ON TABLE saas_catalog_sync_runs TO service_role;
GRANT INSERT (source_id, trigger_type, requested_by, metrics)
    ON TABLE saas_catalog_sync_runs TO service_role;
GRANT UPDATE (status, candidate_version_id, metrics, error_summary, started_at, finished_at, updated_at)
    ON TABLE saas_catalog_sync_runs TO service_role;
GRANT SELECT ON TABLE saas_catalog_snapshot_versions TO service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE saas_catalog_reservations TO service_role;
GRANT SELECT ON TABLE saas_exchange_rates TO service_role;

REVOKE ALL ON FUNCTION saas_stage_catalog_candidate(UUID, TEXT, TIMESTAMPTZ, JSONB, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_start_catalog_sync(UUID, TEXT, INTEGER, UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_recover_stale_catalog_sync_runs(TEXT[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_claim_next_catalog_sync(TEXT[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_finish_catalog_sync_no_changes(UUID, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_mark_catalog_source_file_deleted(UUID, TEXT, UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_auto_publish_catalog_snapshot(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_insert_exchange_rates_if_absent(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_publish_catalog_snapshot(UUID, INTEGER, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_reject_catalog_snapshot(UUID, INTEGER, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_clone_catalog_candidate_with_asset(UUID, INTEGER, TEXT, TEXT[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_clone_catalog_candidate_with_image_metadata(UUID, INTEGER, TEXT, TEXT[], TEXT, TEXT, TEXT[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_catalog_reservation_summary(TEXT, INTEGER) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_reserve_catalog_items(INTEGER, UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION saas_stage_catalog_candidate(UUID, TEXT, TIMESTAMPTZ, JSONB, JSONB, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION saas_start_catalog_sync(UUID, TEXT, INTEGER, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION saas_recover_stale_catalog_sync_runs(TEXT[]) TO service_role;
GRANT EXECUTE ON FUNCTION saas_claim_next_catalog_sync(TEXT[]) TO service_role;
GRANT EXECUTE ON FUNCTION saas_finish_catalog_sync_no_changes(UUID, JSONB, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION saas_mark_catalog_source_file_deleted(UUID, TEXT, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION saas_auto_publish_catalog_snapshot(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION saas_insert_exchange_rates_if_absent(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION saas_publish_catalog_snapshot(UUID, INTEGER, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION saas_reject_catalog_snapshot(UUID, INTEGER, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION saas_clone_catalog_candidate_with_asset(UUID, INTEGER, TEXT, TEXT[]) TO service_role;
GRANT EXECUTE ON FUNCTION saas_clone_catalog_candidate_with_image_metadata(UUID, INTEGER, TEXT, TEXT[], TEXT, TEXT, TEXT[]) TO service_role;
GRANT EXECUTE ON FUNCTION saas_catalog_reservation_summary(TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION saas_reserve_catalog_items(INTEGER, UUID, TEXT, JSONB) TO service_role;

-- Guarded RLS hardening for legacy and repository job tables.

DO $$
BEGIN
    IF to_regclass('public.jobs') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY';
        EXECUTE 'REVOKE ALL ON TABLE public.jobs FROM anon, authenticated';
        EXECUTE 'GRANT ALL ON TABLE public.jobs TO service_role';
    END IF;

    IF to_regclass('public.saas_quote_jobs') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.saas_quote_jobs ENABLE ROW LEVEL SECURITY';
        EXECUTE 'REVOKE ALL ON TABLE public.saas_quote_jobs FROM anon, authenticated';
        EXECUTE 'GRANT ALL ON TABLE public.saas_quote_jobs TO service_role';
    END IF;
END;
$$;

ALTER TABLE public.saas_quote_jobs
    ADD COLUMN IF NOT EXISTS attempt_token UUID,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

ALTER TABLE public.saas_quote_jobs
    ALTER COLUMN input_path DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_quote_jobs_processing_lease
    ON public.saas_quote_jobs(lease_expires_at)
    WHERE status = 'processing';

CREATE OR REPLACE FUNCTION saas_reserve_mixed_cart(
    p_usuario_id INTEGER,
    p_quote_job_id UUID,
    p_groups JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_group JSONB;
    v_item JSONB;
    v_catalog TEXT;
    v_identity TEXT;
    v_sku TEXT;
    v_quantity NUMERIC;
    v_stock NUMERIC;
    v_total_lines INTEGER := 0;
    v_result JSONB := '[]'::JSONB;
    v_seen_catalogs TEXT[] := ARRAY[]::TEXT[];
    v_row RECORD;
    v_reserved_before NUMERIC(20, 6);
    v_available_before NUMERIC(20, 6);
    v_reserved_by_others BOOLEAN;
    v_insufficient BOOLEAN;
BEGIN
    PERFORM 1
    FROM saas_quote_jobs
    WHERE id = p_quote_job_id
      AND usuario_id = p_usuario_id
      AND status = 'draft'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'mixed quote job is invalid';
    END IF;

    IF p_groups IS NULL OR jsonb_typeof(p_groups) <> 'array'
       OR jsonb_array_length(p_groups) NOT BETWEEN 0 AND 7 THEN
        RAISE EXCEPTION 'mixed groups must be a bounded array';
    END IF;

    CREATE TEMP TABLE IF NOT EXISTS mixed_reservation_lines (
        catalog TEXT NOT NULL,
        identity TEXT NOT NULL,
        sku TEXT NOT NULL,
        quantity NUMERIC(20, 6) NOT NULL,
        stock NUMERIC(20, 6) NOT NULL,
        PRIMARY KEY (catalog, identity)
    ) ON COMMIT DROP;

    DELETE FROM pg_temp.mixed_reservation_lines;

    FOR v_group IN SELECT value FROM jsonb_array_elements(p_groups)
    LOOP
        IF jsonb_typeof(v_group) <> 'object'
           OR NOT (v_group ?& ARRAY['catalog','items'])
           OR (v_group - ARRAY['catalog','items']::TEXT[]) <> '{}'::JSONB
           OR jsonb_typeof(v_group -> 'catalog') <> 'string'
           OR jsonb_typeof(v_group -> 'items') <> 'array' THEN
            RAISE EXCEPTION 'mixed group has invalid shape';
        END IF;

        v_catalog := btrim(v_group ->> 'catalog');
        IF v_catalog NOT IN ('tarkett','offiho','cr-global','sonara','sunon','alma','lumbro') THEN
            RAISE EXCEPTION 'mixed catalog is invalid';
        END IF;
        IF v_catalog = ANY(v_seen_catalogs) THEN
            RAISE EXCEPTION 'mixed catalog is duplicated';
        END IF;
        v_seen_catalogs := array_append(v_seen_catalogs, v_catalog);
        IF jsonb_array_length(v_group -> 'items') = 0 THEN
            RAISE EXCEPTION 'mixed reservation group is empty';
        END IF;
        v_total_lines := v_total_lines + jsonb_array_length(v_group -> 'items');
        IF v_total_lines > 500 THEN
            RAISE EXCEPTION 'mixed reservation line count is invalid';
        END IF;

        FOR v_item IN SELECT value FROM jsonb_array_elements(v_group -> 'items')
        LOOP
            IF jsonb_typeof(v_item) <> 'object'
               OR NOT (v_item ?& ARRAY['identity','quantity','sku','stock'])
               OR (v_item - ARRAY['identity','quantity','sku','stock']::TEXT[]) <> '{}'::JSONB
               OR jsonb_typeof(v_item -> 'identity') <> 'string'
               OR jsonb_typeof(v_item -> 'quantity') <> 'string'
               OR jsonb_typeof(v_item -> 'sku') <> 'string'
               OR jsonb_typeof(v_item -> 'stock') <> 'string' THEN
                RAISE EXCEPTION 'mixed reservation item has invalid shape';
            END IF;

            v_identity := btrim(v_item ->> 'identity');
            v_sku := btrim(v_item ->> 'sku');
            IF char_length(v_identity) NOT BETWEEN 1 AND 500
               OR v_identity ~ '[[:cntrl:]]'
               OR char_length(v_sku) > 500
               OR v_sku ~ '[[:cntrl:]]' THEN
                RAISE EXCEPTION 'mixed reservation identity is invalid';
            END IF;
            IF v_sku = '' AND v_catalog NOT IN ('sonara','lumbro') THEN
                RAISE EXCEPTION 'mixed reservation sku is invalid';
            END IF;
            IF (v_item ->> 'quantity') !~ '^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?$'
               OR (v_item ->> 'stock') !~ '^(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,6})?$' THEN
                RAISE EXCEPTION 'mixed reservation decimal is invalid';
            END IF;
            v_quantity := (v_item ->> 'quantity')::NUMERIC;
            v_stock := (v_item ->> 'stock')::NUMERIC;
            IF v_quantity <= 0 OR v_quantity > 1000000
               OR v_stock < 0 OR v_stock > 1000000000 THEN
                RAISE EXCEPTION 'mixed reservation decimal is out of range';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_temp.mixed_reservation_lines
                WHERE catalog = v_catalog AND identity = v_identity
            ) THEN
                RAISE EXCEPTION 'mixed reservation identity is duplicated';
            END IF;
            INSERT INTO pg_temp.mixed_reservation_lines
                (catalog, identity, sku, quantity, stock)
            VALUES (v_catalog, v_identity, v_sku, v_quantity, v_stock);
        END LOOP;
    END LOOP;

    IF EXISTS (SELECT 1 FROM saas_tarkett_reservations WHERE quote_job_id = p_quote_job_id)
       OR EXISTS (SELECT 1 FROM saas_offiho_reservations WHERE quote_job_id = p_quote_job_id)
       OR EXISTS (SELECT 1 FROM saas_catalog_reservations WHERE quote_job_id = p_quote_job_id) THEN
        RAISE EXCEPTION 'mixed quote job already has reservations';
    END IF;

    IF v_total_lines = 0 THEN
        RETURN '[]'::JSONB;
    END IF;

    FOR v_catalog, v_identity IN
        SELECT catalog, identity
        FROM pg_temp.mixed_reservation_lines
        ORDER BY catalog, identity
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended(v_catalog || ':' || v_identity, 0)
        );
    END LOOP;

    FOR v_row IN
        SELECT catalog, identity, sku, quantity, stock
        FROM pg_temp.mixed_reservation_lines
        ORDER BY catalog, identity
    LOOP
        IF v_row.catalog = 'tarkett' THEN
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_tarkett_reservations
            WHERE product_code = v_row.identity AND status = 'active';
        ELSIF v_row.catalog = 'offiho' THEN
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_offiho_reservations
            WHERE product_code = v_row.identity AND status = 'active';
        ELSE
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_catalog_reservations
            WHERE supplier = v_row.catalog
              AND internal_id = v_row.identity
              AND status = 'active';
        END IF;

        v_available_before := GREATEST(v_row.stock - v_reserved_before, 0);
        v_insufficient := v_row.quantity > v_available_before;

        IF v_row.catalog = 'tarkett' THEN
            INSERT INTO saas_tarkett_reservations
                (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
            VALUES
                (gen_random_uuid(), p_usuario_id, p_quote_job_id, v_row.identity,
                 v_row.quantity, 'active', NOW(), NOW());
        ELSIF v_row.catalog = 'offiho' THEN
            INSERT INTO saas_offiho_reservations
                (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
            VALUES
                (gen_random_uuid(), p_usuario_id, p_quote_job_id, v_row.identity,
                 v_row.quantity, 'active', NOW(), NOW());
        ELSE
            INSERT INTO saas_catalog_reservations
                (supplier, internal_id, sku, quantity, usuario_id, quote_job_id,
                 status, created_at, updated_at)
            VALUES
                (v_row.catalog, v_row.identity, v_row.sku, v_row.quantity,
                 p_usuario_id, p_quote_job_id, 'active', NOW(), NOW());
        END IF;

        v_result := v_result || jsonb_build_array(jsonb_build_object(
            'catalog', v_row.catalog,
            'identity', v_row.identity,
            'reserved_before', to_char(
                v_reserved_before, 'FM999999999999999999999999990.000000'
            ),
            'available_before', to_char(
                v_available_before, 'FM999999999999999999999999990.000000'
            ),
            'insufficient', v_insufficient,
            'reserved_by_others', v_reserved_by_others
        ));
    END LOOP;

    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION saas_release_mixed_cart(p_quote_job_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_tarkett INTEGER;
    v_offiho INTEGER;
    v_supplier INTEGER;
    v_job_status TEXT;
    v_catalog TEXT;
    v_identity TEXT;
BEGIN
    SELECT status
    INTO v_job_status
    FROM saas_quote_jobs
    WHERE id = p_quote_job_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'mixed quote job is invalid';
    END IF;

    CREATE TEMP TABLE IF NOT EXISTS mixed_release_lines (
        catalog TEXT NOT NULL,
        identity TEXT NOT NULL,
        PRIMARY KEY (catalog, identity)
    ) ON COMMIT DROP;

    DELETE FROM pg_temp.mixed_release_lines;

    INSERT INTO pg_temp.mixed_release_lines (catalog, identity)
    SELECT 'tarkett', product_code
    FROM saas_tarkett_reservations
    WHERE quote_job_id = p_quote_job_id AND status = 'active'
    ON CONFLICT (catalog, identity) DO NOTHING;

    INSERT INTO pg_temp.mixed_release_lines (catalog, identity)
    SELECT 'offiho', product_code
    FROM saas_offiho_reservations
    WHERE quote_job_id = p_quote_job_id AND status = 'active'
    ON CONFLICT (catalog, identity) DO NOTHING;

    INSERT INTO pg_temp.mixed_release_lines (catalog, identity)
    SELECT supplier, internal_id
    FROM saas_catalog_reservations
    WHERE quote_job_id = p_quote_job_id AND status = 'active'
    ON CONFLICT (catalog, identity) DO NOTHING;

    FOR v_catalog, v_identity IN
        SELECT catalog, identity
        FROM pg_temp.mixed_release_lines
        ORDER BY catalog, identity
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended(v_catalog || ':' || v_identity, 0)
        );
    END LOOP;

    IF v_job_status = 'draft' THEN
        UPDATE saas_quote_jobs
        SET status = 'failed',
            error_message = COALESCE(NULLIF(error_message, ''), 'mixed reservations released'),
            updated_at = NOW()
        WHERE id = p_quote_job_id;
    END IF;

    UPDATE saas_tarkett_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_tarkett = ROW_COUNT;

    UPDATE saas_offiho_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_offiho = ROW_COUNT;

    UPDATE saas_catalog_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_supplier = ROW_COUNT;

    RETURN jsonb_build_object(
        'tarkett', v_tarkett, 'offiho', v_offiho, 'supplier', v_supplier
    );
END;
$$;

REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM anon;
REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) TO service_role;

REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM anon;
REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM authenticated;
GRANT EXECUTE ON FUNCTION saas_release_mixed_cart(UUID) TO service_role;
