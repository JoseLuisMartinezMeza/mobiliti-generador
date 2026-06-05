# Mobiliti SaaS - Generador de Cotizaciones

Sistema de generacion de cotizaciones con control de suscripciones.

**Arquitectura:**
- **Backend API** → Desplegado en Vercel (serverless)
- **Base de Datos** → Supabase (PostgreSQL)
- **Cliente** → Aplicacion de escritorio Windows (.exe) con Tkinter
- **Generador** → xlwings local (requiere Excel en Windows)

---

## Arquitectura

```
+----------------------------+         +---------------------+
| Cliente Windows (.exe)     |         | Vercel (Serverless) |
| - Tkinter GUI              |  HTTPS  | - FastAPI + Mangum  |
| - xlwings local            | <-----> | - Auth JWT          |
| - Genera cotizaciones      |         | - Verifica suscrip. |
+----------------------------+         +----------+----------+
                                                  |
                                                  | REST API
                                                  v
                                       +---------------------+
                                       | Supabase (Postgres) |
                                       | - saas_usuarios     |
                                       | - saas_suscripciones|
                                       | - saas_sesiones     |
                                       +---------------------+
```

---

## 1. Setup Base de Datos (Supabase)

### 1.1 Crear tablas

Ve al **SQL Editor** del dashboard de Supabase y ejecuta:

```sql
-- Archivo: supabase_setup/create_tables.sql
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
```

### 1.2 Crear usuario admin

```bash
cd mobiliti_saas/supabase_setup
pip install passlib[bcrypt] requests
python seed_admin.py
```

**Credenciales admin:**
- Email: `proyectosjlmm@gmail.com`
- Password: `REMOVED_PASSWORD`

---

## 2. Desplegar Backend en Vercel

### 2.1 Instalar Vercel CLI

```bash
npm install -g vercel
```

### 2.2 Configurar variables de entorno

```bash
cd mobiliti_saas
vercel --version  # login primero si es necesario

# Variables requeridas
vercel env add SUPABASE_URL
# Valor: https://amarztcyhgtszmwazxgl.supabase.co

vercel env add SUPABASE_SERVICE_KEY
# Valor: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

vercel env add JWT_SECRET_KEY
# Valor: una clave secreta larga (cambiar en produccion)

vercel env add CORS_ORIGINS
# Valor: *
```

### 2.3 Desplegar

```bash
cd mobiliti_saas
vercel --prod
```

O con drag & drop:
1. Comprime la carpeta `mobiliti_saas` (sin node_modules)
2. Sube a [vercel.com](https://vercel.com) como proyecto Python

### 2.4 Verificar despliegue

```bash
curl https://TU-URL.vercel.app/health
# Debe responder: {"status": "ok"}
```

---

## 3. Compilar Cliente Desktop

### 3.1 Configurar URL del API

Edita `mobiliti_saas/config.json`:

```json
{
  "api_url": "https://TU-URL.vercel.app"
}
```

### 3.2 Compilar

```bash
cd mobiliti_saas
python scripts/build_cliente.py
```

O manualmente:

```bash
cd mobiliti_saas
python -m PyInstaller Mobiliti_SaaS.spec --clean --noconfirm
```

### 3.3 Salida

El ejecutable se genera en:
- `mobiliti_saas/dist/Mobiliti_Generador.exe`
- `mobiliti_saas/release/Mobiliti_Generador.exe` (copia)

---

## 4. Endpoints API

| Endpoint | Metodo | Auth | Descripcion |
|----------|--------|------|-------------|
| `/` | GET | No | Health check |
| `/health` | GET | No | Estado del servidor |
| `/login` | POST | No | Login email/password |
| `/verificar-sesion` | POST | Token | Re-verifica suscripcion |
| `/generar-cotizacion` | POST | Token | Autoriza generacion local |
| `/admin/usuarios` | GET | Admin | Listar usuarios |
| `/admin/usuarios` | POST | Admin | Crear usuario |
| `/admin/suscripciones` | GET | Admin | Listar suscripciones |
| `/admin/suscripciones` | POST | Admin | Crear suscripcion |
| `/admin/suscripciones/{id}` | PATCH | Admin | Cambiar estado |

### Crear usuario cliente

```bash
curl -X POST https://TU-URL.vercel.app/admin/usuarios \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "cliente@ejemplo.com",
    "password": "password123",
    "nombre": "Cliente Ejemplo",
    "empresa": "Empresa SA"
  }'
```

### Crear suscripcion

```bash
curl -X POST https://TU-URL.vercel.app/admin/suscripciones \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "usuario_id": 2,
    "plan": "mensual",
    "dias": 30
  }'
```

### Suspender cliente

```bash
curl -X PATCH https://TU-URL.vercel.app/admin/suscripciones/2 \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"estado": "suspendida"}'
```

---

## 5. Flujo de Uso

```
1. Admin crea usuario + suscripcion via API/curl
2. Cliente recibe email/password
3. Cliente abre Mobiliti_Generador.exe
4. Cliente ingresa credenciales
5. Backend verifica suscripcion activa
6. Cliente puede generar cotizaciones
7. Cada generacion re-verifica suscripcion online
8. Admin puede suspender en cualquier momento
```

---

## 6. Estructura de Archivos

```
mobiliti_saas/
  api/                    # Backend para Vercel
    index.py              # FastAPI app + endpoints
    db.py                 # Cliente Supabase REST
    auth.py               # JWT + bcrypt
  cliente/                # Cliente desktop (Tkinter)
    main_cliente.py       # GUI principal
    entry_point.py        # Entry point PyInstaller
  scripts/                # Scripts utilitarios
    build_cliente.py      # Compila el .exe
  supabase_setup/         # Setup de base de datos
    create_tables.sql     # SQL para crear tablas
    seed_admin.py         # Crea admin inicial
  vercel.json             # Configuracion Vercel
  requirements.txt        # Dependencias Vercel
  config.json             # URL del API (para cliente)
  Mobiliti_SaaS.spec      # Spec PyInstaller
```

---

## 7. Notas Importantes

- **Vercel** es serverless: NO ejecuta xlwings (no tiene Excel). El generador corre localmente en Windows.
- **Supabase** guarda usuarios y suscripciones. El backend en Vercel solo hace queries REST a Supabase.
- **JWT tokens** expiran en 60 minutos. El cliente re-verifica en cada accion.
- **config.json** debe distribuirse junto al .exe para que el cliente sepa a que API conectarse.
- **No hay archivos .py expuestos** en la distribucion: todo esta empaquetado en el .exe.

---

## 8. Troubleshooting

### "Sin conexion" al abrir el cliente
- Verifica que `config.json` tenga la URL correcta de Vercel
- Verifica que el backend responda: `curl URL/health`

### Error 403 "Suscripcion expirada"
- El admin debe renovar la suscripcion via PATCH `/admin/suscripciones/{id}`

### Build muy grande
- PyInstaller incluye todo Python + dependencias (~90MB es normal)
- Usa UPX (`--upx-dir`) para reducir tamano

### Vercel timeout
- Las funciones serverless de Vercel tienen timeout de 10s (hobby) o 60s (pro)
- Nuestros endpoints son rapidos (< 1s) porque solo hacen queries a Supabase
