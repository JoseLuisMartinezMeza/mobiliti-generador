# Quotation Shadow Cleanup Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminar sombras de piso y fondos de imágenes importadas desde `Quotation` sin
alterar la hoja fuente ni perder estructuras finas del producto.

**Architecture:** El procesador actual conserva su comportamiento por defecto. Una opción
explícita `remove_shadow` habilita segmentación local para imágenes importadas; el motor la
activa según el origen de cada línea. La segmentación es perezosa y cuenta con controles de
calidad y fallback determinista.

**Tech Stack:** Python, Pillow, rembg, onnxruntime, pytest.

---

### Task 1: Especificar el contrato con pruebas en rojo

**Files:**
- Modify: `tests/test_quote_engine_image_processing.py`
- Modify: `tests/test_quote_engine_golden.py`

**Step 1: Write the failing tests**

- Agregar una prueba que simule una máscara sin sombra y compruebe que la salida deja
  blanco/transparente el piso oscuro conservando el objeto.
- Agregar una prueba de rescate de una pata clara y delgada sin rescatar una sombra ancha.
- Agregar una prueba que compruebe que `remove_shadow=False` no invoca el segmentador.
- Agregar una prueba del motor que compruebe `remove_shadow=True` sólo para origen
  `imported`.

**Step 2: Run test to verify it fails**

Run:
`python -m pytest tests/test_quote_engine_image_processing.py tests/test_quote_engine_golden.py -q`

Expected: FAIL por ausencia del nuevo contrato o del segmentador local.

### Task 2: Implementar segmentación local protegida

**Files:**
- Modify: `mobiliti_saas/quote_engine/image_processing.py`
- Modify: `mobiliti_saas/worker/requirements.txt`

**Step 1: Write minimal implementation**

- Añadir carga perezosa y cacheada del modelo `silueta`.
- Preservar imágenes con alfa útil.
- Implementar recuperación limitada de bordes conectados.
- Implementar limpieza de halo, recorte y validación de máscara.
- Añadir fallback al flujo actual ante importación, inferencia o máscara inválida.
- Declarar versiones compatibles de `rembg` y `onnxruntime` en el worker.

**Step 2: Run focused tests**

Run:
`python -m pytest tests/test_quote_engine_image_processing.py -q`

Expected: PASS.

### Task 3: Activar sólo para imágenes importadas

**Files:**
- Modify: `mobiliti_saas/quote_engine/engine.py`

**Step 1: Pass origin-aware flag**

- En `_improve_official_cotizacion_images`, pasar `remove_shadow=True` únicamente cuando
  `line.origin == "imported"`.

**Step 2: Run engine tests**

Run:
`python -m pytest tests/test_quote_engine_golden.py -q`

Expected: PASS.

### Task 4: Validación visual y regresión

**Files:**
- No production file changes expected.

**Step 1: Process real examples**

- Procesar las tres imágenes suministradas y guardar artefactos diagnósticos temporales.
- Confirmar visualmente que no hay sombra oscura y que patas/ruedas/cables permanecen.

**Step 2: Run regression suite**

Run:
`python -m pytest tests/test_quote_engine_image_processing.py tests/test_quote_engine_golden.py tests/test_quote_engine_images.py -q`

Expected: PASS.
