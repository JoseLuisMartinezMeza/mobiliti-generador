# Mobiliti Quote Worker

Worker para procesar jobs de la web SaaS.

Modo final para SaaS online: `QUOTE_ENGINE=python`. Corre sin Microsoft Excel
en Linux/Windows/macOS y es la ruta preparada para Docker/cloud.

El generador antiguo `xlwings` queda archivado en `versiones historial` como
referencia historica. No es ruta productiva.

Variables requeridas:

```powershell
$env:SUPABASE_URL="https://TU-PROYECTO.supabase.co"
$env:SUPABASE_SERVICE_KEY="REMOVED_SUPABASE_SERVICE_KEY"
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
# Gratis/local, sin llamadas externas. Es el default.
$env:IMAGE_PROVIDER="pillow"

# Opcion IA de costo. No guardes la clave en el repo.
$env:IMAGE_PROVIDER="dezgo"
$env:DEZGO_API_KEY="..."
$env:DEZGO_MODEL="flux_2"
$env:DEZGO_ENDPOINT="https://api.dezgo.com/remove-background"
```

Tambien puedes mandar `image_provider` en la metadata del job (`pillow` o
`dezgo`). Si Dezgo no tiene clave o falla, el motor cae al pipeline local para
evitar que el job quede colgado.

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
