# Verificación local del catálogo Lumbro 2026

Fecha: 2026-07-18

Rama: `codex/offiho-catalog-20260709`

Resultado Task 12: **PASS**

Regresión integral: PASS

Entorno: local y recuperable

## Alcance verificado

Se sembró el snapshot Lumbro construido offline desde las cinco fuentes
aprobadas y se validó en la aplicación local mediante el navegador in-app en
`http://127.0.0.1:5173/`. La entrada Lumbro abre el catálogo compartido con
`62` productos agrupados y `147` variantes.

La verificación no sincronizó SharePoint ni publicó el snapshot o sus assets.
El feature flag de Lumbro se habilitó exclusivamente en el proceso API local.

## Semilla, hashes y cobertura

Comando de construcción offline:

```powershell
python .superpowers\sdd\artifacts\lumbro-20260718\seed_local_preview.py
```

`seed_local_preview.py` construyó el snapshot auditado, materializó los assets
por hash y generó el archivo de staging local
`.superpowers/sdd/artifacts/lumbro-20260718/db.with-lumbro.json`. El script no
reemplazó por sí mismo el dev-store activo.

Resultado del build real en `8.8 s`:

| Evidencia | Valor |
|---|---:|
| SHA-256 de fuentes combinadas | `c0f104e2b49a713f7cd63cf95d2ee3f23a4226ea87843d7d820caf29d2411bb6` |
| Items/variantes del snapshot | 147 |
| Variantes técnicas del spec guide | 126 |
| Códigos verificados | 4 |
| `needs_review` | 143 |
| Items con precio | 138 |
| Assets PNG únicos | 46 |
| Bindings de imagen | 125 |
| Items enriquecidos por catálogo | 88 |
| Filas comerciales analizadas | 70 |
| Importadas / reconciliadas / excluidas | 21 / 40 / 9 |

La igualdad de cobertura se verificó como
`70 = 21 + 40 + 9`. Las nueve exclusiones conservan una razón concreta en
`.superpowers/sdd/artifacts/lumbro-20260718/coverage.json`.

El manifest de la semilla está en
`.superpowers/sdd/artifacts/lumbro-20260718/local-seed-manifest.json`. El
snapshot conserva `approved_asset`; los assets se materializaron por hash bajo
`.superpowers/sdd/artifacts/lumbro-20260718/asset-server-root/`.

## Dev-store y recuperación

La configuración del proceso local resolvió el dev-store activo en:

```text
.superpowers/sdd/artifacts/task-22-preview/dev-store/db.json
```

Antes de reemplazar los datos locales se creó esta copia byte-for-byte:

```text
.superpowers/sdd/artifacts/lumbro-20260718/dev-store-backups/db.before-lumbro-20260718-205312.json
```

- Tamaño del original y backup: `5,352,405` bytes.
- SHA-256 original y backup:
  `b4ccaca73c536ba49aff580f5ef9bd595ea5cabf70c9303e4c5c14e8831bcd64`.
- SHA-256 del dev-store sembrado:
  `c983df0fc3e0ac9d4dc334707953ba4e417590b728a8ce4a087534bcad069e49`.
- Se conservaron `cr-global`, `sonara`, `sunon` y `alma`; sólo se añadió
  `lumbro`.

Comando histórico exacto usado para crear y verificar el backup antes de tocar
el dev-store:

```powershell
$ErrorActionPreference='Stop'; $artifact=Resolve-Path '.superpowers\sdd\artifacts\lumbro-20260718'; $backupDir=Join-Path $artifact 'dev-store-backups'; New-Item -ItemType Directory -Force -Path $backupDir | Out-Null; $source=Resolve-Path '.superpowers\sdd\artifacts\task-22-preview\dev-store\db.json'; $stamp=Get-Date -Format 'yyyyMMdd-HHmmss'; $dest=Join-Path $backupDir "db.before-lumbro-$stamp.json"; Copy-Item -LiteralPath $source -Destination $dest; $a=(Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant(); $b=(Get-FileHash -Algorithm SHA256 -LiteralPath $dest).Hash.ToLowerInvariant(); if($a -ne $b){ throw 'Backup hash mismatch' }; [pscustomobject]@{Source=$source.Path;Backup=$dest;Bytes=(Get-Item -LiteralPath $dest).Length;SHA256=$b} | Format-List
```

Comando histórico exacto que comprobó que el dev-store no había cambiado,
copió el staging y verificó el hash resultante:

```powershell
$ErrorActionPreference='Stop'; $db=Resolve-Path '.superpowers\sdd\artifacts\task-22-preview\dev-store\db.json'; $current=(Get-FileHash -Algorithm SHA256 -LiteralPath $db).Hash.ToLowerInvariant(); if($current -ne 'b4ccaca73c536ba49aff580f5ef9bd595ea5cabf70c9303e4c5c14e8831bcd64'){ throw "Dev-store changed after backup: $current" }; $staged=Resolve-Path '.superpowers\sdd\artifacts\lumbro-20260718\db.with-lumbro.json'; Copy-Item -LiteralPath $staged -Destination $db -Force; $after=(Get-FileHash -Algorithm SHA256 -LiteralPath $db).Hash.ToLowerInvariant(); $expected=(Get-FileHash -Algorithm SHA256 -LiteralPath $staged).Hash.ToLowerInvariant(); if($after -ne $expected){ throw 'Seeded db hash mismatch' }; $data=Get-Content -Raw -LiteralPath $db | ConvertFrom-Json; [pscustomobject]@{Path=$db.Path;BeforeSHA256=$current;AfterSHA256=$after;Published=(($data.catalog_published_snapshots.psobject.Properties.Name)-join ',');LumbroItems=@($data.catalog_published_snapshots.lumbro.payload.items).Count;LumbroHash=$data.catalog_published_snapshots.lumbro.source_hash} | Format-List
```

Por tanto, `seed_local_preview.py` sólo preparó staging/assets y el
`Copy-Item` protegido por hashes reemplazó exclusivamente el `db.json` del
dev-store local indicado arriba.

La restauración local es recuperable mediante la copia anterior, después de
detener los procesos locales. No se eliminó ningún archivo.

## Runtime local

Servicios usados:

| Servicio | URL | Estado final |
|---|---|---|
| Vite web | `http://127.0.0.1:5173/` | activo |
| API dev | `http://127.0.0.1:8000/` | health HTTP 200 |
| Assets por hash | `http://127.0.0.1:8093/` | activo; imagen de control HTTP 200 |

Comandos equivalentes/documentados:

```powershell
# Web, desde mobiliti_saas\web
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm.cmd run dev -- --port 5173

# API local con Lumbro habilitado sólo en este proceso
powershell -ExecutionPolicy Bypass -File `
  .superpowers\sdd\artifacts\lumbro-20260718\run-api-lumbro.ps1

# Assets locales inmutables
python -m http.server 8093 --bind 127.0.0.1 --directory `
  .superpowers\sdd\artifacts\lumbro-20260718\asset-server-root

Invoke-RestMethod http://127.0.0.1:8000/health
```

Un refresh del API local resolvió un aviso transitorio de caché. La
revalidación terminó con health HTTP 200 y sin errores ni warnings de consola.

## Matriz de verificación Browser

| Comprobación | Evidencia observada | Resultado |
|---|---|---|
| Navegación | La entrada lateral `Lumbro` abre 62 productos agrupados / 147 variantes | PASS |
| Producto representativo | NAPOLI verificado muestra código, descripción, unidad `PZA`, color negro, dimensiones `217 × 58 × 47 mm` y precio neto `MXN 660` | PASS |
| Imagen oficial | Recurso local completo de `368 × 274`; en desktop se muestra a `178.67 × 178.67`, sin rotura ni upscale | PASS |
| Fallback oficial | Texto visible `Ver catálogo Lumbro`; `href` y nueva pestaña confirmados en `https://www.lumbromx.com/category/all-products`, título `All Products \| LUMBRO` | PASS |
| Cantidad inválida | `2.5` con `min=1`, `step=1`, `stepMismatch=true`; `Agregar` conservó contador en 0 | PASS |
| Cantidad válida | `2` se agregó al carrito | PASS |
| Importe neto | NAPOLI `660 × 2`: subtotal neto `1320`, IVA `211.20`, total `1531.20` | PASS |
| Desktop | `1440 × 1000`; `scrollWidth=1425 ≤ 1440`, `overflowX=false`; drawer de 390 px dentro del viewport | PASS |
| Móvil | `390 × 844`; tarjeta 358 px, copy/facts 326.67 px, imagen `325.33 × 245.29`, `overflowX=false` | PASS |
| Drawer móvil | Drawer 358.79 px dentro del viewport; subtotal/IVA/total correctos | PASS |
| Consola final | Errores `[]`; warnings `[]` | PASS |

La imagen móvil también se mantiene por debajo de sus dimensiones naturales
de `368 × 274`, por lo que no se amplía artificialmente.

## Defectos encontrados y cerrados

### Descuento duplicado sobre precio neto

La primera inspección mostró un descuento cliente del 40 % sobre un precio que
ya era neto: lista `1320`, descuento `528`, subtotal `792`, IVA `126.72` y total
`918.72`. Esto contradecía el contrato `list_price_net` y al worker, que fija
`descuento=0`.

El commit `48ece9a` (`fix(web): respetar precio neto en catalogos`) corrigió el
flujo compartido, sin rama Lumbro:

- el formulario ya no presenta un descuento editable o engañoso;
- el submit envía `descuento: 0`;
- el total calcula precio neto más IVA una sola vez.

TDD: RED por ausencia de `supplierCartTotals`; GREEN con `14 passed`.

### Tarjeta ilegible en móvil estrecho

La primera captura `390 × 844` no tenía overflow horizontal, pero la regla
`132px minmax(0, 1fr)` comprimía la segunda columna y renderizaba textos largos
letra por letra.

El commit `c42517e` (`fix(web): apilar catalogo en movil estrecho`) agregó un
breakpoint genérico a `480px`: tarjeta a una columna, imagen `4 / 3` con
`object-fit: contain` heredado y footer apilado. La regla desktop y el sidebar
no cambiaron.

TDD: RED por ausencia del breakpoint; GREEN con `15 passed`.

Tarkett y Offiho usan componentes distintos en `main.jsx` y no fueron
modificados por ninguno de los dos fixes.

## Pruebas y build

```powershell
# RED documental inicial: terminó con código 1 y el mensaje esperado
if (Test-Path 'docs/superpowers/reports/2026-07-18-lumbro-local-verification.md') {
  exit 0
} else {
  Write-Error 'Lumbro local verification report is missing'
}

# Después de los fixes
python -m pytest tests/test_supplier_catalog_ui.py -q
# 15 passed in 3.67s

Set-Location mobiliti_saas\web
npm.cmd run build
# vite v7.3.5; 1701 modules transformed; PASS in 2.42s
```

La prueba de cálculo cubre expresamente NAPOLI `660 × 2 = 1320`, IVA
`211.20` y total `1531.20`. La prueba responsive conserva la regla desktop y
prohíbe cambios al sidebar dentro del breakpoint estrecho.

## Artefactos visuales

- Desktop final:
  `.superpowers/sdd/artifacts/lumbro-20260718/desktop-1440-napoli-final.png`.
- Móvil antes del fix, conservado como evidencia del defecto:
  `.superpowers/sdd/artifacts/lumbro-20260718/mobile-390-napoli.png`.
- Móvil final, tarjeta:
  `.superpowers/sdd/artifacts/lumbro-20260718/mobile-390-napoli-card-viewport.png`.
- Móvil final, carrito neto:
  `.superpowers/sdd/artifacts/lumbro-20260718/mobile-390-napoli-cart-net.png`.
- Consola final persistida desde la variable viva del IAB:
  `.superpowers/sdd/artifacts/lumbro-20260718/browser-console-final.json`.
  SHA-256:
  `45f4370df19d2df9231398da608ad5fe29f4c63224d0495f2253971b537a954c`.
  El artefacto registra niveles `error`, `warn` y `warning`, límite 100,
  `logs: []` y las métricas desktop/móvil usadas en esta verificación.

Los screenshots, snapshot, assets, backup y logs son artefactos locales y no
se incluyen en el commit del reporte.

## Regresión integral Task 13

La regresión se ejecutó sobre `4bbccbd`, que ya incluye los fixes locales
`48ece9a` (precio neto sin descuento duplicado) y `c42517e` (layout móvil
estrecho). Los gates se ejecutaron en el orden del plan y terminaron con
código `0`:

```powershell
python -m pytest tests/test_catalog_source_safety.py tests/test_lumbro_links.py tests/test_catalog_migrations.py -q
# 101 passed in 8.45s

python -m pytest tests/test_catalog_source_config.py tests/test_catalog_importers_lumbro.py tests/test_lumbro_catalog_audit.py tests/test_catalog_sync_service.py tests/test_catalog_repository.py tests/test_supplier_catalog.py tests/test_supplier_catalog_ui.py tests/test_quote_jobs_api.py tests/test_quote_worker.py tests/test_lumbro_catalog_e2e.py tests/test_quote_engine_lumbro.py tests/test_mobiliti_capacity.py -q
# 555 passed, 42 warnings in 737.80s (0:12:17)

python -m pytest -q
# 1260 passed, 9 skipped, 53 warnings in 1595.43s (0:26:35)

python -m compileall -q mobiliti_saas vercel_deploy scripts
# exit 0; sin errores de sintaxis

python -c "from pathlib import Path; import hashlib; groups=[['mobiliti_saas/api/index.py','mobiliti_saas/web/api/index.py','vercel_deploy/api/index.py'],['mobiliti_saas/quote_engine/supplier_catalog.py','mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py']]; assert all(len({hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in g}) == 1 for g in groups)"
# exit 0

Set-Location mobiliti_saas\web
npm.cmd run build
# vite v7.3.5; 1701 modules transformed; built in 2.29s
```

Los `42` warnings focalizados corresponden a `21` avisos de validación de
datos y `21` avisos de imágenes WMF emitidos por openpyxl. En la suite total
hubo `26 + 26` de esos mismos avisos y una deprecación conocida de
`Pillow.Image.getdata`, para `53` warnings. Los `9` casos omitidos fueron
reportados por pytest como `skipped`; no hubo fallos ni errores.

Hashes de paridad verificados:

- API raíz, web y Vercel:
  `023fe204a0b6139b6409c53205c3020113eed8b7ab0662584539a6c73464f3d1`.
- Dominio `supplier_catalog` raíz y web:
  `19adedc6648f9dc394387ca49157fcd1af54844bce561ae48e4103eb9031c968`.

El build usó el `package.json` y lockfile existentes; no se instalaron ni
actualizaron dependencias. Task 13 no necesitó cambios de código: sólo este
reporte registra los resultados.

## Límites y estado de producción

**Producción no modificada.** No se ejecutó SQL, no se escribió en Microsoft
Graph/SharePoint, no se publicó un snapshot remoto, no se cargaron assets a
Supabase Storage, no se habilitó Lumbro globalmente, y no hubo cambio en
Supabase, Vercel, deploy, push ni PR.

Una migración, sincronización, publicación de snapshot/assets o despliegue
requiere autorización separada.
