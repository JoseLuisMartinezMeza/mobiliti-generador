# JOME and Lauco Catalogs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrar JOME y Lauco como catálogos MXN completos y preservar sin cambios el dato financiero vinculado USD/MXN del formato oficial.

**Architecture:** Dos importadores estrictos producen el contrato normalizado ya usado por los catálogos de proveedor. JOME combina dos XLSX; Lauco usa un lector XLSB seguro y acotado. Los registros compartidos habilitan ambos catálogos en sync, API, Proyecto, cotización, SQL y frontend sin crear rutas paralelas.

**Tech Stack:** Python 3.14, pytest, openpyxl, pyxlsb 1.0.10, XML/ZIP estándar, FastAPI, React/Vite, PostgreSQL/Supabase.

## Global Constraints

- No borrar archivos permanentemente ni ejecutar operaciones Git destructivas.
- Preservar todos los cambios preexistentes del árbol de trabajo.
- No incluir cambios ajenos en commits; por el árbol sucio, no crear commits automáticos.
- Todos los costos JOME y Lauco son MXN desde origen.
- JOME usa costo E; Lauco usa costo F. Nunca usar precios comerciales I/K.
- MA02 y MA03 se normalizan a MXN sin aplicar tipo de cambio y conservan la moneda declarada en procedencia.
- Conservar `Mobiliti!J6`, `Mobiliti!K6`, `=_FV(J6,"High")`, rich-data/metadata y fórmulas W/X.
- Escribir una prueba que falle por la ausencia del comportamiento antes de cada cambio productivo.
- Mantener las copias API y quote-engine que el repositorio exige byte-idénticas.
- Actualizar `armado-caratula/41-Diagnostico-campo-integracion-JOME-Lauco.md` después de cada bloque material.

---

### Task 1: Congelar el contrato aprobado en pruebas

**Files:**
- Create: `tests/test_jome_catalog_importer.py`
- Create: `tests/test_lauco_catalog_importer.py`
- Modify: `tests/test_official_template_contract.py`
- Modify: `tests/test_official_mobiliti_pricing.py`

**Interfaces:**
- Consumes: plantilla oficial y helpers de fixtures existentes.
- Produces: pruebas RED para costos, moneda MXN, `_FV`, metadatos y W/X.

- [ ] **Step 1: Escribir pruebas financieras RED**

Agregar aserciones que abran el paquete OOXML y verifiquen que `J6` conserve
su índice de metadata, que `K6` conserve `=_FV(J6,"High")`, y que las firmas
de fórmulas W/X sean idénticas antes y después de componer.

- [ ] **Step 2: Ejecutar las pruebas financieras**

Run:
`python -m pytest tests/test_official_template_contract.py tests/test_official_mobiliti_pricing.py -q`

Expected: cualquier prueba nueva falla únicamente si la composición actual
modifica el vínculo o rechaza el estado vinculado.

- [ ] **Step 3: Escribir contratos RED de importadores**

Las pruebas sintéticas deben exigir:

```python
assert ma02["raw_cost"] == Decimal("135")
assert ma02["base_currency"] == "MXN"
assert ma02["provenance"]["declared_currency"] == "USD"
assert lauco_grade_1["raw_cost"] == Decimal("11780")
assert lauco_grade_1["base_currency"] == "MXN"
assert chrome_legs["option_kind"] == "add_on"
```

- [ ] **Step 4: Ejecutar los contratos de importadores**

Run:
`python -m pytest tests/test_jome_catalog_importer.py tests/test_lauco_catalog_importer.py -q`

Expected: FAIL porque los módulos JOME/Lauco aún no existen.

### Task 2: Importador JOME con dos fuentes XLSX

**Files:**
- Create: `mobiliti_saas/worker/catalog_sync/importers/jome.py`
- Modify: `mobiliti_saas/worker/catalog_sync/importers/common.py`
- Modify: `mobiliti_saas/worker/catalog_sync/importers/__init__.py`
- Test: `tests/test_jome_catalog_importer.py`

**Interfaces:**
- Consumes: `SourceDocument`, extracción segura OOXML y `CatalogSnapshotBuild`.
- Produces: `import_jome_catalog(documents, *, synced_at) -> dict`.

- [ ] **Step 1: Verificar RED del parser de filas**

Ejecutar el test que exige costo E, moneda MXN y procedencia de la moneda
declarada.

- [ ] **Step 2: Implementar el parser mínimo**

Leer únicamente `COSTO ESTRUCTURAS 2026` y `COSTO LAMINADO 2026`; construir
identidades con subcatálogo, sistema, bloque, código, dimensiones y fila.
Ignorar I. Normalizar toda moneda a MXN y añadir:

```python
provenance["declared_currency"] = declared_currency
provenance["currency_normalization"] = (
    "human_source_error_to_mxn" if declared_currency != "MXN" else None
)
```

- [ ] **Step 3: Implementar imágenes y variantes**

Reutilizar anclas OOXML. Ignorar solo relaciones WDP no utilizables; conservar
PNG/JPEG/TIFF. Compartir imagen principal por bloque sin colapsar códigos
duplicados.

- [ ] **Step 4: Ejecutar pruebas JOME**

Run:
`python -m pytest tests/test_jome_catalog_importer.py tests/test_catalog_source_safety.py -q`

Expected: PASS.

### Task 3: Lector seguro e importador Lauco XLSB

**Files:**
- Create: `mobiliti_saas/worker/catalog_sync/xlsb_source.py`
- Create: `mobiliti_saas/worker/catalog_sync/importers/lauco.py`
- Modify: `mobiliti_saas/worker/catalog_sync/importers/__init__.py`
- Modify: `mobiliti_saas/worker/requirements.txt`
- Test: `tests/test_lauco_catalog_importer.py`
- Test: `tests/test_catalog_source_safety.py`

**Interfaces:**
- Produces: `read_validated_xlsb_source(bytes) -> XlsbSource`.
- Produces: `import_lauco_catalog(document, *, synced_at) -> dict`.

- [ ] **Step 1: Verificar RED de seguridad XLSB**

Probar rechazo de rutas ZIP escapadas, ratios de compresión excesivos,
VBA/OLE/ActiveX, vínculos externos y relaciones desconocidas.

- [ ] **Step 2: Implementar validador y lector mínimo**

Fijar `pyxlsb==1.0.10`. Aplicar límites antes de abrir. Leer valores
cacheados de `COSTO-LAUCO-2026` sin ejecutar fórmulas.

- [ ] **Step 3: Implementar variantes y complementos**

Agrupar por fila de código. Crear opciones base para Tapiz Grado 1/2 y
complementos para patas. Usar F como costo, MXN como moneda y conservar G en
procedencia. Ignorar K.

- [ ] **Step 4: Implementar extracción de imágenes**

Resolver relaciones `sheet*.bin.rels -> drawing*.xml -> media/*`, validar
tipos y asociar las anclas a los grupos de producto.

- [ ] **Step 5: Ejecutar pruebas Lauco**

Run:
`python -m pytest tests/test_lauco_catalog_importer.py tests/test_catalog_source_safety.py -q`

Expected: PASS.

### Task 4: Registrar fuentes y sincronización

**Files:**
- Modify: `mobiliti_saas/worker/catalog_sync/sources.json`
- Modify: `mobiliti_saas/worker/catalog_sync/__init__.py`
- Modify: `mobiliti_saas/worker/catalog_sync/service.py`
- Modify: `mobiliti_saas/worker/catalog_sync/repository.py`
- Modify: `tests/test_catalog_source_config.py`
- Modify: `tests/test_catalog_sync_service.py`
- Modify: `tests/test_catalog_repository.py`

**Interfaces:**
- Consumes: importadores JOME/Lauco.
- Produces: snapshots publicables con slugs `jome` y `lauco`.

- [ ] **Step 1: Añadir expectativas RED de siete proveedores**

Exigir los dos IDs Graph de JOME y el ID Graph XLSB de Lauco, adaptadores
registrados y repositorio habilitado.

- [ ] **Step 2: Ejecutar pruebas de configuración**

Run:
`python -m pytest tests/test_catalog_source_config.py tests/test_catalog_sync_service.py tests/test_catalog_repository.py -q`

Expected: FAIL por registros ausentes.

- [ ] **Step 3: Añadir fuentes y adaptadores**

Registrar exactamente los paths/IDs oficiales inspeccionados. Habilitar
`.xlsb` solo para el lector seguro Lauco.

- [ ] **Step 4: Ejecutar pruebas de sincronización**

Repetir el comando de Step 2. Expected: PASS.

### Task 5: Ampliar contratos de catálogo, Proyecto y Excel

**Files:**
- Modify: `mobiliti_saas/quote_engine/supplier_catalog.py`
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/quote_engine/engine.py`
- Modify: `mobiliti_saas/quote_engine/quotation_sheets.py`
- Mirror: `mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py`
- Mirror: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/worker/quote_worker.py`
- Modify: `tests/test_supplier_catalog.py`
- Modify: `tests/test_mixed_catalog_cart.py`
- Modify: `tests/test_mixed_catalog_quote_e2e.py`

**Interfaces:**
- Produces: orden de nueve catálogos, etiquetas y proveedores canónicos.

- [ ] **Step 1: Escribir pruebas RED end-to-end**

Exigir `jome` y `lauco`, ambos MXN, proveedores `Jome` y `Lauco Sofas`, y una
cotización USD que convierta cada costo exactamente una vez.

- [ ] **Step 2: Ejecutar pruebas**

Run:
`python -m pytest tests/test_supplier_catalog.py tests/test_mixed_catalog_cart.py tests/test_mixed_catalog_quote_e2e.py -q`

Expected: FAIL por slugs no permitidos.

- [ ] **Step 3: Ampliar registros y alias**

Añadir ambos catálogos al orden y políticas. No introducir una ruta especial
de precio. Sincronizar las copias web byte a byte.

- [ ] **Step 4: Ejecutar pruebas**

Repetir Step 2 y añadir:
`python -m pytest tests/test_quote_engine_golden.py tests/test_official_template_contract.py -q`

Expected: PASS y `_FV`/W/X intactos.

### Task 6: API, reservas y migración forward

**Files:**
- Modify: `mobiliti_saas/api/index.py`
- Mirror: `mobiliti_saas/web/api/index.py`
- Mirror: `vercel_deploy/api/index.py`
- Create: `mobiliti_saas/supabase_setup/2026_07_jome_lauco_catalogs.sql`
- Modify: `mobiliti_saas/supabase_setup/create_tables.sql`
- Modify: `tests/test_quote_jobs_api.py`
- Modify: `tests/test_catalog_migrations.py`
- Modify: `tests/test_mixed_catalog_postgres.py`

**Interfaces:**
- Produces: siete proveedores de snapshot y nueve catálogos mixtos.

- [ ] **Step 1: Escribir pruebas RED API/SQL**

Exigir registro, endpoints, reservas y límites 7/9; el décimo catálogo debe
ser rechazado.

- [ ] **Step 2: Ejecutar pruebas**

Run:
`python -m pytest tests/test_quote_jobs_api.py tests/test_catalog_migrations.py tests/test_mixed_catalog_postgres.py -q`

Expected: FAIL por restricciones antiguas.

- [ ] **Step 3: Implementar migración y registros**

Crear una migración forward que amplíe CHECKs y RPCs. Reflejar el estado final
en `create_tables.sql`. Mantener las tres copias API byte-idénticas.

- [ ] **Step 4: Ejecutar pruebas**

Repetir Step 2. Expected: PASS.

### Task 7: Frontend y configuración operativa

**Files:**
- Create: `mobiliti_saas/web/src/catalogRegistry.js`
- Modify: `mobiliti_saas/web/src/main.jsx`
- Modify: `mobiliti_saas/web/src/mixedCart.js`
- Modify: `mobiliti_saas/web/src/MixedCartDrawer.jsx`
- Modify: `mobiliti_saas/web/src/productPicker.js`
- Modify: `mobiliti_saas/web/src/CatalogAdminPanel.jsx`
- Modify: `mobiliti_saas/.env.example`
- Modify: `mobiliti_saas/web/.env.example`
- Modify: `scripts/dev-start.ps1`
- Modify: `tests/test_supplier_catalog_ui.py`
- Modify: `tests/test_mixed_catalog_cart_ui.py`
- Modify: `tests/test_project_ui.py`

**Interfaces:**
- Produces: un registro frontend compartido con JOME, Lauco y Lumbro.

- [ ] **Step 1: Escribir pruebas RED UI**

Exigir navegación, selector de proveedor, reemplazo/complemento y panel admin
para nueve catálogos.

- [ ] **Step 2: Ejecutar pruebas**

Run:
`python -m pytest tests/test_supplier_catalog_ui.py tests/test_mixed_catalog_cart_ui.py tests/test_project_ui.py -q`

Expected: FAIL por catálogos ausentes.

- [ ] **Step 3: Centralizar el registro y habilitar vistas**

Crear `catalogRegistry.js` y reemplazar listas duplicadas sin cambiar el
comportamiento de catálogos existentes.

- [ ] **Step 4: Compilar y probar**

Run:
`npm.cmd run build`

Workdir: `mobiliti_saas/web`

Luego repetir Step 2. Expected: build y pruebas PASS.

### Task 8: Validación oficial, fuentes reales y documentación

**Files:**
- Modify: `tests/test_jome_catalog_importer.py`
- Modify: `tests/test_lauco_catalog_importer.py`
- Modify: `mobiliti_saas/worker/README.md`
- Modify: `CLOUD_DEPLOY.md`
- Update through MCP: `armado-caratula/41-Diagnostico-campo-integracion-JOME-Lauco.md`

**Interfaces:**
- Produces: evidencia reproducible y guía de despliegue.

- [ ] **Step 1: Ejecutar pruebas contra copias oficiales locales**

Con los archivos de SharePoint ya descargados, verificar hashes, conteos
esperados, costos de muestra, variantes, imágenes y ausencia de doble margen.

- [ ] **Step 2: Generar un libro de aceptación**

Crear una cotización mixta con al menos un JOME, un Lauco y un catálogo
existente en MXN y USD. Verificar paquete ZIP, hojas, fórmulas, imágenes,
proveedores y apertura con el validador disponible.

- [ ] **Step 3: Ejecutar regresión completa**

Run:
`python -m pytest -q`

Run:
`npm.cmd run build`

Expected: cero fallos y build exitoso.

- [ ] **Step 4: Actualizar documentación y Obsidian**

Registrar archivos modificados, comandos, resultados, hashes, riesgos
residuales y procedimiento de sincronización/publicación.
