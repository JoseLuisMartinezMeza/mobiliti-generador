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

-- Índices útiles
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

-- Insertar versión actual (v1.5.6)
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

-- Solo debe haber una versión activa: desactivar las anteriores automáticamente
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

-- Índice útil
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
