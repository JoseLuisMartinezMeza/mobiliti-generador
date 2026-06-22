# Mobiliti Quote Worker

Worker para procesar jobs de la web SaaS.

Modo final para SaaS online: `QUOTE_ENGINE=python`. Corre sin Microsoft Excel
en Linux/Windows/macOS y es la ruta preparada para Docker/cloud.

El generador antiguo `xlwings` queda archivado en `versiones historial` como
referencia historica. No es ruta productiva.

Variables requeridas:

```powershell
$env:SUPABASE_URL="https://TU-PROYECTO.supabase.co"
$env:SUPABASE_SERVICE_KEY="REPLACE_WITH_SUPABASE_SERVICE_ROLE_KEY"
```

Variables opcionales:

```powershell
$env:QUOTE_STORAGE_BUCKET="quote-files"
$env:QUOTE_ENGINE="python"
$env:TEMPLATE_PATH="C:\ruta\Formato Cotizacion 2026 GDL (1).xlsx"
$env:WORKER_POLL_SECONDS="10"
$env:WORKER_STALE_MINUTES="30"
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
$env:DEZGO_PROMPT="photorealistic premium office furniture product image, preserve the exact original product shape and identity, geometry, materials, wood grain, metal legs, color and proportions, centered full product visible, isolated on a clean pure white or transparent studio background, soft natural catalog shadow only, crisp edges, high resolution, sharp commercial catalog quality, remove dirty gray background artifacts, no text, no logos, no people"
$env:DEZGO_NEGATIVE_PROMPT="distorted geometry, changed product design, extra furniture, people, hands, text, watermark, logo, cropped product, blurry, low resolution, cartoon, illustration, oversaturated colors"
```

Tambien puedes mandar `image_provider` en la metadata del job (`pillow` o
`dezgo`). Si `image_provider=dezgo` llega desde la web y Dezgo no tiene clave o
falla, el job debe fallar con error visible; no hace fallback silencioso a
imagenes originales/locales.

Nota: `remove-background` no es retoque IA generativo. Para mejorar imagenes con
Dezgo usa `image2image_flux_2_pro`; el motor ignora endpoints legacy
`image2image`, `image2image_flux_2` y `remove-background` por defecto para
mantener Flux 2 Pro, salvo que configures explicitamente
`DEZGO_ALLOW_LEGACY_IMAGE_ENDPOINT=1` o `DEZGO_ALLOW_NON_RETOUCH_ENDPOINT=1`.

Cuando `image_provider=dezgo` y una fila de producto no trae imagen embebida en
`Quotation`, el worker intenta generar una imagen nueva desde el nombre,
categoria, dimensiones y descripcion del producto usando `DEZGO_TEXT_ENDPOINT`.

Ejecucion:

```powershell
python mobiliti_saas\worker\quote_worker.py --once
python mobiliti_saas\worker\quote_worker.py
```

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
