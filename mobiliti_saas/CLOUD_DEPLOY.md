# Mobiliti SaaS deploy cloud

Objetivo: web + API + worker online sin Windows y sin Microsoft Excel.

## Componentes

- Web: `mobiliti_saas/web`
- API: `vercel_deploy/api/index.py`
- Worker: `mobiliti_saas/worker`, `QUOTE_ENGINE=python`
- DB/Auth/metadata: Supabase (`saas_quote_jobs`)
- Archivos input/output: Supabase Storage por compatibilidad o Cloudflare R2
  en produccion recomendada (`QUOTE_STORAGE_PROVIDER=r2`)

No despliegues `mobiliti_saas` como root de Vercel. Ese folder conserva
codigo legado de escritorio/API vieja. Targets correctos:

- API Vercel root: `vercel_deploy`
- Web Vercel root: `mobiliti_saas/web`

## 1. Supabase

1. Rota claves compartidas por chat.
2. Ejecuta SQL base/migraciones:

```powershell
$env:DATABASE_URL="postgresql://postgres:[PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres"
python scripts\apply_supabase_sql.py
pip install psycopg[binary]
python scripts\apply_supabase_sql.py --apply
```

   Alternativa manual: ejecuta `mobiliti_saas/supabase_setup/create_tables.sql`
   en Supabase SQL Editor.

3. Si sigues con Supabase Storage, confirma bucket privado `quote-files`.
   Para produccion con cuota chica de Supabase, mueve los Excel/PDF a
   Cloudflare R2 y deja Supabase solo para DB/auth/metadata.
4. Crea usuario admin con `mobiliti_saas/supabase_setup/seed_admin.py`.

Variables:

```env
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres
SUPABASE_URL=https://[PROJECT_REF].supabase.co
SUPABASE_SERVICE_KEY=[SERVICE_ROLE_OR_SECRET_KEY]
JWT_SECRET_KEY=[LONG_RANDOM_SECRET]
QUOTE_STORAGE_BUCKET=quote-files
QUOTE_STORAGE_PROVIDER=supabase
```

Para Cloudflare R2 necesitas credenciales S3 de R2. El `API TOKEN` general de
Cloudflare no sirve para firmar URLs S3:

```env
QUOTE_STORAGE_PROVIDER=r2
R2_ACCOUNT_ID=[CLOUDFLARE_ACCOUNT_ID]
R2_ACCESS_KEY_ID=[R2_S3_ACCESS_KEY_ID]
R2_SECRET_ACCESS_KEY=[R2_S3_SECRET_ACCESS_KEY]
R2_BUCKET=quote-files
R2_REGION=auto
R2_ENDPOINT_URL=https://[CLOUDFLARE_ACCOUNT_ID].r2.cloudflarestorage.com
```

Antes de activar R2, verifica configuracion sin imprimir secretos:

```powershell
python scripts\r2_doctor.py `
  --cloudflare-env-file "C:\ruta\.env Cloudflare.txt" `
  --bucket quote-files `
  --origin https://TU-WEB.vercel.app `
  --probe-object
```

No configures `QUOTE_STORAGE_PROVIDER=r2` en produccion hasta que el reporte
muestre `s3_ready=true`, `cors_ready=true` y `probe_ready=true`. Para uploads
desde navegador, R2 necesita CORS con origen de la web, metodos `GET`, `PUT`,
`HEAD` y header `Content-Type`.

## 2. API en Vercel

Root del proyecto: `vercel_deploy`.

Variables Vercel:

```env
SUPABASE_URL=https://[PROJECT_REF].supabase.co
SUPABASE_SERVICE_KEY=[SERVICE_ROLE_OR_SECRET_KEY]
JWT_SECRET_KEY=[LONG_RANDOM_SECRET]
CORS_ORIGINS=https://TU-WEB.vercel.app
QUOTE_STORAGE_BUCKET=quote-files
MAX_QUOTE_UPLOAD_MB=25
MAX_QUOTE_OUTPUT_MB=100
QUOTE_STORAGE_PROVIDER=r2
R2_ACCOUNT_ID=[CLOUDFLARE_ACCOUNT_ID]
R2_ACCESS_KEY_ID=[R2_S3_ACCESS_KEY_ID]
R2_SECRET_ACCESS_KEY=[R2_S3_SECRET_ACCESS_KEY]
R2_BUCKET=quote-files
R2_REGION=auto
```

Comandos:

```powershell
cd vercel_deploy
vercel --prod
```

Verificacion:

```powershell
curl https://TU-API.vercel.app/health
python scripts\saas_doctor.py --api-url https://TU-API.vercel.app
powershell -ExecutionPolicy Bypass -File scripts\verify-saas.ps1 -Prod -ApiUrl https://TU-API.vercel.app -SkipSmoke
```

`saas_doctor.py` debe confirmar:

- `saas_quote_jobs` accesible por REST
- bucket `quote-files` privado con `file_size_limit` de 100 MB para permitir outputs XLSX con imagenes
- env vars reales, sin placeholders

## 3. Web en Vercel

Root del proyecto: `mobiliti_saas/web`.
Config incluida: `mobiliti_saas/web/vercel.json` (`vite`, output `dist`,
SPA fallback a `index.html`).

Variables Vercel:

```env
VITE_API_BASE_URL=https://TU-API.vercel.app
VITE_SUPABASE_URL=https://[PROJECT_REF].supabase.co
VITE_SUPABASE_ANON_KEY=[ANON_PUBLIC_KEY]
```

Comandos:

```powershell
cd mobiliti_saas\web
npm install
npm run build
vercel --prod
```

## 4. Worker online

El worker no requiere Excel. Puede correr en Render, Fly.io, Railway, ECS,
Cloud Run o cualquier host Docker con salida HTTPS.

Build local:

```powershell
docker build -f mobiliti_saas\worker\Dockerfile -t mobiliti-worker .
```

Run local contra Supabase:

```powershell
docker run --rm `
  -e SUPABASE_URL="https://[PROJECT_REF].supabase.co" `
  -e SUPABASE_SERVICE_KEY="[SERVICE_ROLE_OR_SECRET_KEY]" `
  -e QUOTE_ENGINE="python" `
  -e QUOTE_STORAGE_PROVIDER="r2" `
  -e R2_ACCOUNT_ID="[CLOUDFLARE_ACCOUNT_ID]" `
  -e R2_ACCESS_KEY_ID="[R2_S3_ACCESS_KEY_ID]" `
  -e R2_SECRET_ACCESS_KEY="[R2_S3_SECRET_ACCESS_KEY]" `
  -e R2_BUCKET="quote-files" `
  mobiliti-worker
```

Variables cloud worker:

```env
SUPABASE_URL=https://[PROJECT_REF].supabase.co
SUPABASE_SERVICE_KEY=[SERVICE_ROLE_OR_SECRET_KEY]
QUOTE_ENGINE=python
QUOTE_STORAGE_PROVIDER=r2
R2_ACCOUNT_ID=[CLOUDFLARE_ACCOUNT_ID]
R2_ACCESS_KEY_ID=[R2_S3_ACCESS_KEY_ID]
R2_SECRET_ACCESS_KEY=[R2_S3_SECRET_ACCESS_KEY]
R2_BUCKET=quote-files
WORKER_POLL_SECONDS=10
WORKER_STALE_MINUTES=30
IMAGE_PROVIDER=pillow
```

Para usar Dezgo en el worker cloud, agrega estos secretos solo en el panel del
host (Render/Railway/Fly/ECS/Cloud Run), nunca en git:

```env
IMAGE_PROVIDER=dezgo
DEZGO_API_KEY=[DEZGO_SECRET_KEY]
DEZGO_MODEL=realistic_vision_5_1
DEZGO_ENDPOINT=https://api.dezgo.com/image2image
DEZGO_TEXT_ENDPOINT=https://api.dezgo.com/text2image_flux
DEZGO_IMAGE_STRENGTH=0.58
```

`remove-background` solo quita fondo; no genera retoque IA. El motor fuerza
`image2image` por defecto para que la opcion Dezgo produzca imagenes retocadas
y falle de forma visible si la clave o la API no funcionan.

## 5. Smoke test produccion

1. Login en web.
2. Subir `Quotation.xlsx`.
3. Submit crea job `queued`.
4. Worker cambia `queued -> processing -> completed`.
5. Web muestra descarga.
6. XLSX descargado abre y contiene hoja `Cotizacion`.

Si job queda `queued`, worker no esta corriendo o no tiene env vars correctas.
Si upload falla, revisar bucket `quote-files` y anon key de web.

Smoke local automatizado:

```powershell
python scripts\saas_doctor.py --dev --skip-supabase
python scripts\smoke-saas.py --api-url http://127.0.0.1:8000
powershell -ExecutionPolicy Bypass -File scripts\verify-saas.ps1
```

Smoke produccion con upload Supabase firmado:

```powershell
$env:SUPABASE_URL="https://[PROJECT_REF].supabase.co"
$env:SUPABASE_ANON_KEY="[ANON_PUBLIC_KEY]"
python scripts\smoke-saas.py `
  --api-url https://TU-API.vercel.app `
  --email cliente@ejemplo.com `
  --password "PASSWORD"
```
