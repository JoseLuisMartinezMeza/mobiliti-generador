# Mobiliti SaaS - Generador de Cotizaciones

Sistema de generacion de cotizaciones con control de suscripciones.

Para probar el SaaS completo sin Supabase ni Excel, usa
[`DEV_LOCAL.md`](DEV_LOCAL.md).

**Arquitectura:**
- **Web SaaS** -> React/Vite en `mobiliti_saas/web`
- **Backend API** -> Vercel/FastAPI en `vercel_deploy/api/index.py`
- **Base de Datos/Storage** -> Supabase PostgreSQL + bucket privado `quote-files`
- **Worker online** -> `mobiliti_saas/worker` con `QUOTE_ENGINE=python` sin Excel
- **Cliente desktop legado** -> archivado en `versiones historial`; no es ruta de produccion

---

## Arquitectura

```
+----------------------------+         +---------------------+
| Web React / Cliente legacy |         | Vercel (Serverless) |
| - Upload XLSX              |  HTTPS  | - FastAPI + JWT     |
| - Historial/descarga       | <-----> | - Jobs cotizacion   |
+----------------------------+         +----------+----------+
                                                  |
                                                  | REST API + Storage
                                                  v
                               +-------------------------------+
                               | Supabase                      |
                               | - saas_usuarios/suscripciones |
                               | - saas_quote_jobs             |
                               | - quote-files private bucket  |
                               +---------------+---------------+
                                               |
                                               v
                               +-------------------------------+
                               | Worker quote_worker.py        |
                               | - quote_engine Python puro    |
                               | - Docker/cloud sin Excel      |
                               +-------------------------------+
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

Variables requeridas para `seed_admin.py`:
- `DATABASE_URL`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `ADMIN_NOMBRE` (opcional)
- `ADMIN_EMPRESA` (opcional)

---

## 2. Desplegar Backend en Vercel

### 2.1 Instalar Vercel CLI

```bash
npm install -g vercel
```

### 2.2 Configurar variables de entorno

```bash
cd vercel_deploy
vercel --version  # login primero si es necesario

# Variables requeridas
vercel env add SUPABASE_URL
# Valor: https://TU-PROYECTO.supabase.co

vercel env add SUPABASE_SERVICE_KEY
# Valor: tu service role key de Supabase

vercel env add JWT_SECRET_KEY
# Valor: una clave secreta larga (cambiar en produccion)

vercel env add CORS_ORIGINS
# Valor: *
```

### 2.3 Desplegar

```bash
cd vercel_deploy
vercel --prod
```

O con drag & drop:
1. Comprime la carpeta `vercel_deploy`
2. Sube a [vercel.com](https://vercel.com) como proyecto Python

### 2.4 Verificar despliegue

```bash
curl https://TU-URL.vercel.app/health
# Debe responder: {"status": "ok"}
```

---

## 3. Cliente Desktop Legacy

El cliente desktop con `xlwings` ya no es la ruta productiva. Para produccion,
usa la web SaaS + API + worker Docker con `QUOTE_ENGINE=python`.

El historico del cliente/generador Windows se conserva en `versiones historial`
solo como referencia.

---

## 3B. Ejecutar Web SaaS y Worker Online

### Web local

```bash
cd mobiliti_saas/web
npm install
npm run dev
```

Variables en `mobiliti_saas/web/.env`:

```env
VITE_API_BASE_URL=https://TU-API.vercel.app
VITE_SUPABASE_URL=https://TU-PROYECTO.supabase.co
VITE_SUPABASE_ANON_KEY=TU_ANON_KEY_PUBLICA
```

### Worker online sin Excel

```bash
cd mobiliti_saas/worker
pip install -r requirements.txt
set SUPABASE_URL=https://TU-PROYECTO.supabase.co
set SUPABASE_SERVICE_KEY=TU_SERVICE_KEY
set QUOTE_ENGINE=python
python quote_worker.py
```

`QUOTE_ENGINE=python` es el motor final para SaaS. El motor `xlwings` queda
solo en historial como referencia antigua.

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
| `/cotizaciones/init-upload` | POST | Token | Crea job y token de carga firmado |
| `/cotizaciones/{id}/submit` | POST | Token | Encola cotizacion para worker |
| `/cotizaciones` | GET | Token | Lista historial web |
| `/cotizaciones/{id}` | GET | Token | Estado de una cotizacion |
| `/cotizaciones/{id}/download` | GET | Token | URL firmada de descarga |

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
2. Cliente entra a la web y hace login
3. Web pide /cotizaciones/init-upload
4. Web sube XLSX a Supabase Storage con token firmado
5. Web manda metadata a /cotizaciones/{id}/submit
6. Worker toma job queued, genera XLSX y sube output
7. Web consulta estado y descarga con URL firmada
8. El endpoint `/generar-cotizacion` queda solo como compatibilidad de API; la ruta productiva usa `/cotizaciones/*`
```

---

## 6. Estructura de Archivos

```
mobiliti_saas/
  api/                    # Copia local de API
  quote_engine/           # Motor final Python puro
  worker/                 # Worker Docker/cloud
    quote_worker.py       # Consume jobs y genera XLSX
    online_quote_generator.py
    Dockerfile
  web/                    # Frontend React/Vite
  supabase_setup/         # Setup de base de datos
    create_tables.sql     # SQL para crear tablas
    seed_admin.py         # Crea admin inicial

vercel_deploy/
  api/index.py            # API productiva para Vercel
  vercel.json

versiones historial/
  HISTORIAL DE VERSIONES/ # Desktop/xlwings/backend viejo archivados
```

---

## 7. Notas Importantes

- **Vercel** es serverless: NO ejecuta Excel. La API encola jobs y el worker Docker con Python puro genera los XLSX.
- **Supabase** guarda usuarios y suscripciones. El backend en Vercel solo hace queries REST a Supabase.
- **JWT tokens** expiran en 60 minutos. El cliente re-verifica en cada accion.
- **Worker Docker** usa `QUOTE_ENGINE=python`; no necesita Windows ni Microsoft Excel.
- **Legacy desktop/xlwings** esta archivado en `versiones historial` y no es parte de produccion.

### Plantilla oficial, preservación y límites técnicos

- La plantilla promovida se acepta sólo con SHA-256
  `e8bd97286aaa8af5dcf6d08b715231b9edcbe28b84da3db2523dfbb43f2c3989`.
- Se promueve con `scripts/promote_official_quote_template.py` hacia
  `mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx`, usando
  el manifiesto `formato-cotizacion-2026-oficial.contract.json` y un destino
  nuevo.
- La allowlist mutable comprende las hojas `Mobiliti`, `Cotizacion`, `Fletes`
  y `Estrategia Comercial `, sus referencias estructurales de workbook,
  `calcChain.xml`, el dibujo de productos y las partes agregadas para
  `Quotation`/`Quotation_Data`. Las demás partes quedan byte-idénticas.
- `Quotation` preserva la fuente importada; los renglones combinados aparecen
  una sola vez y en orden en `Quotation_Data`, siempre `veryHidden`.
- `Mobiliti!J` contiene costo convertido numérico y congelado. `K6`, `W`, `X`
  y las fórmulas oficiales posteriores no repiten la conversión.
- La capacidad se limita por las 1,048,576 filas físicas de XLSX menos filas
  reservadas y por 25 MiB de request, no por antiguos topes de líneas o
  secciones. Un exceso falla explícitamente; no hay truncamiento silencioso.

Aceptación local reproducible:

```powershell
python -m pytest tests\test_official_quote_stress.py -v
npm --prefix mobiliti_saas/web run build
```

Estado del handoff: validación local únicamente. No se ejecutó despliegue ni
se escribió en SharePoint, Supabase, Storage remoto o producción.

---

## 8. Troubleshooting

### Job queda en `queued`
- Verifica que el worker Docker este corriendo.
- Verifica `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `QUOTE_STORAGE_BUCKET` y `QUOTE_ENGINE=python`.

### Error 403 "Suscripcion expirada"
- El admin debe renovar la suscripcion via PATCH `/admin/suscripciones/{id}`

### Vercel timeout
- Las funciones serverless de Vercel tienen timeout de 10s (hobby) o 60s (pro)
- Nuestros endpoints son rapidos (< 1s) porque solo hacen queries a Supabase
