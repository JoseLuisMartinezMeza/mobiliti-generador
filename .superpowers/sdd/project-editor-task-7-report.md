# Task 7 Report: Project Editor E2E y responsive

Fecha: 2026-07-23

## Entregado

- El flujo de Proyecto persistente quedó cubierto de extremo a extremo:
  creación, dos ocurrencias independientes de un mismo producto, complemento
  con cantidad fija, autosave, recarga, reapertura y generación.
- Los flujos heredados de cuatro catálogos, doble submit, cantidad inválida,
  importación pequeña, importación de 700 líneas y edición de borradores
  importados se adaptaron al contrato actual de Proyecto.
- El panel rápido usa los roles actuales: diálogo `Proyecto activo`, botón
  `Editar Proyecto`, contador `Proyecto (n)` y estados accesibles
  `Cambios pendientes`, `Guardando` y `Guardado`.
- En 390 x 844 el editor ocupa el viewport completo, conserva scroll interno,
  no provoca overflow horizontal y presenta las acciones de línea en una sola
  columna.
- El stub de navegador cubre promoción durable de imports y bloquea toda red
  externa o ruta API no declarada. La promoción devuelve manifest canónico,
  asset de fuente e imágenes por fila con las llaves durables esperadas.
- No se publicó ni desplegó ningún artefacto remoto.

## Correcciones encontradas por los E2E

- `projectQuoteFieldsFromMixedQuote` conserva únicamente los ocho campos
  persistentes; valores transitorios como `template` ya no invalidan autosave.
- La cotización mixta proyecta complementos a la sección de su principal antes
  de crear el snapshot.
- Los productos importados reciben posiciones consecutivas 0..n-1 por sección.
- Los nuevos principales de catálogo reciben la siguiente posición de su
  sección aunque el objeto entrante ya estuviera normalizado con posición 0.
  Complementos y reemplazos conservan sus rutas de posicionamiento propias.
- `serializeProject` permite `official_code` vacío sólo para importados, en
  concordancia con el modelo Python y con el borrador editable; catálogo sigue
  fallando con `Codigo oficial requerido`.

## Evidencia TDD

- RED inicial combinado: `75 passed, 8 failed`; los fallos eran E2E heredados
  que todavía dependían del carrito anterior y no creaban Proyecto activo.
- RED de carga masiva: el POST de promoción terminó en 24.6 ms, no hubo PATCH,
  `console_errors=[]` y `page_errors=['Posicion de linea duplicada']`.
  El gate aislado pasó después de asignar posiciones por sección.
- RED de importación persistente: el fixture real trae códigos oficiales
  vacíos y `serializeProject` lanzó `Codigo oficial requerido`. El nuevo unit
  reprodujo el fallo y quedó verde permitiendo vacío sólo en importados.
- RED de múltiples catálogos: dos principales normalizados en la misma sección
  producían `[0, 0]`; el nuevo unit exige `[0, 1]`.
- La sincronización de cada alta de catálogo espera la respuesta PATCH de esa
  mutación y después el estado final `Guardado`, por lo que no acepta un estado
  `saved` obsoleto.

## Validación final

```powershell
python -m pytest tests/test_project_ui.py tests/test_project_model_ui.py -q
```

Resultado: `75 passed in 20.78s`.

```powershell
python -m pytest tests/test_mixed_catalog_browser_e2e.py -q -k "not 700"
```

Resultado: `9 passed, 1 deselected in 73.29s`.

```powershell
python -m pytest tests/test_mixed_catalog_browser_e2e.py::test_browser_submits_700_lines_once_from_compact_collapsed_cart -vv -s
```

Resultado: `1 passed in 99.16s`.

```powershell
python -m pytest tests/test_mixed_catalog_browser_e2e.py tests/test_project_ui.py tests/test_project_model_ui.py -q
```

Resultado final combinado: `85 passed in 97.42s`.

```powershell
npm.cmd --prefix mobiliti_saas/web run build
```

Resultado: Vite transformó 1712 módulos y terminó en 6.41 s.

El teardown final confirmó `FIXTURE_VITE_COUNT=0` y
`FIXTURE_CHROME_COUNT=0`. Los servidores Vite existentes en el puerto 5173 y
los procesos Playwright MCP eran sesiones ajenas al fixture y se preservaron.

## Commits

- Implementación inicial: `7b977df test: cover persistent project editing workflow`.
- Las correcciones derivadas de la aceptación se entregan en un commit
  separado junto con este reporte.

## Archivos de la corrección

- `mobiliti_saas/web/src/mixedCart.js`
- `tests/test_mixed_catalog_browser_e2e.py`
- `tests/test_project_model_ui.py`
- `.superpowers/sdd/project-editor-task-7-report.md`

Se preservaron sin staging los cambios y artefactos ajenos ya presentes en el
worktree.
