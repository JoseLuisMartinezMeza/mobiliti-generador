-- ============================================================
-- SQL COMPLETO - Mobiliti SaaS v2
-- INCLUYE: Base de datos + Todo el sistema de auth/tokens
-- ============================================================

-- 1. ACTIVAR UUID
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. TABLA DE USUARIOS (Auth + Datos)
CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    nombre VARCHAR(255),
    password_hash VARCHAR(255) NOT NULL,
    rol VARCHAR(20) DEFAULT 'usuario',
    activo BOOLEAN DEFAULT true,
    ultimo_acceso TIMESTAMP WITH TIME ZONE,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    actualizado_en TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. TABLA DE LICENCIAS (SaaS)
CREATE TABLE IF NOT EXISTS licencias (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    codigo VARCHAR(100) NOT NULL UNIQUE,
    tipo VARCHAR(50) DEFAULT 'anual',
    estado VARCHAR(20) DEFAULT 'activa',
    activada_en TIMESTAMP WITH TIME ZONE,
    expira_en TIMESTAMP WITH TIME ZONE,
    equipo_fingerprint VARCHAR(255),
    usos INTEGER DEFAULT 0,
    max_usos INTEGER DEFAULT 3,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 4. TABLA DE SUSCRIPCIONES (Alternativa)
CREATE TABLE IF NOT EXISTS suscripciones (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    tipo VARCHAR(50) DEFAULT 'anual',
    estado VARCHAR(20) DEFAULT 'activa',
    fecha_inicio TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    fecha_fin TIMESTAMP WITH TIME ZONE,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 5. TABLA DE EQUIPOS (Activaciones)
CREATE TABLE IF NOT EXISTS equipos (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    nombre VARCHAR(255),
    sistema VARCHAR(50),
    fingerprint VARCHAR(255),
    activo BOOLEAN DEFAULT true,
    ultimo_acceso TIMESTAMP WITH TIME ZONE,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 6. TABLA DE LOGS DE USO
CREATE TABLE IF NOT EXISTS logs_uso (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios(id),
    accion VARCHAR(50),
    detalle JSONB,
    ip VARCHAR(45),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 7. VISTA DE USUARIOS CON SUSCRIPCION ACTIVA
CREATE OR REPLACE VIEW usuarios_activos AS
SELECT 
    u.*,
    s.estado as suscripcion_estado,
    s.fecha_fin as suscripcion_expira
FROM usuarios u
LEFT JOIN suscripciones s ON u.id = s.usuario_id
WHERE u.activo = true 
    AND (s.estado = 'activa' OR s.estado IS NULL)
    AND (s.fecha_fin IS NULL OR s.fecha_fin > NOW());

-- 8. FUNCION PARA VERIFICAR SUSCRIPCION
CREATE OR REPLACE FUNCTION verificar_suscripcion(p_usuario_id INTEGER)
RETURNS TABLE(
    activa BOOLEAN,
    tipo VARCHAR,
    dias_restantes INTEGER,
    mensaje TEXT
) AS $$
DECLARE
    v_suscripcion RECORD;
    v_dias INTEGER;
BEGIN
    SELECT * INTO v_suscripcion FROM suscripciones 
    WHERE usuario_id = p_usuario_id AND estado = 'activa' 
    ORDER BY id DESC LIMIT 1;
    
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'ninguna'::VARCHAR, 0::INTEGER, 'No hay suscripcion activa'::TEXT;
        RETURN;
    END IF;
    
    IF v_suscripcion.fecha_fin IS NULL THEN
        RETURN QUERY SELECT true, v_suscripcion.tipo, 9999::INTEGER, 'Suscripcion ilimitada'::TEXT;
        RETURN;
    END IF;
    
    v_dias := EXTRACT(DAY FROM (v_suscripcion.fecha_fin - NOW()));
    
    IF v_dias > 0 THEN
        RETURN QUERY SELECT true, v_suscripcion.tipo, v_dias, 'OK'::TEXT;
    ELSE
        RETURN QUERY SELECT false, v_suscripcion.tipo, 0, 'Suscripcion vencida'::TEXT;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 9. CREAR ADMIN INICIAL (cambiar contraseña en produccion)
INSERT INTO usuarios (email, nombre, password_hash, rol, activo)
VALUES (
    '***REMOVED***',
    'Jose Luis Martinez',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewKyNiAYMyzJ/I1i',
    'admin',
    true
)
ON CONFLICT (email) DO NOTHING;

-- 10. CREAR SUSCRIPCION ILIMITADA PARA ADMIN
INSERT INTO suscripciones (usuario_id, tipo, estado, fecha_fin)
SELECT id, 'anual', 'activa', NULL
FROM usuarios 
WHERE email = '***REMOVED***'
ON CONFLICT DO NOTHING;

-- 11. POLITICAS RLS (si usas Supabase con RLS)
ALTER TABLE usuarios ENABLE ROW LEVEL SECURITY;
ALTER TABLE licencias ENABLE ROW LEVEL SECURITY;
ALTER TABLE suscripciones ENABLE ROW LEVEL SECURITY;

CREATE POLICY usuarios_admin ON usuarios FOR ALL USING (rol = 'admin');
CREATE POLICY usuarios_propios ON usuarios FOR SELECT USING (id = current_setting('app.current_user_id')::INTEGER);

-- 12. INDICES PARA PERFORMANCE
CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email);
CREATE INDEX IF NOT EXISTS idx_usuarios_rol ON usuarios(rol);
CREATE INDEX IF NOT EXISTS idx_licencias_codigo ON licencias(codigo);
CREATE INDEX IF NOT EXISTS idx_licencias_usuario ON licencias(usuario_id);
CREATE INDEX IF NOT EXISTS idx_suscripciones_usuario ON suscripciones(usuario_id);
CREATE INDEX IF NOT EXISTS idx_equipos_usuario ON equipos(usuario_id);
CREATE INDEX IF NOT EXISTS idx_logs_usuario ON logs_uso(usuario_id);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs_uso(timestamp);

-- ============================================================
-- MIGRACION V2: Agregar tabla de tokens de activacion
-- ============================================================

CREATE TABLE IF NOT EXISTS tokens_activacion (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    token VARCHAR(500) NOT NULL UNIQUE,
    tipo VARCHAR(50) DEFAULT 'activacion',
    usado BOOLEAN DEFAULT false,
    expira_en TIMESTAMP WITH TIME ZONE,
    usado_en TIMESTAMP WITH TIME ZONE,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tokens_usuario ON tokens_activacion(usuario_id);
CREATE INDEX IF NOT EXISTS idx_tokens_token ON tokens_activacion(token);
