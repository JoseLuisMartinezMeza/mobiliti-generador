# INSTRUCCIONES PARA DESPLEGAR MOBILITI SAAS

## PASO 1: Crear tablas en Supabase (SQL Editor)

1. Abre tu proyecto en [supabase.com](https://supabase.com)
2. Ve a **SQL Editor** (en el menu lateral)
3. Crea un **New Query**
4. **BORRA TODO** lo que haya en el editor
5. **Copia y pega EXACTAMENTE** este codigo SQL (todo el bloque):

```sql
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
```

6. Presiona el boton **RUN** (verde, arriba a la derecha)
7. Debe decir "Success. No rows returned"

**IMPORTANTE:** No pegues la ruta del archivo como hiciste antes. Solo pega el codigo SQL puro.

---

## PASO 2: Desplegar Backend en Vercel

### Opcion A: Con Vercel CLI (recomendado)

```bash
# 1. Instalar Vercel CLI
npm install -g vercel

# 2. Login
vercel login

# 3. Ve a la carpeta del backend
cd mobiliti_saas

# 4. Configurar variables de entorno
vercel env add SUPABASE_URL
# Valor: https://amarztcyhgtszmwazxgl.supabase.co

vercel env add SUPABASE_SERVICE_KEY
# Valor: JWT_PLACEHOLDER

vercel env add JWT_SECRET_KEY
# Valor: inventa una clave larga y segura (ej: Mobiliti2026SecretKeyChangeThis!)

# 5. Desplegar
vercel --prod
```

### Opcion B: Subir ZIP a Vercel (mas facil)

1. Ve a [vercel.com](https://vercel.com) y crea una cuenta
2. Click **"Add New Project"**
3. Selecciona **"Import Git Repository"** o sube un ZIP
4. Si subes ZIP:
   - Comprime SOLO la carpeta `mobiliti_saas` (con api/, vercel.json, requirements.txt)
   - Sube el ZIP
5. En **Environment Variables**, agrega:
   - `SUPABASE_URL` = `https://amarztcyhgtszmwazxgl.supabase.co`
   - `SUPABASE_SERVICE_KEY` = `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`
   - `JWT_SECRET_KEY` = una clave larga que inventes
6. Click **Deploy**

### Verificar que funciona

```bash
curl https://TU-URL.vercel.app/health
```

Debe responder: `{"status":"ok"}`

---

## PASO 3: Configurar URL en el Cliente

Edita el archivo `config.json` (esta junto al .exe):

```json
{
  "api_url": "https://TU-URL.vercel.app"
}
```

Reemplaza `TU-URL` con la URL real que te dio Vercel.

---

## PASO 4: Distribuir a Clientes

Los archivos que debes dar a cada cliente son:

1. `Mobiliti_Generador.exe` (92 MB)
2. `config.json` (con la URL correcta)

El cliente solo necesita:
- Windows 10/11
- Microsoft Excel instalado
- Conexion a internet

---

## Credenciales Admin

- **Email:** `***REMOVED***`
- **Password:** `***REMOVED***`

---

## Como crear un cliente nuevo

### 1. Crear usuario

```bash
curl -X POST https://TU-URL.vercel.app/admin/usuarios \
  -H "Authorization: Bearer TU_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "cliente@ejemplo.com",
    "password": "password123",
    "nombre": "Cliente Ejemplo",
    "empresa": "Empresa SA"
  }'
```

### 2. Crear suscripcion

```bash
curl -X POST https://TU-URL.vercel.app/admin/suscripciones \
  -H "Authorization: Bearer TU_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "usuario_id": 2,
    "plan": "mensual",
    "dias": 30
  }'
```

### 3. Suspender cliente (cortar acceso)

```bash
curl -X PATCH https://TU-URL.vercel.app/admin/suscripciones/2 \
  -H "Authorization: Bearer TU_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"estado": "suspendida"}'
```

---

## Estructura de archivos final

```
mobiliti_saas/
  api/
    index.py          <- Backend completo (FastAPI + Supabase)
  cliente/
    main_cliente.py   <- GUI Tkinter
    entry_point.py    <- Entry point PyInstaller
  scripts/
    build_cliente.py  <- Compila el .exe
  supabase_setup/
    TODO_EN_UNO_SQL.sql  <- SQL para copiar en Supabase
  vercel.json         <- Config Vercel
  requirements.txt    <- Dependencias Vercel
  config.json         <- URL del API (para el cliente)
  Mobiliti_SaaS.spec  <- Spec PyInstaller
  release/
    Mobiliti_Generador.exe  <- Ejecutable final
```

---

## Problemas comunes

### "Sin conexion" al abrir el cliente
- Verifica que `config.json` tenga la URL correcta
- Verifica que Vercel este online: `curl URL/health`

### Error 403 "Suscripcion expirada"
- Renueva la suscripcion via API o crea una nueva

### Build muy grande
- 90-95 MB es normal (incluye Python + Excel libraries + Tkinter)
