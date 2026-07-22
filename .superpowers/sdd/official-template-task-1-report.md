# Reporte — Tarea 1: Promote and Verify the Official Template

Fecha: 2026-07-21
Estado: DONE

## Implementación

- Se agregó `mobiliti_saas/quote_engine/official_template.py` con el contrato
  inmutable (`TemplateContract`), la inspección OOXML (`TemplateInspection`),
  carga de contrato, verificación de compatibilidad y promoción fail-closed.
- La inspección resuelve cada hoja mediante la relación actual de
  `xl/workbook.xml` y `xl/_rels/workbook.xml.rels`; no presupone nombres como
  `sheetN.xml`. Verifica SHA-256, estados de hojas, nombres definidos, partes
  de vínculos externos y fórmulas de las hojas `SPEC`.
- Se creó el contrato JSON oficial y se promovió la copia auditada de SharePoint
  como `Formato Cotizacion 2026 Oficial.xlsx`. SHA-256 confirmado:
  `e8bd97286aaa8af5dcf6d08b715231b9edcbe28b84da3db2523dfbb43f2c3989`.
- Se añadió el comando `scripts/promote_official_quote_template.py`.
- El worker y la imagen Docker usan el nombre exacto de la copia promovida. Si
  falta, `_default_template()` lanza `FileNotFoundError`; ya no busca copias
  históricas en silencio.

## Evidencia TDD

### RED

1. Tras crear `tests/test_official_template_contract.py`, se ejecutó:

   ```powershell
   python -m pytest tests/test_official_template_contract.py -v
   ```

   Resultado esperado: fallo de colección con
   `ModuleNotFoundError: mobiliti_saas.quote_engine.official_template`.

2. Tras promover la plantilla y antes de cambiar el selector del worker, se
   ejecutó:

   ```powershell
   python -m pytest tests/test_official_template_contract.py::test_worker_default_template_is_the_promoted_official_copy -v
   ```

   Resultado esperado: fallo porque el worker resolvía
   `Formato Cotización 2026 GDL (1).xlsx` en vez de la copia oficial promovida.

### GREEN

1. Promoción auditada:

   ```powershell
   python scripts/promote_official_quote_template.py --source "C:\Users\pepem\AppData\Local\Temp\mobiliti-template-audit-20260721\official-template-sharepoint.xlsx" --destination "mobiliti_saas\worker\templates\Formato Cotizacion 2026 Oficial.xlsx" --contract "mobiliti_saas\worker\templates\formato-cotizacion-2026-oficial.contract.json"
   ```

   Resultado: `Plantilla oficial promovida:` seguido del SHA-256 oficial.

2. Verificación dirigida:

   ```powershell
   python -m pytest tests/test_official_template_contract.py tests/test_quote_worker.py::test_default_template_resolves_existing_template -v
   ```

   Resultado: 4 passed.

3. Suite proporcional:

   ```powershell
   python -m pytest tests/test_official_template_contract.py tests/test_quote_worker.py tests/test_mobiliti_sharepoint_contract.py -v
   ```

   Resultado: 85 passed en 99.67 s. Hubo 6 advertencias conocidas de `openpyxl`
   sobre validación de datos y formato WMF; no hubo fallos.

4. Validación de diff propio:

   ```powershell
   git diff --cached --check
   ```

   Resultado: sin problemas en los archivos de esta tarea.

## Archivos de implementación

- `mobiliti_saas/quote_engine/official_template.py`
- `mobiliti_saas/worker/templates/formato-cotizacion-2026-oficial.contract.json`
- `mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx`
- `scripts/promote_official_quote_template.py`
- `tests/test_official_template_contract.py`
- `mobiliti_saas/worker/Dockerfile`
- `mobiliti_saas/worker/quote_worker.py` (solo el hunk de `_default_template()`)

## Commit

- `8db28d8 feat: promote official quote template`

## Self-review y preocupaciones

- La verificación compara cinco propiedades independientes y arroja un error
  con todos los campos incompatibles antes de permitir usar una copia alterada.
- El contrato conserva los límites de mutación requeridos para tareas futuras;
  esta tarea no muta el OOXML.
- Se preservaron todos los cambios no relacionados. En particular, los hunks
  preexistentes de `mobiliti_saas/worker/quote_worker.py` y
  `tests/test_quote_worker.py` permanecieron fuera del commit.
- La plantilla histórica permanece físicamente en el árbol por la política de
  no eliminación, pero el worker ya no la selecciona. No hay preocupaciones
  funcionales pendientes para esta tarea.
