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

CREATE TABLE IF NOT EXISTS saas_suscripciones (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES saas_usuarios(id) ON DELETE CASCADE,
    estado TEXT DEFAULT 'activa' CHECK (estado IN ('activa','vencida','suspendida','cancelada')),
    plan TEXT DEFAULT 'mensual',
    fecha_inicio TIMESTAMPTZ DEFAULT NOW(),
    fecha_fin TIMESTAMPTZ NOT NULL,
    creado TIMESTAMPTZ DEFAULT NOW()
);

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

CREATE INDEX IF NOT EXISTS idx_usuarios_email ON saas_usuarios(email);
CREATE INDEX IF NOT EXISTS idx_suscripciones_usuario ON saas_suscripciones(usuario_id);
CREATE INDEX IF NOT EXISTS idx_sesiones_token ON saas_sesiones(token_jwt);

INSERT INTO saas_usuarios (email, hashed_password, nombre, empresa, es_admin, activo)
VALUES (
    '***REMOVED***',
    '$2b$12$beKMMIyQzAwErlJkSGc4meSk5CrMeYbaiPUDMHsPxGixhDG/uJN9C',
    'Administrador Mobiliti',
    'Mobiliti',
    TRUE,
    TRUE
)
ON CONFLICT (email) DO NOTHING;

INSERT INTO saas_suscripciones (usuario_id, estado, plan, fecha_inicio, fecha_fin)
SELECT 
    id,
    'activa',
    'anual',
    NOW(),
    NOW() + INTERVAL '10 years'
FROM saas_usuarios 
WHERE email = '***REMOVED***'
ON CONFLICT DO NOTHING;
