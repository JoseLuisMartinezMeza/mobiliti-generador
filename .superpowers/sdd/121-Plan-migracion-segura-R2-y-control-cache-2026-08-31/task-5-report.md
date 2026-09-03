# Task 5 — Compatibilidad de proyectos históricos y trabajos en cola

Fecha: 2026-08-31
Base: `b4141ffada1ba22315e8f171f1316d31e411ccbc`
Implementación: `56e8ca402099c7ce1b8c0ef2fff7ebe63583ffaf`

## Resultado

- Los proyectos históricos se presentan desde una copia profunda con la URL pública del provider activo, sin modificar el JSONB persistido ni snapshots.
- La conversión es bidireccional e idempotente entre la URL pública exacta de Supabase `catalog-assets/<sha256>.<ext>` y la base pública R2 configurada.
- Sólo se convierten líneas `source=catalog` de catálogos genéricos. Se excluyen Offiho, Tarkett, imports, `/dev`, static, rutas firmadas, hosts externos, query/fragment (incluso delimitadores vacíos), prefijos extra y nombres que no sean hash SHA-256 con extensión permitida.
- `GET /projects/{id}` devuelve `Cache-Control: private, no-store` y firma imágenes importadas únicamente en la copia de respuesta.
- El PATCH compara por `line_id` y exige coincidencia exacta de `source`, `catalog`, la identidad estructurada existente y `object_name`. Si sólo cambió Supabase↔R2, restaura la URL persistida; si el PATCH completo queda idéntico, no llama `db_save_project` ni cambia revisión/hash. Si hay otro cambio legítimo, lo guarda con la URL persistida restaurada. Una identidad u object hash distinto sí se guarda.
- Una cotización nueva recarga el catálogo activo y congela su URL R2 en `groups[].items[]`; `project_context.normalized_project_payload` conserva el payload histórico y su URL persistida.
- No fue necesario modificar frontend ni `tests/test_project_model_ui.py`; su suite completa se ejecutó para confirmar compatibilidad.
- Las tres copias desplegables de la API quedaron byte-identical.

## TDD RED → GREEN

Primer RED:

```text
python -B -m pytest -p no:cacheprovider tests/test_project_api.py tests/test_project_quote_api.py -q
5 failed, 37 passed
```

Cuatro fallos eran los contratos nuevos esperados: header no-store, Supabase→R2, R2→Supabase y anti-churn PATCH. El quinto reveló una llamada de fixture anterior a Task 1 que omitía el `supplier` ahora obligatorio; `git blame` la ubicó en el contrato previo. Se corrigió sólo esa llamada de prueba a `supplier=sunon`, sin cambiar producción.

Segundo RED de borde:

```text
python -B -m pytest -p no:cacheprovider tests/test_project_api.py::test_project_visible_catalog_image_rejects_noncanonical_or_excluded_urls -q
2 failed, 11 passed
```

Los casos `url?` y `url#` demostraron que `urlparse` normaliza delimitadores vacíos. La solución mínima los rechaza antes del parsing.

GREEN focal:

```text
python -B -m pytest -p no:cacheprovider tests/test_project_api.py tests/test_project_quote_api.py -q
43 passed

python -B -m pytest -p no:cacheprovider tests/test_project_api.py tests/test_project_model_ui.py tests/test_project_quote_api.py tests/test_quote_template_selection.py -q
111 passed
```

GREEN relevante final, después del último cambio:

```text
python -B -m pytest -p no:cacheprovider tests/test_catalog_repository.py tests/test_catalog_sync_service.py tests/test_quote_jobs_api.py tests/test_supplier_catalog.py tests/test_project_api.py tests/test_project_catalog_search.py tests/test_catalog_migrations.py tests/test_project_model_ui.py tests/test_project_quote_api.py tests/test_quote_template_selection.py -q
934 passed, 2 skipped
```

La compilación sintáctica de las tres APIs terminó con exit code 0:

```text
python -B -m py_compile mobiliti_saas/api/index.py mobiliti_saas/web/api/index.py vercel_deploy/api/index.py
```

## Regresión amplia y hallazgo fuera de alcance

La ejecución de todos los `test_project*.py`, `test_quote_jobs_api.py`, `test_mixed_catalog_cart.py` y `test_mixed_catalog_workbook.py` produjo `800 passed, 5 failed`. Los cinco fallos están en `tests/test_project_quote_acceptance.py` y son ajenos a Task 5: las expectativas fijan columnas `W/X` y fórmulas históricas de `Fletes`, mientras el template actual genera columna `AA` y fórmulas diferentes. Task 5 no modifica el template, `quote_engine` ni el worker. No se ensanchó el alcance para alterar esas pruebas o el workbook.

## Corrección de re-review — 2026-09-01

El revisor detectó que un PATCH completamente idéntico todavía llamaba a `db_save_project` cuando no había ocurrido una reescritura de provider. Se añadió primero una prueba que convierte cualquier llamada a `db_save_project` en fallo. El RED confirmó el defecto:

```text
python -B -m pytest -p no:cacheprovider tests/test_project_api.py::test_project_patch_identical_payload_does_not_write_or_change_revision tests/test_project_api.py::test_project_visible_catalog_image_rejects_noncanonical_or_excluded_urls -q
1 failed, 20 passed
```

La solución mínima retorna el proyecto persistido después de normalizar, aplicar el guard de URL y validar ownership siempre que `name` y `payload` sean idénticos, independientemente de si hubo reescritura de provider. No se llama `db_save_project` y se conservan revisión, `updated_at` y hash. El booleano `restored` dejó de aportar comportamiento y se eliminó; el helper ahora devuelve únicamente la copia protegida.

También se ampliaron los negativos de URL con puerto, userinfo, host con case no canónico, segmentos percent-encoded, objeto percent-encoded, slash codificado y dot-segment. No se añadió una expectativa incorrecta sobre el case del scheme.

GREEN específico del re-review:

```text
python -B -m pytest -p no:cacheprovider tests/test_project_api.py::test_project_patch_identical_payload_does_not_write_or_change_revision tests/test_project_api.py::test_project_patch_provider_only_image_change_preserves_jsonb_revision_and_hash tests/test_project_api.py::test_project_patch_other_change_saves_but_restores_persisted_catalog_url tests/test_project_api.py::test_project_patch_real_catalog_image_change_is_persisted tests/test_project_api.py::test_project_visible_catalog_image_rejects_noncanonical_or_excluded_urls -q
25 passed
```

Focal actual completo:

```text
python -B -m pytest -p no:cacheprovider tests/test_project_api.py tests/test_project_quote_api.py -q
53 passed
```

Verificación relevante post-review, incluyendo UI sin cambios y las dos pruebas de paridad API:

```text
python -B -m pytest -p no:cacheprovider tests/test_project_api.py tests/test_project_model_ui.py tests/test_project_quote_api.py tests/test_quote_template_selection.py tests/test_quote_jobs_api.py::test_deployable_api_copies_have_identical_sha256 -q
122 passed
```

`py_compile` de las tres APIs y `git diff --check` finalizaron sin errores. La corrección está incluida en el commit adicional informado en el handoff.

## Gate de drenaje y rollback

- Los estados reales que deben considerarse son `draft`, `queued` y `processing`; no se usa un estado `running`.
- Antes de retirar cualquiera de los dos hosts de la allowlist, deben drenar todos los trabajos pre-corte en esos estados, incluidos reintentos `processing` obsoletos. La allowlist dual Supabase+R2 permanece durante transición y rollback.
- No se implementó retiro de host, mutación de colas ni cambio operativo.
- El rollback ordinario cambia provider/base y redespliega, conservando Supabase. Si R2 recibió hashes nuevos durante el corte, primero se congelan escrituras y se copian/verifican esos hashes en Supabase; no se borra ni sobrescribe ningún objeto.

## Alcance y riesgos pendientes

- No hubo deploy, push, DDL, actualización masiva, acceso a secretos ni mutación live.
- Gate 5 de código queda cubierto; el corte operativo sigue condicionado al dominio R2 exacto, allowlist dual desplegada, drenaje de colas y gates externos 6–8.
- Las URLs históricas arbitrarias permanecen sin transformación deliberadamente; sólo el contrato content-addressed exacto es reversible de forma segura.
