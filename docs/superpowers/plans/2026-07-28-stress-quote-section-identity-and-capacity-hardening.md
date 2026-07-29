# Stress Quote Section Identity and Dynamic Capacity Hardening Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Hacer que una cotización de Proyecto conserve la identidad persistente de sus secciones, falle antes de encolar si esa identidad no coincide y genere sin truncamiento cantidades de secciones o productos superiores al formato base de 16 × 33.

**Architecture:** Mantener el `MobilitiRowMap` dinámico existente como única fuente de posiciones físicas. Los IDs de sección del Proyecto serán opacos y estables; el orden visual dependerá de `position`, nunca de renumerar el ID. El contrato mixto validará la relación `occurrence_id → section_id` antes de crear el job y el motor conservará la misma validación como defensa en profundidad.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, openpyxl/OOXML, pytest, Supabase/Vercel worker.

---

## Diagnóstico confirmado

- El job de producción `300-00066` (`PRUEBA`) falló con `Principal de Proyecto en sección incorrecta` después de 452 segundos.
- El Proyecto contenía 18 secciones y 117 líneas físicas; la primera sección tenía 37 líneas físicas. Es una carga pequeña frente al límite real de XLSX.
- Sus IDs persistentes tenían huecos: `section-1`, `section-5`, `section-6`, …, `section-22`.
- `_build_saved_project_quote_payload()` renumera esas secciones como `section-1`, `section-2`, …, mientras `project_context()` conserva los IDs persistentes. El motor compara ambos contratos y rechaza correctamente la discrepancia, pero demasiado tarde.
- Un reproductor mínimo con `section-1` y `section-5` produce presentación `section-1`, `section-2` y contexto `section-1`, `section-5`.
- Las 14 pruebas puras de capacidad y layout pasan. La capacidad actual usa `max(16, section_count)` y `max(33, item_count)` y solo rechaza por el límite físico de XLSX.
- La matriz E2E de stress existente está roja antes de medir capacidad por una deriva independiente del fixture sintético: `Fila canónica no coincide: provider`. Esto no causó `300-00066`, que alcanzó una etapa posterior, pero actualmente impide usar esa suite como puerta de regresión.

## Invariantes de la solución

1. Un `section_id` persistido no se reescribe ni se deriva de su posición.
2. `position` controla el orden; el rótulo visible “Sección N” puede usar el ordinal sin cambiar el ID.
3. Cada `line_id` de presentación debe apuntar a la misma sección que su composición de Proyecto.
4. La validación cruzada ocurre antes de crear el job o subir sus archivos.
5. Más de 16 secciones y más de 33 líneas por sección expanden filas; no truncan ni reutilizan subtotales.
6. Solo los límites físicos de XLSX y el límite documentado de bytes pueden rechazar una cotización grande.

### Task 1: Congelar la regresión de IDs de sección con huecos

**Files:**
- Modify: `tests/test_project_quote_api.py`
- Test: `tests/test_project_quote_api.py`

**Step 1: Write the failing API regression test**

Agregar `test_project_quote_preserves_non_contiguous_section_ids_before_enqueue`:

- construir un Proyecto válido con secciones persistentes `section-1` y `section-5`;
- colocar una línea principal distinta en cada sección;
- llamar `POST /projects/{id}/quote`;
- leer el payload congelado subido por el fixture;
- exigir que `payload["sections"]` conserve exactamente `["section-1", "section-5"]`;
- exigir que cada `project_context.compositions[*].section_id` coincida con la sección que contiene sus `component_line_ids`;
- exigir que los eventos sigan siendo `create_job`, `upload`, `queue`, `wake`.

**Step 2: Run the test to verify it fails**

Run:

```powershell
python -m pytest tests/test_project_quote_api.py::test_project_quote_preserves_non_contiguous_section_ids_before_enqueue -q
```

Expected: FAIL mostrando que `section-5` fue transformada en `section-2`.

**Step 3: Keep the fixture representative**

No usar IDs contiguos ni recrear un Proyecto nuevo durante el assert. El caso debe simular secciones eliminadas por el usuario, que es el origen real de los huecos.

### Task 2: Preservar la identidad persistente al construir la cotización

**Files:**
- Modify: `mobiliti_saas/api/index.py:4741`
- Modify: `mobiliti_saas/web/api/index.py:4741`
- Test: `tests/test_project_quote_api.py`

**Step 1: Replace ordinal IDs with persistent IDs**

En ambos espejos de `_build_saved_project_quote_payload()`, construir cada sección así:

```python
{
    "id": section["section_id"],
    "title": section["concept"],
    "line_ids": compositions_by_section.get(section["section_id"], []),
}
```

Eliminar `enumerate(..., start=1)` de la identidad. Conservar el orden ya normalizado de `checked["sections"]`.

**Step 2: Run the focused regression**

Run:

```powershell
python -m pytest tests/test_project_quote_api.py::test_project_quote_preserves_non_contiguous_section_ids_before_enqueue -q
```

Expected: PASS.

**Step 3: Verify both server entrypoints remain equivalent**

Run:

```powershell
git diff --no-index mobiliti_saas/api/index.py mobiliti_saas/web/api/index.py
```

Expected: únicamente las diferencias preexistentes y deliberadas; el bloque `_build_saved_project_quote_payload` debe ser equivalente.

### Task 3: Validar `line_id → section_id` antes de encolar

**Files:**
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py:426`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py:426`
- Modify: `tests/test_mixed_catalog_cart.py:447`
- Test: `tests/test_mixed_catalog_cart.py`

**Step 1: Write a failing cross-contract test**

Agregar un caso que:

- construya un `project_context` exacto con dos secciones;
- mantenga el mismo conjunto de ocurrencias;
- mueva en `presentation_sections` una ocurrencia a la sección equivocada;
- espere `ValueError("Contexto de Proyecto invalido")`.

Este test debe demostrar que comparar solo conjuntos de IDs no detecta la deriva actual.

**Step 2: Change the normalization input**

Después de `_normalize_presentation_sections()`, crear:

```python
occurrence_section_ids = {
    line_id: section["id"]
    for section in normalized_sections
    for line_id in section["line_ids"]
}
```

Cambiar `_normalize_project_context()` para recibir ese mapeo, no solo una lista de ocurrencias.

**Step 3: Validate every composition mapping**

Además de los checks actuales:

- para cada composición, exigir que todos sus `component_line_ids` estén en `occurrence_section_ids`;
- exigir que todos apunten al `composition["section_id"]`;
- conservar la igualdad exacta del contexto congelado y las validaciones de principal/complementos.

Aplicar el mismo check dentro de `validate_mixed_catalog_payload()`, reconstruyendo el mapeo desde `payload["sections"]`.

**Step 4: Synchronize the quote-engine mirror**

Copiar el cambio de forma byte-identical al espejo web y ejecutar:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py::test_project_context_must_be_exact_and_reference_every_occurrence tests/test_mixed_catalog_cart.py::test_quote_engine_module_copies_are_byte_identical -q
```

Expected: PASS.

**Step 5: Verify fail-fast behavior at the API boundary**

Extender la prueba de Task 1 con un payload deliberadamente incoherente y exigir:

- HTTP 400;
- `jobs == []`;
- `storage == {}`;
- `events == []`.

### Task 4: Reparar la puerta E2E de stress antes de modificar capacidad

**Files:**
- Modify: `tests/test_official_quote_stress.py:535`
- Test: `tests/test_official_quote_stress.py:817`

**Step 1: Pin the current failure**

Run:

```powershell
python -m pytest tests/test_official_quote_stress.py::test_large_quotes_preserve_every_line_and_official_contract -q
```

Expected before repair: FAIL en `_bind_authoritative_canonical_rows` con `Fila canónica no coincide: provider`.

**Step 2: Align the synthetic provider contract**

Actualizar únicamente el fixture sintético para escribir y congelar el mismo proveedor canónico que usa el constructor de cada catálogo. No relajar la validación productiva ni omitir el campo `provider`.

**Step 3: Run the complete existing stress matrix**

Run:

```powershell
python -m pytest tests/test_official_quote_stress.py::test_large_quotes_preserve_every_line_and_official_contract -q
```

Expected: PASS para `one-34`, `one-100`, `sections-17`, `sections-20`, `20x40` y `10x100`.

### Task 5: Convertir `PRUEBA` en una regresión exacta de capacidad dinámica

**Files:**
- Modify: `tests/test_official_quote_stress.py`
- Modify: `tests/test_project_quote_acceptance.py:704`
- Modify: `tests/test_mobiliti_capacity.py`
- Test: same files

**Step 1: Add the exact production shape**

Agregar un shape con:

- 18 secciones;
- IDs con huecos iguales al patrón real;
- 117 líneas físicas;
- 37 líneas físicas en la primera sección;
- 2 complementos y 115 principales/composiciones.

**Step 2: Assert the row-map contract**

Para cada sección exigir:

- `product_capacity == max(33, physical_item_count)`;
- `subtotal_row > last_product_row`;
- el siguiente `header_row == previous subtotal_row + 1`;
- IDs estables aunque los ordinales visibles sean contiguos;
- el resumen global, fechas, tablas inferiores y pie queden después de `row_map.after_sections_row`.

**Step 3: Assert no data or formula truncation**

En el XLSX generado exigir:

- las 117 ocurrencias presentes una sola vez en `Quotation_Data`, `Mobiliti` y `Cotizacion`;
- fórmulas oficiales W/X traducidas hasta la última fila de la última sección;
- subtotales y total general referenciando todas las filas;
- ninguna referencia nueva `#REF!`, `#VALUE!` o rangos cerrados en la fila 571;
- ZIP/OOXML válido y apertura sin reparación.

**Step 4: Keep the larger boundary matrix**

Run:

```powershell
python -m pytest tests/test_mobiliti_layout.py tests/test_mobiliti_capacity.py tests/test_official_quote_stress.py tests/test_project_quote_acceptance.py -q
```

Expected: PASS para 34/100 líneas en una sección, 17/20 secciones, 20×40, 10×100, 700 líneas/35 secciones y el shape exacto de `PRUEBA`.

### Task 6: Auditar y retirar límites heredados ambiguos

**Files:**
- Modify if reachable: `mobiliti_saas/quote_engine/engine.py:174`
- Modify if reachable: `mobiliti_saas/web/mobiliti_saas/quote_engine/engine.py`
- Test: `tests/test_mobiliti_capacity.py`
- Test: `tests/test_official_quote_stress.py:913`

**Step 1: Prove route reachability**

Trazar la ruta de Proyecto hasta `plan_mobiliti_layout()`. Añadir un test que falle si la generación productiva vuelve a usar las constantes heredadas `MOBILITI_SECTION_COUNT = 32` o `MAX_PROD_PER_SECTION = 64`.

**Step 2: Remove or isolate dead caps**

Si son inalcanzables, eliminarlas junto con sus helpers muertos. Si una ruta legacy sigue siendo necesaria, hacerla delegar al `MobilitiRowMap` dinámico y documentar el motivo.

**Step 3: Preserve the only real limits**

Mantener:

- `XLSX_MAX_ROWS = 1_048_576`;
- reserva estructural de filas;
- límite de bytes documentado para el payload/importación.

No introducir un nuevo límite comercial de secciones o productos.

### Task 7: Observabilidad, validación productiva y cierre

**Files:**
- Modify: `mobiliti_saas/api/index.py`
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: worker logging module used by production
- Update: Obsidian incident note

**Step 1: Add bounded stage metadata**

Registrar sin datos sensibles:

- `project_id`, revisión y hash;
- conteo de secciones, principales, complementos y líneas físicas;
- máximo de líneas por sección;
- etapa actual y duración;
- código estable para `project_section_mapping_invalid`.

**Step 2: Validate before expensive work**

El check de identidad debe ocurrir antes de:

- crear/subir el job en API;
- procesar imágenes;
- componer OOXML.

Conservar la validación del motor como defensa en profundidad.

**Step 3: Validate against a safe clone**

En producción, duplicar `PRUEBA` y no modificar el Proyecto original. Generar la cotización del clon y comprobar:

- 18 secciones con IDs no contiguos;
- 117 líneas físicas;
- descarga válida;
- fórmulas y totales completos;
- tiempo sin los 7m32s desperdiciados por una validación tardía.

**Step 4: Run the release gate**

Run:

```powershell
python -m pytest tests/test_project_quote_api.py tests/test_mixed_catalog_cart.py tests/test_mobiliti_layout.py tests/test_mobiliti_capacity.py tests/test_official_quote_stress.py tests/test_project_quote_acceptance.py -q
```

Expected: PASS.

**Step 5: Document the result**

Actualizar la nota de Obsidian con:

- causa raíz;
- evidencia del job;
- pruebas ejecutadas;
- cambios desplegados;
- hash/versión de producción;
- resultado de la cotización clonada;
- riesgos residuales de tiempo y tamaño de imágenes.

## Criterios de aceptación

- `300-00066` ya no puede fallar por renumeración de IDs de sección.
- Una incoherencia de sección se rechaza antes de crear el job.
- El mismo contenido con 18 secciones/117 líneas genera un XLSX válido.
- Las matrices 20×40, 10×100 y 700/35 no truncan filas ni fórmulas.
- Las secciones extra desplazan correctamente resumen, fechas, tablas inferiores y pie.
- No aparecen nuevos límites 16, 32, 33 o 64 como reglas de negocio.
- Los dos entrypoints API y los dos quote engines mantienen contratos equivalentes.
