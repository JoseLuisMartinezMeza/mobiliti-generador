-- ============================================================
-- Tablas para Mobiliti SaaS
-- Copiar TODO este contenido y pegar en SQL Editor de Supabase
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

-- Indices para busquedas rapidas
CREATE INDEX IF NOT EXISTS idx_usuarios_email ON saas_usuarios(email);
CREATE INDEX IF NOT EXISTS idx_suscripciones_usuario ON saas_suscripciones(usuario_id);
CREATE INDEX IF NOT EXISTS idx_sesiones_token ON saas_sesiones(token_jwt);
