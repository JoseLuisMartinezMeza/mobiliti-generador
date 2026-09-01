# Task 4 — Provider R2 específico para catálogo

Estado: implementación local lista para revisión. No se ejecutó DDL, deploy, push ni ninguna llamada live a Cloudflare o Supabase. Gate 4 live no se declara: el dominio público, token y bucket reales siguen pendientes de Tasks 7/8.

## Alcance implementado

- `CATALOG_ASSET_STORAGE_PROVIDER=supabase|r2`, con `supabase` por defecto, controla coordinadamente URLs hidratadas y nuevas escrituras de `catalog-assets`.
- API y worker usan exclusivamente `CATALOG_ASSET_R2_*`; no leen ni reutilizan `R2_*`, `R2_BUCKET` ni el cliente de `quote-files`.
- El cliente S3 de catálogo recibe endpoint, access key, secret y región explícitos. No usa la cadena de credenciales AWS.
- El origen público R2 exige HTTPS exacto, sin usuario/contraseña, puerto explícito, path distinto de `/`, query ni fragment; `r2.dev` y sus subdominios se rechazan.
- PUT de catálogo usa `IfNoneMatch="*"`, MIME, `Cache-Control: public, max-age=31536000, immutable` y metadata `sha256`. Un 412 sólo se acepta tras HEAD con SHA, tamaño y MIME exactos; 404/`NoSuchKey` significa ausencia y 403/5xx/timeout siguen siendo error.
- El registro neutral de DB conserva el bucket lógico `catalog-assets` y usa provider `r2` sólo después de PUT exitoso o HEAD compatible. No se compara ETag con SHA ni se descarga el body para verificar existencia.
- La hidratación R2 usa únicamente `CATALOG_ASSET_PUBLIC_BASE_URL`; Supabase conserva su URL pública anterior. El fingerprint del cache incluye provider/origen para evitar servir URLs obsoletas tras un cutover.
- `/health` de API y worker sólo publica provider y booleanos `storage_configured`, `public_configured` y `ready`; no llama a R2 ni expone endpoint, account, bucket o credenciales.
- La allowlist de imágenes de supplier conserva simultáneamente orígenes Supabase y R2 para rollback, pero sólo admite claves content-addressed bajo la ruta exacta de `catalog-assets`, sin query/fragment, y revalida redirects.
- Los tres `api/index.py` y las dos copias de `catalog_cart.py` quedan byte-identical.
- Los ejemplos de entorno, preflight y provisionamiento incluyen configuración de catálogo separada. Las credenciales sólo aparecen en entornos server/worker; el frontend sólo recibe provider/base pública.

## TDD RED → GREEN

RED inicial, antes de producción:

- repository: `14 failed` por ausencia de provider/config/PUT/HEAD R2.
- API: `12 failed` por ausencia de URL R2, fingerprint, upload, readiness y aislamiento.
- supplier/cart: `2 failed, 2 passed`; faltaban path exacto y rechazo de `r2.dev`.
- deploy/preflight/provision: `10 failed, 1 passed` por configuración condicional ausente.
- RED adicional de preflight: `1 failed, 10 passed` al exigir que DB/raw sigan requiriendo `SUPABASE_URL`.
- RED adicional de worker health: `2 failed` por readiness de catálogo ausente.

GREEN focal:

- repository R2: `14 passed`.
- API R2/cache/health/isolation: `12 passed`; registro R2 dirigido posterior: `3 passed`.
- supplier allowlist: `4 passed`.
- deploy/preflight/provision: `22 passed`.
- worker health: `2 passed`.

## Verificación fresca

- `python -B -m pytest -p no:cacheprovider tests/test_catalog_repository.py -q` → `75 passed`.
- `python -B -m pytest -p no:cacheprovider tests/test_quote_jobs_api.py -q` → `313 passed`.
- `python -B -m pytest -p no:cacheprovider tests/test_supplier_catalog.py -q` → `152 passed`.
- `python -B -m pytest -p no:cacheprovider tests/test_hetzner_deploy_safety.py -q` → `55 passed`.
- `python -B -m pytest -p no:cacheprovider tests/test_quote_worker.py -q` → `107 passed`.
- `python -B -m pytest -p no:cacheprovider tests/test_catalog_sync_service.py tests/test_mixed_catalog_cart.py tests/test_offiho_catalog.py -q` → `679 passed, 9 warnings`; warnings preexistentes de openpyxl sobre Data Validation/WMF.
- `python -m py_compile` sobre APIs, repository, worker, preflight y ambas copias de cart → exit `0`.
- Parser PowerShell sobre `deploy/hetzner/provision.ps1` → sin errores.
- SHA-256 de las tres APIs → `33E6B0AE60DC470742E40FC802053452672DE0DDE6B47D9A606DA237260ADC0B`.
- SHA-256 de ambas copias de cart → `6585AD7367DD2985606AB70F686F5902270ADE900F11AED4736A53D33B55B379`.
- `git diff --check` acotado → sin errores; sólo avisos de conversión LF/CRLF de Git para este worktree Windows.

## Aislamiento de `quote-files`

- No se modificó el camino de storage de cotizaciones ni sus nombres `R2_*`/`R2_BUCKET`.
- Las pruebas inyectan clientes distintos y comprueban `catalog-assets` frente a `quote-files`.
- Configurar sólo `R2_*` de cotizaciones no completa ni habilita R2 de catálogo; la configuración de catálogo falla cerrada.

## Riesgos y gates pendientes

- No existe aún un `CATALOG_ASSET_PUBLIC_BASE_URL` real validado para producción en este cambio.
- No se comprobó ni creó live el bucket `catalog-assets`, custom domain, CORS, cache rules o token de Cloudflare; corresponde a Tasks 7/8.
- No se migraron objetos ni se aplicó cutover de registry; Tasks 6–8 conservan esos gates.
- La disponibilidad externa no forma parte de liveness: health refleja configuración local, no realiza HEAD contra R2 en cada request.
