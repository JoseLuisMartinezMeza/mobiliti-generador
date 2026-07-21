DONE

# Corrección de revisión — Tarea 1: manifiesto autoritativo de Quotation

Fecha: 2026-07-21

## RED observado

Antes de implementar la corrección se añadieron regresiones y se ejecutaron de forma focalizada. El resultado fue `2 failed, 1 passed`:

- Una fila cuya moneda explícita era `USD` aceptaba un payload con `source_currency=MXN`; no lanzaba error.
- Un ZIP con tamaño descomprimido declarado por encima del límite no tenía preflight; la prueba fallaba porque no existía `MAX_ZIP_MEMBER_UNCOMPRESSED`.

## Cambios realizados

- La moneda explícita del manifiesto ahora es autoritativa por fila. Un `source_currency` del payload diferente se rechaza; si la fila no tiene moneda, se conserva el fallback permitido del payload o selector global.
- Se incorporó un preflight común de XLSX antes de cualquier `load_workbook` o lectura de miembros ZIP. Limita bytes de entrada (25 MiB), número de entradas (5,000), tamaño por miembro (100 MiB), tamaño descomprimido acumulado (200 MiB) y ratio de compresión (200:1).
- El preflight rechaza ZIP inválidos/ZIP64 no permitido mediante errores de dominio, entradas duplicadas y rutas anómalas (absolutas, con `..`, separadores Windows, NUL o segmentos vacíos).
- La ruta de extracción de imágenes comparte el mismo preflight.
- Se mantuvieron byte-idénticos `mobiliti_saas/quote_engine/quotation_import.py` y su mirror web.

## Pruebas y validación

- GREEN focal: `3 passed` para sustitución de moneda, fallback sin moneda y expansión ZIP antes de OpenPyXL.
- GREEN completa: `python -m pytest tests/test_quotation_import.py -q` → `14 passed`.
- `python -m py_compile` pasó para ambos módulos espejo.
- La comparación binaria de los módulos espejo pasó.
- `git diff --check` de los archivos de esta tarea pasó. El chequeo global todavía informa problemas ya presentes en archivos no relacionados, que no se modificaron.

## Commit

`fix(import): harden quotation manifest parsing` (commit aislado de los archivos de Tarea 1).
