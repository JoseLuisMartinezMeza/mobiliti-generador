# Verificación local del catálogo Lumbro 2026

Fecha: 2026-07-18

Rama: `codex/offiho-catalog-20260709`

Resultado Task 12: **PASS**

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

Los screenshots, snapshot, assets, backup y logs son artefactos locales y no
se incluyen en el commit del reporte.

## Límites y estado de producción

**Producción no modificada.** No se ejecutó SQL, no se escribió en Microsoft
Graph/SharePoint, no se publicó un snapshot remoto, no se cargaron assets a
Supabase Storage, no se habilitó Lumbro globalmente, y no hubo cambio en
Supabase, Vercel, deploy, push ni PR.

Una migración, sincronización, publicación de snapshot/assets o despliegue
requiere autorización separada.
