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
    version TEXT NOT NULL,
    download_url TEXT NOT NULL,
    release_notes TEXT,
    force_update BOOLEAN DEFAULT FALSE,
    activa BOOLEAN DEFAULT TRUE,
    creado TIMESTAMPTZ DEFAULT NOW(),
    actualizado TIMESTAMPTZ DEFAULT NOW()
);

-- Insertar versión actual (v1.5.6)
INSERT INTO saas_versiones (version, download_url, release_notes, force_update)
VALUES (
    '1.5.6',
    'https://github.com/REMOVED_PASSWORD/mobiliti-generador/releases/download/v1.5.6/Mobiliti_Generador.exe',
    'Fix: eliminado subprocess en modo .exe para evitar crash _MEI Tcl data',
    FALSE
)
ON CONFLICT DO NOTHING;

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
