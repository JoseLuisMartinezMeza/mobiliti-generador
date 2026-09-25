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
2. Para una **base de datos nueva**, ejecuta el bootstrap explícito:

```powershell
$env:DATABASE_URL="postgresql://postgres:[PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres"
pip install psycopg[binary]
python scripts\apply_supabase_sql.py --bootstrap-new-project
python scripts\apply_supabase_sql.py --bootstrap-new-project --apply
```

   `create_tables.sql` es sólo para una base de datos nueva. La alternativa
   manual es copiarlo al SQL Editor únicamente en ese caso.

   Este runner ejecuta únicamente el bootstrap, A y B canónicos, con ruta y
   contenido verificados. SQL adicional requiere un proceso separado o una
   ejecución manual revisada; no existe un flag de bypass en este runner.

   Para un **proyecto existente**, nunca uses el bootstrap y nunca apliques A+B
   juntas. El orden obligatorio es:

   1. aplicar A, `2026_09_catalog_asset_registry_r2.sql`;
   2. completar Gate 7A y ejecutar/certificar Gate 6 (Task 6) con los 2,214
      objetos del manifiesto fijado;
   3. aplicar B, `2026_09_catalog_asset_registry_r2_cutover.sql`, confirmando
      exactamente el batch `470442fc-3dc3-5948-b0e4-1dd34c1fcd30`.

```powershell
python scripts\apply_supabase_sql.py --file mobiliti_saas\supabase_setup\2026_09_catalog_asset_registry_r2.sql
python scripts\apply_supabase_sql.py --file mobiliti_saas\supabase_setup\2026_09_catalog_asset_registry_r2.sql --apply
# Gate 7A + scripts\migrate_catalog_assets_to_r2.py --execute + certificación Gate 6
python scripts\apply_supabase_sql.py --file mobiliti_saas\supabase_setup\2026_09_catalog_asset_registry_r2_cutover.sql --confirm-cutover-batch 470442fc-3dc3-5948-b0e4-1dd34c1fcd30
python scripts\apply_supabase_sql.py --file mobiliti_saas\supabase_setup\2026_09_catalog_asset_registry_r2_cutover.sql --confirm-cutover-batch 470442fc-3dc3-5948-b0e4-1dd34c1fcd30 --apply
```

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
MAX_QUOTE_OUTPUT_MB=150
QUOTE_STORAGE_PROVIDER=r2
R2_ACCOUNT_ID=[CLOUDFLARE_ACCOUNT_ID]
R2_ACCESS_KEY_ID=[R2_S3_ACCESS_KEY_ID]
R2_SECRET_ACCESS_KEY=[R2_S3_SECRET_ACCESS_KEY]
R2_BUCKET=quote-files
R2_REGION=auto
```

Las variables de catálogo son server-only y están separadas de todas las
`QUOTE_STORAGE_*`/`R2_*` anteriores. En el primer deploy compatible conserva
`CATALOG_ASSET_STORAGE_PROVIDER=supabase`; no cambies el proveedor durante la
aplicación de A ni antes de certificar Gate 6.

```env
CATALOG_ASSET_STORAGE_PROVIDER=supabase
CATALOG_ASSET_PUBLIC_BASE_URL=https://[PROJECT_REF].supabase.co/storage/v1/object/public/catalog-assets
CATALOG_ASSET_R2_ACCOUNT_ID=[CATALOG_CLOUDFLARE_ACCOUNT_ID]
CATALOG_ASSET_R2_ENDPOINT_URL=https://[CATALOG_CLOUDFLARE_ACCOUNT_ID].r2.cloudflarestorage.com
CATALOG_ASSET_R2_ACCESS_KEY_ID=[CATALOG_R2_S3_ACCESS_KEY_ID]
CATALOG_ASSET_R2_SECRET_ACCESS_KEY=[CATALOG_R2_S3_SECRET_ACCESS_KEY]
CATALOG_ASSET_R2_SESSION_TOKEN=[CATALOG_R2_OPTIONAL_SESSION_TOKEN]
CATALOG_ASSET_R2_BUCKET=catalog-assets
CATALOG_ASSET_R2_REGION=auto
```

`CATALOG_ASSET_R2_SESSION_TOKEN` es opcional y se omite cuando las credenciales
no lo entregan. Las credenciales y el bucket deben pertenecer sólo a catálogo;
nunca reutilices el bucket `quote-files` ni sus credenciales. El orden de
canary/readiness es: desplegar código dual con provider `supabase`, comprobar
`/health`, completar A → Gate 7A → Gate 6 → B, configurar la misma public base
R2 en preview y worker canary, comprobar `catalog_asset_ready=true`, y sólo
entonces coordinar el cambio único a provider `r2`. Mantén ambos hosts exactos
en allowlist durante la ventana de rollback.

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
- bucket `quote-files` privado con `file_size_limit` de 150 MB para permitir outputs XLSX con imagenes
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
docker build --pull -f mobiliti_saas\worker\Dockerfile -t mobiliti-worker .
```

El contexto del build usa la allowlist del `.dockerignore` raiz; no cambies el
contexto a un directorio mas amplio ni reincorpores `.git`, historiales,
catalogos fuente o archivos de entorno. La imagen instala
`mobiliti_saas/worker/requirements.lock`, usa una base Debian slim/glibc
fijada por digest y ejecuta el runtime como UID/GID `10001`, no como `root`.
El runtime reserva `/tmp` para la caché de Numba y el modelo local de
segmentación de imágenes; en producción ese directorio debe conservar el
volumen temporal declarado en `deploy/hetzner/docker-compose.yml`.

Run local contra Supabase:

```powershell
docker run --rm `
  --read-only `
  --tmpfs /tmp:rw,noexec,nosuid,size=256m `
  --memory 768m `
  --cpus 1 `
  --pids-limit 256 `
  --cap-drop ALL `
  --security-opt no-new-privileges:true `
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

## 5. Sincronizacion de catalogos

Antes del smoke productivo, el worker de catalogos requiere la migracion
`2026_07_multi_supplier_catalogs.sql` y debe ejecutar
`mobiliti_saas/worker/render_web_worker.py`. La sincronizacion se habilita de
forma gradual mediante una lista explicita; vacia o desactivada no accede a
SharePoint. La base de datos reclama primero solicitudes manuales, aplica el
intervalo de seis horas y evita dos runs activos para una misma fuente.

Variables adicionales del worker, documentadas solo por nombre:

- `MS_GRAPH_TENANT_ID`
- `MS_GRAPH_CLIENT_ID`
- `MS_GRAPH_CERT_PATH`
- `MS_GRAPH_CERT_THUMBPRINT`
- `SHAREPOINT_HOSTNAME`
- `SHAREPOINT_SITE_PATH`
- `SHAREPOINT_DRIVE_NAME`
- `SHAREPOINT_CATALOG_ROOT`
- `BANXICO_SIE_TOKEN`
- `CATALOG_SYNC_ENABLED`
- `CATALOG_ENABLED_SUPPLIERS`
- `CATALOG_SYNC_TIMEOUT_SECONDS`
- `CATALOG_ASSET_PUBLIC_BASE_URL`

Los proveedores habilitables son `cr-global`, `sonara`, `sunon`, `alma`,
`lumbro`, `jome`, `lauco`, `idelika`, `conceptos`, `labenze` y `requiez`.
El valor vacío mantiene todos los catálogos
deshabilitados; configura solo una lista CSV de estos identificadores, sin
duplicados ni espacios.

`jome` requiere los dos XLSX oficiales de Estructuras y Laminado; usa solo el
costo E, ignora el precio comercial I y publica MXN. Las etiquetas USD de MA02
y MA03 se corrigen a MXN sin conversion, conservando la moneda declarada en la
procedencia. `lauco` requiere su XLSB oficial y `pyxlsb==1.0.10`; usa costo F,
conserva G como procedencia, ignora K y publica MXN. Estos archivos se procesan
solo en el worker: se aplican los limites ZIP/OOXML, se bloquean relaciones o
vinculos externos y una fuente invalida no publica un snapshot parcial.
Vercel no descarga ni parsea archivos de SharePoint: esa responsabilidad queda
en el worker. Verifica `/health` y sus campos `last_catalog_sync_at` y
`last_catalog_sync_status` antes de ampliar la lista. El mismo health expone
`last_rate_sync_at` y `last_rate_sync_status`; nunca expone el token.

El refresco Banxico se ejecuta en un subproceso separado solo cuando la cola de
cotizaciones esta libre. Consulta USD/MXN y EUR/MXN para los ultimos 14 dias,
inserta observaciones append-only y se limita a una ejecucion cada seis horas.
Un fallo usa reintento de 15 minutos y timeout de 30 segundos, sin impedir una
cotizacion o el siguiente catalogo. Antes de habilitarlo, guarda
`BANXICO_SIE_TOKEN` exclusivamente en el secret manager del host.

El scheduler recupera en base de datos los runs `running` con lease vencido de
45 minutos antes de reclamar trabajo. El timeout configurable del subprocess
siempre se limita por debajo de ese lease. Un resultado `no_work` no cambia el
timestamp ni borra un estado `failed/timeout`; solo una sincronizacion real
exitosa limpia el degradado. El `HEALTHCHECK` usa el `PORT` efectivo del runtime.

Gate local completado el 17 de julio de 2026: la imagen OCI se construyo con el
contexto allowlist, paso `pip check`, importo todas las dependencias de runtime
y Docker Scout reporto cero vulnerabilidades conocidas. Un smoke aislado
API -> cola -> worker -> XLSX completo un job con filesystem raiz de solo
lectura, capacidades eliminadas, `no-new-privileges`, 768 MiB de memoria y un
CPU. El pico observado fue 360,812,544 bytes, sin OOM ni reinicios. Conserva
como gates separados la concurrencia PostgreSQL, servicios externos y el
canary en el host real; este resultado no autoriza un despliegue productivo.

Gate PostgreSQL aislado completado el 17 de julio de 2026: el bootstrap real y
las carreras de claim, staging, publicacion, reservas y tipos de cambio pasaron
en PostgreSQL Supabase 17 sin puertos de host. La prueba corrigio el lock
determinista de la primera insercion FX y la liberacion idempotente de reservas
genericas. Este resultado tampoco aplica la migracion a Supabase productivo.

La lectura delegada de SharePoint confirmo que los 12 archivos allowlisted
existen y que sus encabezados/contenido representan CR Global, Sonara, Sunon y
ALMA. Para el runtime sigue siendo obligatorio crear una aplicacion Entra con
certificado y `Sites.Selected`, probar Graph delta y Storage en preproduccion y
aprobar cada snapshot antes de ampliar el canary.

## 6. Smoke test produccion

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
