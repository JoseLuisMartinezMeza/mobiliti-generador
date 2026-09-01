# Task 1 — Caché de búsqueda por versión publicada

Fecha: 2026-08-31

## Resultado

Se eliminó la búsqueda global implícita: el selector no solicita datos hasta
que exista un proveedor válido y el backend rechaza `supplier` ausente antes
de cargar cualquier payload. La caché de catálogos de proveedores consulta
primero únicamente `published_version_id`; con la misma versión reutiliza el
catálogo hidratado y con una versión distinta vuelve a leer exactamente un
payload. La ruta autenticada `/catalogs/search` responde con
`Cache-Control: private, no-store`.

## Archivos modificados

- `mobiliti_saas/web/api/index.py`
- `mobiliti_saas/api/index.py`
- `vercel_deploy/api/index.py`
- `mobiliti_saas/quote_engine/catalog_search.py`
- `mobiliti_saas/web/src/ProductPickerDialog.jsx`
- `mobiliti_saas/web/src/productPicker.js`
- `tests/test_project_ui.py`
- `tests/test_project_catalog_search.py`
- `tests/test_quote_jobs_api.py`

## Decisiones

- Los tres entrypoints API son byte-a-byte idénticos. SHA-256 verificado:
  `D19B4DC4CEA1065CB2DE9851DF53145FA498323203417EAD15C314D9F9C7D69B`.
- `db_get_published_catalog_version_id()` hace la consulta pequeña a
  `saas_catalog_sources`; el payload sólo se consulta al no haber entrada de
  caché para esa versión.
- La caché compartida conserva exclusivamente el catálogo publicado e
  hidratado, nunca reservas ni otros datos dependientes del usuario.
- `search_catalog_products()` exige proveedor y mantiene los filtros y la
  paginación como transformación posterior al catálogo compartido.
- El selector muestra una selección explícita de proveedor y corta su efecto
  de búsqueda antes de invocar `request` cuando aún no se ha elegido uno.

## TDD

### RED

Se añadieron primero pruebas observables para: cero solicitud del selector
sin proveedor, rechazo del backend sin lectura de payload, hit de caché con
una sola lectura de payload, invalidación por nueva versión y encabezado de
caché autenticado.

Comandos ejecutados:

```powershell
python -m pytest tests/test_project_ui.py::test_product_picker_does_not_create_a_search_request_without_supplier -q
python -m pytest tests/test_project_catalog_search.py::test_search_requires_an_explicit_supplier -q
python -m pytest tests/test_quote_jobs_api.py::test_catalog_search_rejects_missing_supplier_before_reading_payload tests/test_quote_jobs_api.py::test_supplier_catalog_cache_reuses_payload_when_published_version_is_unchanged tests/test_quote_jobs_api.py::test_supplier_catalog_cache_reads_one_new_payload_after_published_version_changes tests/test_quote_jobs_api.py::test_authenticated_catalog_search_disables_shared_http_caching -q
```

Salida observada: 1 fallo de UI (`initial` aún era una URL), 1 fallo del
buscador (no se elevó `ValueError`) y 4 fallos API (se leyó el payload,
hubo dos lecturas de payload y faltó `Cache-Control`).

### GREEN y regresión

```powershell
python -m pytest tests/test_project_catalog_search.py tests/test_project_ui.py -q
python -m pytest tests/test_quote_jobs_api.py -q
python -m py_compile mobiliti_saas/web/api/index.py mobiliti_saas/api/index.py vercel_deploy/api/index.py mobiliti_saas/quote_engine/catalog_search.py
Get-FileHash mobiliti_saas/web/api/index.py,mobiliti_saas/api/index.py,vercel_deploy/api/index.py -Algorithm SHA256
git diff --check -- mobiliti_saas/web/api/index.py mobiliti_saas/api/index.py vercel_deploy/api/index.py mobiliti_saas/web/src/ProductPickerDialog.jsx mobiliti_saas/web/src/productPicker.js mobiliti_saas/quote_engine/catalog_search.py tests/test_project_ui.py tests/test_project_catalog_search.py tests/test_quote_jobs_api.py
```

Resultados:

- `70 passed` para buscador/UI.
- `298 passed` para API.
- `py_compile` terminó con código 0.
- Los tres SHA-256 son idénticos.
- `git diff --check` no informó errores en los archivos de la tarea.

## Self-review

- El rechazo por proveedor faltante sucede al validar parámetros, antes de
  `_require_active_subscription` y de `_catalog_search_snapshots`; por tanto
  no abre ni lee payloads.
- Los hits comparan la metadata `published_version_id` antes de la función
  que lee payload; las pruebas verifican una sola lectura para una versión y
  una lectura adicional al cambiarla.
- La respuesta de búsqueda autenticada se marca privada y sin almacenamiento
  compartido.
- No se realizaron DDL, despliegues, llamadas externas de escritura, push ni
  eliminaciones. Se preservaron cambios ajenos ya presentes en el worktree.
