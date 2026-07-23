# Mobiliti Quote Worker

Worker para procesar jobs de la web SaaS.

Modo final para SaaS online: `QUOTE_ENGINE=python`. Corre sin Microsoft Excel
en Linux/Windows/macOS y es la ruta preparada para Docker/cloud.

El generador antiguo `xlwings` queda archivado en `versiones historial` como
referencia historica. No es ruta productiva.

## Proyectos persistentes

La API web guarda los Proyectos por usuario mediante `/projects`; las rutas de
detalle solo exponen recursos del usuario autenticado. Las actualizaciones usan
revision optimista: `PATCH /projects/{id}`, el archivado y la restauracion
requieren la revision esperada y responden con conflicto ante una version vieja.

`POST /projects/{id}/archive` archiva sin eliminacion permanente, mientras que
`GET /projects` lista los Proyectos activos o archivados. `GET /catalogs/search`
alimenta el selector unificado. Al importar una Quotation,
`POST /projects/{id}/imports/{job_id}` promueve su fuente e imagenes a recursos
durables, privados y asociados al Proyecto del usuario.

Esta fase no cambia todavia el motor XLSX.

Al cotizar una revision guardada, el JSON descargado por el worker es la
autoridad inmutable. Despues de validar el payload mixto, el worker entrega al
generador oficial una copia profunda de `project_context` junto con
`project_id`, `project_revision` y `project_payload_hash`. No consulta el
Proyecto actual, no reconstruye su composicion y no recalcula las tasas de
cambio congeladas durante este handoff.

Variables requeridas:

```powershell
$env:SUPABASE_URL="https://TU-PROYECTO.supabase.co"
$env:SUPABASE_SERVICE_KEY="REPLACE_WITH_SUPABASE_SERVICE_ROLE_KEY"
```

Variables opcionales:

```powershell
$env:QUOTE_STORAGE_PROVIDER="supabase"
$env:QUOTE_STORAGE_BUCKET="quote-files"
$env:QUOTE_ENGINE="python"
$env:MAX_QUOTE_OUTPUT_MB="100"
$env:TEMPLATE_PATH="C:\ruta\Formato Cotizacion 2026 Oficial.xlsx"
$env:WORKER_POLL_SECONDS="10"
$env:WORKER_STALE_MINUTES="30"
```

Para guardar inputs/outputs en Cloudflare R2 en lugar de Supabase Storage,
usa credenciales S3 de R2, no el API token general de Cloudflare:

```powershell
$env:QUOTE_STORAGE_PROVIDER="r2"
$env:R2_ACCOUNT_ID="REPLACE_WITH_CLOUDFLARE_ACCOUNT_ID"
$env:R2_ACCESS_KEY_ID="REPLACE_WITH_R2_S3_ACCESS_KEY_ID"
$env:R2_SECRET_ACCESS_KEY="REPLACE_WITH_R2_S3_SECRET_ACCESS_KEY"
$env:R2_BUCKET="quote-files"
$env:R2_REGION="auto"
# Opcional si usas un endpoint custom:
$env:R2_ENDPOINT_URL="https://REPLACE_WITH_ACCOUNT_ID.r2.cloudflarestorage.com"
```

Verifica antes de activar:

```powershell
cd C:\Users\pepem\Downloads\ARMADO_DE_CARATULA_prod_git_worktree
python scripts\r2_doctor.py --bucket quote-files --origin https://web-lemon-one-45.vercel.app --probe-object
```

Proveedor de mejora de imagenes:

```powershell
# Gratis/local, sin llamadas externas. Solo mejora imagenes existentes.
$env:IMAGE_PROVIDER="pillow"

# Opcion IA de costo. No guardes la clave en el repo.
$env:IMAGE_PROVIDER="dezgo"
$env:DEZGO_API_KEY="..."
$env:DEZGO_MODEL="flux_2_pro"
$env:DEZGO_ENDPOINT="https://api.dezgo.com/image2image_flux_2_pro"
$env:DEZGO_TEXT_ENDPOINT="https://api.dezgo.com/text2image_flux_2_pro"
$env:DEZGO_IMAGE_STRENGTH="0.58"
$env:DEZGO_PROMPT="photorealistic premium office furniture product image, preserve the exact original product shape and identity, geometry, materials, color and proportions, centered full product visible, clean pure white or transparent studio background, soft catalog shadow, crisp edges, high resolution, sharp commercial catalog quality, no text, no logos, no people"
$env:DEZGO_NEGATIVE_PROMPT="distorted geometry, changed product design, extra furniture, people, hands, text, watermark, logo, cropped product, blurry, low resolution, cartoon, illustration, oversaturated colors"
```

Tambien puedes mandar `image_provider` en la metadata del job (`pillow` o
`dezgo`). Si `image_provider=dezgo` llega desde la web y Dezgo no tiene clave o
falla, el job debe fallar con error visible; no hace fallback silencioso a
imagenes originales/locales.

Nota: `remove-background` no es retoque IA generativo. Para mejorar imagenes con
Dezgo usa `image2image_flux_2_pro`; el motor ignora endpoints legacy como
`DEZGO_ENDPOINT=image2image` salvo que configures
`DEZGO_ALLOW_LEGACY_IMAGE_ENDPOINT=1`.

Cuando `image_provider=dezgo` y una fila de producto no trae imagen embebida en
`Quotation`, el worker intenta generar una imagen nueva desde el nombre,
categoria, dimensiones y descripcion del producto usando `DEZGO_TEXT_ENDPOINT`.

Ejecucion:

```powershell
python mobiliti_saas\worker\render_web_worker.py
```

El proceso HTTP atiende `/health`, da prioridad a las cotizaciones y solo
intenta sincronizar un catalogo cuando la cola esta libre. La sincronizacion
aislada procesa como maximo un proveedor por invocacion:

```powershell
python -m mobiliti_saas.worker.catalog_sync.service --due
```

Cuando la cola queda libre, el mismo worker intenta primero el refresco oficial
USD/MXN y EUR/MXN de Banxico en un subproceso aislado. Consulta solo los ultimos
14 dias, inserta observaciones de forma append-only y vuelve a intentarlo cada
seis horas; un fallo usa reintento acotado a 15 minutos y no bloquea
cotizaciones ni sincronizaciones de proveedores:

```powershell
python -m mobiliti_saas.worker.catalog_sync.rate_service
```

Sin `BANXICO_SIE_TOKEN` el refresco queda `misconfigured` en `/health`; el
token solo se lee del entorno y nunca se imprime.

La sincronizacion queda desactivada si `CATALOG_SYNC_ENABLED` no esta activo o
si `CATALOG_ENABLED_SUPPLIERS` esta vacio/invalido. Los identificadores
permitidos son `cr-global`, `sonara`, `sunon` y `alma`; la base de datos aplica
el intervalo de seis horas y reclama primero los runs manuales. Antes de cada
claim, un RPC atomico cierra como `failed` los runs `running` de proveedores
habilitados cuyo lease fijo de 45 minutos vencio.

Nombres de variables para el worker de catalogos, sin valores ni secretos:

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

El health solo publica estados acotados y timestamps. Un timeout o fallo de
catalogo deja el worker en estado degradado, pero no detiene el procesamiento
posterior de cotizaciones. `CATALOG_SYNC_TIMEOUT_SECONDS` usa 1800 segundos por
defecto, acepta solo valores validos y siempre queda por debajo del lease. El
hijo usa codigos de salida acotados: `0` trabajo exitoso, `1` fallo, `2` sin
trabajo y `3` desactivado o mal configurado. Solo `0` limpia un fallo previo y
actualiza `last_catalog_sync_at`.

Prueba local del motor online sin Supabase:

```powershell
python mobiliti_saas\worker\online_quote_generator.py `
  --source "Quotation.xlsx" `
  --output "Cotizacion_online.xlsx" `
  --cotizacion "COT-001" `
  --proyecto "Demo" `
  --cliente "Cliente"
```

Dependencias minimas del worker online:

```powershell
pip install -r mobiliti_saas\worker\requirements.txt
```

El contenedor productivo usa `requirements.lock`, no el archivo de rangos
anterior. Para actualizarlo, resuelve y prueba las dependencias en una imagen
aislada, fija el resultado completo y repite `pip check`, importaciones,
Docker Scout y el smoke de generacion. La imagen corre como UID/GID `10001` y
debe desplegarse con filesystem raiz de solo lectura, `cap-drop=ALL` y
`no-new-privileges`; `/tmp` es el unico espacio temporal requerido.

## Contrato de plantilla oficial y capacidad dinámica

El worker falla cerrado si la plantilla promovida no tiene SHA-256
`e8bd97286aaa8af5dcf6d08b715231b9edcbe28b84da3db2523dfbb43f2c3989`.
La promoción local, siempre hacia un destino nuevo, se ejecuta así:

```powershell
python scripts\promote_official_quote_template.py `
  --source "C:\ruta\plantilla-auditada.xlsx" `
  --destination "mobiliti_saas\worker\templates\Formato Cotizacion 2026 Oficial.xlsx" `
  --contract "mobiliti_saas\worker\templates\formato-cotizacion-2026-oficial.contract.json"
```

El compositor parte de esos bytes y sólo puede cambiar las partes declaradas
por el contrato: `Mobiliti`, `Cotizacion`, `Fletes`, `Estrategia Comercial `,
`workbook.xml`, relaciones/contenidos del workbook, `calcChain.xml`, el dibujo
de productos y las partes nuevas de `Quotation`/`Quotation_Data`. Todo lo demás
se audita byte a byte. `Quotation` conserva exclusivamente la fuente importada;
el orden combinado vive en `Quotation_Data`, que queda `veryHidden`.

`Mobiliti!J` recibe cada costo convertido una sola vez como número congelado.
Las fórmulas oficiales desde `W`, incluido `K6`, calculan desde ese valor y no
vuelven a convertirlo. No hay topes comerciales de 33/500 líneas ni 16/32
secciones: se valida la capacidad física de 1,048,576 filas XLSX menos las
filas reservadas y un request máximo de 25 MiB; nunca se truncan productos.

Gate local de estrés:

```powershell
python -m pytest tests\test_official_quote_stress.py -v
```

Este handoff es sólo local. No promueve artefactos, no escribe en SharePoint,
Supabase o Storage y no despliega Vercel/worker sin autorización nueva.
