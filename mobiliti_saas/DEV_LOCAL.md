# Mobiliti SaaS local sin Supabase ni Excel

Este modo prueba el flujo completo en tu maquina sin usar secretos reales:
login -> upload XLSX -> submit -> worker online -> download.

Credenciales dev:

```text
email: dev@mobiliti.local
password: dev12345
```

## Arranque rapido

Desde la raiz del repo:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-start.ps1
powershell -ExecutionPolicy Bypass -File scripts\dev-status.ps1
```

Para parar procesos iniciados por el script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-stop.ps1
```

Smoke test completo:

```powershell
python scripts\saas_doctor.py --dev --skip-supabase
python scripts\smoke-saas.py
powershell -ExecutionPolicy Bypass -File scripts\verify-saas.ps1
```

## 1. Backend API local

Desde la raiz del repo:

```powershell
$env:MOBILITI_DEV_MODE="1"
$env:JWT_SECRET_KEY="dev-secret-change-me-32-chars"
$env:CORS_ORIGINS="http://127.0.0.1:5173"
$env:MOBILITI_DEV_PUBLIC_BASE_URL="http://127.0.0.1:8000"
python -m uvicorn index:app --app-dir vercel_deploy\api --host 127.0.0.1 --port 8000
```

El backend crea `.mobiliti_dev_store/db.json` y guarda archivos en
`.mobiliti_dev_store/storage/quote-files`.

## 2. Web local

En otra terminal:

```powershell
cd mobiliti_saas\web
npm install
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm run dev -- --port 5173
```

Abre `http://127.0.0.1:5173`.

En modo dev no necesitas `VITE_SUPABASE_URL` ni `VITE_SUPABASE_ANON_KEY`,
porque la web sube el XLSX directo al backend local.

## 3. Worker online local

En otra terminal, desde la raiz del repo:

```powershell
$env:MOBILITI_DEV_MODE="1"
$env:QUOTE_ENGINE="python"
$env:WORKER_STALE_MINUTES="30"
python mobiliti_saas\worker\quote_worker.py
```

Sube una `Quotation.xlsx` desde la web. El worker toma el job `queued`, genera
`output.xlsx` sin Microsoft Excel y la web muestra descarga.

## Produccion

Produccion usa Supabase:

- `MOBILITI_DEV_MODE=0`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`

Rota cualquier clave compartida por chat antes de produccion.
