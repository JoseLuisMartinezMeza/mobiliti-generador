# Quotation Light Texture Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar fondo y sombra de imágenes importadas sin borrar superficies o
texturas claras del producto.

**Architecture:** El flujo `remove_shadow` utilizará una máscara alfa suave y omitirá la
segunda limpieza destructiva por inundación. Un control geométrico rechazará máscaras
suaves de cuadro completo y conservará el original sobre blanco como fallback.

**Tech Stack:** Python 3, Pillow, rembg, pytest.

## Global Constraints

- Modificar sólo el procesamiento de imágenes y sus pruebas focalizadas.
- No agregar dependencias ni servicios.
- La salida seguirá siendo PNG opaco sobre blanco.
- No crear commits automáticos en este worktree compartido y con cambios previos.

---

### Task 1: Fijar las regresiones con pruebas en rojo

**Files:**
- Modify: `tests/test_quote_engine_image_processing.py`

**Interfaces:**
- Consumes: `improve_product_image_bytes(content, content_type, *, background, min_size, cleanup_strength, remove_shadow) -> tuple[bytes, str]`
- Produces: pruebas públicas para preservación clara, segmentación de alfa existente y fallback seguro.

- [ ] **Step 1: Escribir la prueba de preservación de una superficie clara**

Crear una imagen sintética con fondo blanco y una cubierta clara. Sustituir
`_segment_product_locally` por una máscara suave que conserva la cubierta y comprobar
en el PNG final que el centro claro no se vuelve blanco.

- [ ] **Step 2: Escribir la prueba de segmentación aun con alfa de origen**

Crear una imagen RGBA con sombra semitransparente y comprobar que el segmentador se
invoca y la sombra no aparece en la salida.

- [ ] **Step 3: Escribir la prueba de máscara suave insegura**

Devolver una máscara con alfa parcial en todo el cuadro y comprobar que el fallback
conserva los píxeles originales sobre blanco en lugar de aplicar esa máscara.

- [ ] **Step 4: Ejecutar las tres pruebas y confirmar rojo**

Run:
`python -m pytest tests/test_quote_engine_image_processing.py -k "light_surface or source_alpha or unsafe_soft_mask" -q`

Expected: FAIL porque el flujo actual omite segmentación con alfa útil, vuelve a ejecutar
la inundación clara y acepta la máscara suave de cuadro completo.

### Task 2: Aplicar el cambio mínimo al procesador

**Files:**
- Modify: `mobiliti_saas/quote_engine/image_processing.py`

**Interfaces:**
- Consumes: las pruebas públicas de Task 1.
- Produces: `_valid_product_mask(alpha) -> bool` con control de suavidad/caja y
  `_segment_product_locally(source) -> Image.Image` con alfa suave.

- [ ] **Step 1: Usar máscara suave**

Cambiar la llamada a `rembg.remove` a `post_process_mask=False`.

- [ ] **Step 2: Segmentar todas las importadas**

En `_process_imported_image_without_shadow`, llamar siempre a
`_segment_product_locally(source)`; el alfa original no es autoridad porque puede
contener la sombra.

- [ ] **Step 3: Rechazar máscaras suaves de cuadro completo**

En `_valid_product_mask`, calcular la proporción de alfa parcial (`9..244`) y el área
relativa de la caja. Rechazar sólo cuando el área de caja sea al menos `0.98` y el alfa
parcial sea al menos `0.12`.

- [ ] **Step 4: Evitar la segunda inundación y preservar fallback**

Si falla `remove_shadow`, usar `image.convert("RGBA")`. Aplicar
`_remove_light_edge_background` únicamente cuando `remove_shadow=False`; después aplanar
normalmente sobre blanco.

- [ ] **Step 5: Ejecutar pruebas focalizadas**

Run:
`python -m pytest tests/test_quote_engine_image_processing.py -q`

Expected: PASS.

### Task 3: Validar con imágenes reales de output (18)

**Files:**
- No production file changes expected.

**Interfaces:**
- Consumes: imágenes extraídas de `output (18).xlsx`.
- Produces: comparación temporal y evidencia visual.

- [ ] **Step 1: Reprocesar todas las imágenes fuente representativas**

Aplicar el flujo corregido a las 55 imágenes de `Quotation` extraídas en el diagnóstico.

- [ ] **Step 2: Revisar silla clara, mesa clara y fotografía compleja**

Confirmar que `Quotation!C37` y `Quotation!C38` conservan asiento/cubierta y que
`Quotation!C22` usa el fallback conservador.

- [ ] **Step 3: Ejecutar regresión**

Run:
`python -m pytest tests/test_quote_engine_image_processing.py tests/test_image_processing.py -q`

Expected: PASS.

- [ ] **Step 4: Registrar el resultado**

Actualizar `armado-caratula/43-Regresion-fondos-y-texturas-output-18.md` con cambios,
pruebas y rutas de comparación.
